//! mqtt-bridge: Mosquitto <-> Redpanda bridge for the HIL (ESP32-S3) nodes.
//!
//! * `hil/+/raw`    (MQTT)  -> validate HilRawFrame -> Kafka `hil.raw` keyed by node_hex
//! * `hil/+/status` (MQTT)  -> kept in memory, served at `GET /hil`
//! * Kafka `interventions` (group `mqtt-bridge`) -> `hil/{node_hex}/cmd` for HIL/all targets
//! * `GET /metrics` Prometheus text (frames_in, frames_out, bad_frames, ...)
//!
//! Env: MQTT_URL (mqtt://mosquitto:1883), KAFKA_BROKERS (redpanda:9092), METRICS_PORT (9102).

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::{anyhow, Context, Result};
use axum::{extract::State, response::IntoResponse, routing::get, Json, Router};
use prometheus::{Encoder, IntCounter, IntGauge, Registry, TextEncoder};
use rdkafka::consumer::{Consumer, StreamConsumer};
use rdkafka::producer::{FutureProducer, FutureRecord};
use rdkafka::util::Timeout;
use rdkafka::{ClientConfig, Message};
use rumqttc::{AsyncClient, Event, MqttOptions, Packet, QoS};
use serde_json::{json, Value};
use tokio::sync::RwLock;
use tracing::{debug, error, info, warn};

use mqtt_bridge::frame::{node_hex, node_hex_from_topic, parse_header};
use mqtt_bridge::intervention::{hil_targets, to_cmd, Intervention};

const RAW_TOPIC: &str = "hil.raw";
const INTERVENTIONS_TOPIC: &str = "interventions";
const CONSUMER_GROUP: &str = "mqtt-bridge";
/// A node is `online` if a status was seen within this window (status period is 2 s).
const ONLINE_WINDOW: Duration = Duration::from_secs(10);

struct Metrics {
    registry: Registry,
    frames_in: IntCounter,
    frames_out: IntCounter,
    bad_frames: IntCounter,
    kafka_errors: IntCounter,
    status_in: IntCounter,
    cmds_out: IntCounter,
    hil_online: IntGauge,
}

impl Metrics {
    fn new() -> Result<Self> {
        let registry = Registry::new();
        let mk = |name: &str, help: &str| -> Result<IntCounter> {
            let c = IntCounter::new(name, help)?;
            registry.register(Box::new(c.clone()))?;
            Ok(c)
        };
        let frames_in = mk("mqtt_bridge_frames_in", "HilRawFrames received on hil/+/raw")?;
        let frames_out = mk("mqtt_bridge_frames_out", "HilRawFrames produced to Kafka hil.raw")?;
        let bad_frames = mk("mqtt_bridge_bad_frames", "Frames rejected (bad magic/version/length)")?;
        let kafka_errors = mk("mqtt_bridge_kafka_errors", "Kafka produce/consume errors")?;
        let status_in = mk("mqtt_bridge_status_in", "Status messages received on hil/+/status")?;
        let cmds_out = mk("mqtt_bridge_cmds_out", "Commands published to hil/{node}/cmd")?;
        let hil_online = IntGauge::new("mqtt_bridge_hil_online", "HIL nodes with a recent status")?;
        registry.register(Box::new(hil_online.clone()))?;
        Ok(Metrics {
            registry,
            frames_in,
            frames_out,
            bad_frames,
            kafka_errors,
            status_in,
            cmds_out,
            hil_online,
        })
    }
}

#[derive(Clone)]
struct StatusEntry {
    payload: Value,
    seen: Instant,
}

struct AppState {
    metrics: Metrics,
    status: RwLock<HashMap<String, StatusEntry>>, // node_hex -> last status
}

type Shared = Arc<AppState>;

/// `mqtt://host:port` (scheme optional) -> (host, port)
fn parse_mqtt_url(url: &str) -> Result<(String, u16)> {
    let rest = url
        .strip_prefix("mqtt://")
        .or_else(|| url.strip_prefix("tcp://"))
        .unwrap_or(url);
    let rest = rest.trim_end_matches('/');
    let (host, port) = match rest.rsplit_once(':') {
        Some((h, p)) => (h.to_string(), p.parse::<u16>().context("bad MQTT port")?),
        None => (rest.to_string(), 1883),
    };
    if host.is_empty() {
        return Err(anyhow!("empty MQTT host in {url}"));
    }
    Ok((host, port))
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info,rumqttc=warn,rdkafka=warn".into()),
        )
        .init();

    let mqtt_url = std::env::var("MQTT_URL").unwrap_or_else(|_| "mqtt://mosquitto:1883".into());
    let brokers = std::env::var("KAFKA_BROKERS").unwrap_or_else(|_| "redpanda:9092".into());
    let metrics_port: u16 = std::env::var("METRICS_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(9102);
    let (mqtt_host, mqtt_port) = parse_mqtt_url(&mqtt_url)?;
    info!(%mqtt_host, mqtt_port, %brokers, metrics_port, "mqtt-bridge starting");

    let state: Shared = Arc::new(AppState {
        metrics: Metrics::new()?,
        status: RwLock::new(HashMap::new()),
    });

    // ---- Kafka -----------------------------------------------------------
    let producer: FutureProducer = ClientConfig::new()
        .set("bootstrap.servers", &brokers)
        .set("message.timeout.ms", "5000")
        .set("queue.buffering.max.ms", "5") // batch a few frames, keep latency low
        .set("compression.type", "none")
        .create()
        .context("create Kafka producer")?;

    let consumer: StreamConsumer = ClientConfig::new()
        .set("bootstrap.servers", &brokers)
        .set("group.id", CONSUMER_GROUP)
        .set("enable.auto.commit", "true")
        .set("auto.offset.reset", "latest")
        .set("session.timeout.ms", "10000")
        .create()
        .context("create Kafka consumer")?;
    consumer
        .subscribe(&[INTERVENTIONS_TOPIC])
        .context("subscribe interventions")?;

    // ---- MQTT ------------------------------------------------------------
    let client_id = format!("mqtt-bridge-{}", std::process::id());
    let mut opts = MqttOptions::new(client_id, mqtt_host, mqtt_port);
    opts.set_keep_alive(Duration::from_secs(15));
    opts.set_max_packet_size(65536, 65536);
    opts.set_clean_session(true);
    let (mqtt, eventloop) = AsyncClient::new(opts, 256);

    // ---- tasks -----------------------------------------------------------
    let http = tokio::spawn(serve_http(state.clone(), metrics_port));
    let mqtt_task = tokio::spawn(run_mqtt(state.clone(), mqtt.clone(), eventloop, producer));
    let kafka_task = tokio::spawn(run_interventions(state.clone(), mqtt.clone(), consumer));
    let online_task = tokio::spawn(online_gauge(state.clone()));

    tokio::select! {
        r = http => error!("http task exited: {r:?}"),
        r = mqtt_task => error!("mqtt task exited: {r:?}"),
        r = kafka_task => error!("kafka task exited: {r:?}"),
        r = online_task => error!("online task exited: {r:?}"),
        _ = tokio::signal::ctrl_c() => info!("ctrl-c, shutting down"),
    }
    let _ = mqtt.disconnect().await;
    Ok(())
}

/// MQTT event loop: subscribe on every (re)connect, route publishes.
async fn run_mqtt(
    state: Shared,
    client: AsyncClient,
    mut eventloop: rumqttc::EventLoop,
    producer: FutureProducer,
) -> Result<()> {
    loop {
        match eventloop.poll().await {
            Ok(Event::Incoming(Packet::ConnAck(_))) => {
                info!("MQTT connected; subscribing hil/+/raw, hil/+/status");
                client.subscribe("hil/+/raw", QoS::AtMostOnce).await?;
                client.subscribe("hil/+/status", QoS::AtLeastOnce).await?;
            }
            Ok(Event::Incoming(Packet::Publish(p))) => {
                let Some(hex) = node_hex_from_topic(&p.topic) else {
                    debug!(topic = %p.topic, "ignoring unexpected topic");
                    continue;
                };
                if p.topic.ends_with("/raw") {
                    handle_raw(&state, &producer, hex, &p.payload).await;
                } else if p.topic.ends_with("/status") {
                    handle_status(&state, hex, &p.payload).await;
                }
            }
            Ok(_) => {}
            Err(e) => {
                warn!("MQTT connection error: {e}; retrying in 2 s");
                tokio::time::sleep(Duration::from_secs(2)).await;
            }
        }
    }
}

async fn handle_raw(state: &Shared, producer: &FutureProducer, topic_hex: &str, payload: &[u8]) {
    let m = &state.metrics;
    m.frames_in.inc();
    let header = match parse_header(payload) {
        Ok(h) => h,
        Err(e) => {
            m.bad_frames.inc();
            debug!(node = topic_hex, "bad frame: {e}");
            return;
        }
    };
    // Key by the node_id inside the frame (authoritative); warn if the topic disagrees.
    let key = node_hex(header.node_id);
    if key != topic_hex {
        warn!(topic = topic_hex, frame = %key, "topic/frame node id mismatch");
    }
    let record = FutureRecord::to(RAW_TOPIC).key(&key).payload(payload);
    match producer.send(record, Timeout::After(Duration::from_secs(2))).await {
        Ok(_) => m.frames_out.inc(),
        Err((e, _)) => {
            m.kafka_errors.inc();
            warn!("kafka produce failed: {e}");
        }
    }
}

async fn handle_status(state: &Shared, hex: &str, payload: &[u8]) {
    state.metrics.status_in.inc();
    match serde_json::from_slice::<Value>(payload) {
        Ok(v) => {
            state.status.write().await.insert(
                hex.to_string(),
                StatusEntry { payload: v, seen: Instant::now() },
            );
        }
        Err(e) => debug!(node = hex, "bad status JSON: {e}"),
    }
}

/// Kafka `interventions` -> `hil/{node_hex}/cmd`.
async fn run_interventions(state: Shared, client: AsyncClient, consumer: StreamConsumer) -> Result<()> {
    info!("consuming Kafka topic {INTERVENTIONS_TOPIC} (group {CONSUMER_GROUP})");
    loop {
        let msg = match consumer.recv().await {
            Ok(m) => m,
            Err(e) => {
                state.metrics.kafka_errors.inc();
                warn!("kafka consume error: {e}");
                tokio::time::sleep(Duration::from_millis(500)).await;
                continue;
            }
        };
        let Some(payload) = msg.payload() else { continue };
        let iv: Intervention = match serde_json::from_slice(payload) {
            Ok(iv) => iv,
            Err(e) => {
                warn!("bad intervention JSON: {e}");
                continue;
            }
        };
        let Some(cmd) = to_cmd(&iv) else {
            warn!(kind = %iv.kind, "unknown intervention kind; ignored");
            continue;
        };
        let known: Vec<u32> = state
            .status
            .read()
            .await
            .values()
            .filter_map(|s| s.payload.get("node_id").and_then(Value::as_u64))
            .map(|n| n as u32)
            .collect();
        let targets = hil_targets(&iv, &known);
        if targets.is_empty() {
            debug!(id = %iv.id, "intervention does not target HIL nodes");
            continue;
        }
        let body = serde_json::to_vec(&cmd)?;
        for node_id in targets {
            let topic = format!("hil/{}/cmd", node_hex(node_id));
            match client.publish(&topic, QoS::AtLeastOnce, false, body.clone()).await {
                Ok(()) => {
                    state.metrics.cmds_out.inc();
                    info!(id = %iv.id, %topic, mode = cmd.mode, intensity = %cmd.intensity, "forwarded cmd");
                }
                Err(e) => warn!(%topic, "mqtt publish failed: {e}"),
            }
        }
    }
}

async fn online_gauge(state: Shared) -> Result<()> {
    loop {
        tokio::time::sleep(Duration::from_secs(2)).await;
        let n = state
            .status
            .read()
            .await
            .values()
            .filter(|s| s.seen.elapsed() < ONLINE_WINDOW)
            .count();
        state.metrics.hil_online.set(n as i64);
    }
}

// ---- HTTP -------------------------------------------------------------------

async fn serve_http(state: Shared, port: u16) -> Result<()> {
    let app = Router::new()
        .route("/metrics", get(metrics_handler))
        .route("/hil", get(hil_handler))
        .route("/health", get(|| async { "ok" }))
        .with_state(state);
    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    let listener = tokio::net::TcpListener::bind(addr).await?;
    info!(%addr, "http listening (/metrics, /hil, /health)");
    axum::serve(listener, app).await?;
    Ok(())
}

async fn metrics_handler(State(state): State<Shared>) -> impl IntoResponse {
    let mut buf = Vec::new();
    let enc = TextEncoder::new();
    if let Err(e) = enc.encode(&state.metrics.registry.gather(), &mut buf) {
        error!("metrics encode: {e}");
    }
    ([("content-type", "text/plain; version=0.0.4")], buf)
}

/// Last status per node (WIRE section 8 payload) + `online` + `age_s` + `node_hex`.
async fn hil_handler(State(state): State<Shared>) -> impl IntoResponse {
    let map = state.status.read().await;
    let mut out: Vec<Value> = map
        .iter()
        .map(|(hex, s)| {
            let mut v = s.payload.clone();
            if let Value::Object(o) = &mut v {
                let age = s.seen.elapsed();
                o.insert("node_hex".into(), json!(hex));
                o.insert("online".into(), json!(age < ONLINE_WINDOW));
                o.insert("age_s".into(), json!(age.as_secs_f32()));
            }
            v
        })
        .collect();
    out.sort_by(|a, b| a["node_hex"].as_str().cmp(&b["node_hex"].as_str()));
    Json(out)
}
