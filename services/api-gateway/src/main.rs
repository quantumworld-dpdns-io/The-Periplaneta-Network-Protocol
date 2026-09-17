//! api-gateway: axum REST + WebSocket fan-out between Redpanda and the dashboard.
//! Endpoints per `proto/WIRE.md` §9.

mod intervention;
mod wire;

use anyhow::{Context, Result};
use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        Path, State,
    },
    http::{HeaderValue, Method, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use bytes::Bytes;
use futures::{SinkExt, StreamExt};
use prometheus::{Encoder, IntCounter, IntGauge, Registry, TextEncoder};
use rdkafka::config::ClientConfig;
use rdkafka::consumer::{Consumer, StreamConsumer};
use rdkafka::producer::{FutureProducer, FutureRecord};
use rdkafka::Message as KafkaMessage;
use serde::Serialize;
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::sync::broadcast;
use tower_http::cors::{AllowOrigin, CorsLayer};
use tracing::{error, info, warn};
use wire::{is_hil, node_hex, NodeRecord};

const HIL_ONLINE_WINDOW: Duration = Duration::from_secs(5);
const GRID_HZ: u64 = 5;

#[derive(Debug, Clone)]
struct Cfg {
    api_port: u16,
    kafka_brokers: String,
    grid_w: u32,
    grid_h: u32,
    nodes: u32,
    hil_count: u32,
    cors_origins: Vec<String>,
}

fn env_or<T: std::str::FromStr>(k: &str, d: T) -> T {
    std::env::var(k).ok().and_then(|v| v.parse().ok()).unwrap_or(d)
}

impl Cfg {
    fn from_env() -> Self {
        let grid_w: u32 = env_or("GRID_W", 400);
        let grid_h: u32 = env_or("GRID_H", 250);
        Cfg {
            api_port: env_or("API_PORT", 8080),
            kafka_brokers: std::env::var("KAFKA_BROKERS").unwrap_or_else(|_| "localhost:19092".into()),
            grid_w,
            grid_h,
            nodes: env_or("SWARM_NODES", grid_w * grid_h),
            hil_count: env_or("HIL_NODES", 2),
            cors_origins: std::env::var("CORS_ORIGINS")
                .unwrap_or_else(|_| "http://localhost:3000,http://127.0.0.1:3000".into())
                .split(',')
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect(),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
struct HilStatus {
    node_id: u32,
    node_hex: String,
    online: bool,
    mode: u8,
    /// Fields below mirror the MQTT `status` payload (WIRE §8); only what the
    /// gateway can infer from `hil.raw` is filled in until a status relay exists.
    #[serde(skip_serializing_if = "Option::is_none")]
    uptime_s: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    rssi: Option<i8>,
    #[serde(skip_serializing_if = "Option::is_none")]
    fw: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    ip: Option<String>,
    last_seq: u32,
    last_seen_ms: u64,
    frames: u64,
    #[serde(skip)]
    last_seen: Option<Instant>,
}

struct Metrics {
    registry: Registry,
    feature_frames: IntCounter,
    alerts: IntCounter,
    hil_frames: IntCounter,
    interventions: IntCounter,
    grid_frames_sent: IntCounter,
    ws_clients: IntGauge,
    decode_errors: IntCounter,
}

impl Metrics {
    fn new() -> Self {
        let registry = Registry::new();
        let mk = |n: &str, h: &str| {
            let c = IntCounter::new(n, h).unwrap();
            registry.register(Box::new(c.clone())).unwrap();
            c
        };
        let feature_frames = mk("gateway_feature_frames_total", "FeatureFrames consumed");
        let alerts = mk("gateway_alerts_total", "alerts consumed");
        let hil_frames = mk("gateway_hil_frames_total", "HilRawFrames consumed");
        let interventions = mk("gateway_interventions_total", "interventions accepted");
        let grid_frames_sent = mk("gateway_grid_frames_sent_total", "GridFrames sent over /ws/grid");
        let decode_errors = mk("gateway_decode_errors_total", "undecodable Kafka payloads");
        let ws_clients = IntGauge::new("gateway_ws_clients", "open WebSocket connections").unwrap();
        registry.register(Box::new(ws_clients.clone())).unwrap();
        Metrics { registry, feature_frames, alerts, hil_frames, interventions, grid_frames_sent, ws_clients, decode_errors }
    }
}

struct AppState {
    cfg: Cfg,
    /// row-major stress grid, u8 0..255 (WIRE §4.4)
    grid: RwLock<Vec<u8>>,
    grid_t_ms: AtomicU64,
    /// latest NodeRecord + t_ms per virtual node (None = unknown)
    records: RwLock<Vec<Option<(u64, NodeRecord)>>>,
    hil_records: Mutex<HashMap<u32, (u64, NodeRecord)>>,
    hil_status: Mutex<HashMap<u32, HilStatus>>,
    alerts_tx: broadcast::Sender<(u32, Arc<str>)>,
    hil_raw_tx: broadcast::Sender<(u32, Bytes)>,
    producer: FutureProducer,
    kafka_ok: AtomicBool,
    metrics: Metrics,
}

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64
}

impl AppState {
    fn apply_feature_frame(&self, f: &wire::FeatureFrame) {
        let n = self.cfg.nodes;
        if f.first_node_id >= wire::HIL_NODE_BASE || f.records.len() == 1 && is_hil(f.first_node_id) {
            let mut h = self.hil_records.lock().unwrap();
            for (i, r) in f.records.iter().enumerate() {
                h.insert(f.node_id(i), (f.t_ms, *r));
            }
            return;
        }
        {
            let mut grid = self.grid.write().unwrap();
            let mut recs = self.records.write().unwrap();
            for (i, r) in f.records.iter().enumerate() {
                let id = f.node_id(i);
                if id < n {
                    let idx = id as usize;
                    if idx < grid.len() {
                        grid[idx] = wire::stress_to_u8(r.stress);
                    }
                    recs[idx] = Some((f.t_ms, *r));
                }
            }
        }
        self.grid_t_ms.fetch_max(f.t_ms, Ordering::Relaxed);
    }

    fn note_hil_frame(&self, h: &wire::HilRawHeader) {
        let mut m = self.hil_status.lock().unwrap();
        let e = m.entry(h.node_id).or_insert_with(|| empty_hil(h.node_id));
        e.mode = h.mode;
        e.last_seq = h.seq;
        e.last_seen = Some(Instant::now());
        e.last_seen_ms = now_ms();
        e.frames += 1;
        e.online = true;
    }

    fn hil_list(&self) -> Vec<HilStatus> {
        let mut m = self.hil_status.lock().unwrap();
        for i in 0..self.cfg.hil_count {
            m.entry(wire::HIL_NODE_BASE + i).or_insert_with(|| empty_hil(wire::HIL_NODE_BASE + i));
        }
        let mut v: Vec<HilStatus> = m
            .values_mut()
            .map(|s| {
                s.online = s.last_seen.map(|t| t.elapsed() < HIL_ONLINE_WINDOW).unwrap_or(false);
                s.clone()
            })
            .collect();
        v.sort_by_key(|s| s.node_id);
        v
    }

    fn node_record(&self, id: u32) -> Option<(u64, NodeRecord)> {
        if is_hil(id) {
            self.hil_records.lock().unwrap().get(&id).copied()
        } else {
            self.records.read().unwrap().get(id as usize).copied().flatten()
        }
    }
}

fn empty_hil(node_id: u32) -> HilStatus {
    HilStatus {
        node_id,
        node_hex: node_hex(node_id),
        online: false,
        mode: 0,
        uptime_s: None,
        rssi: None,
        fw: None,
        ip: None,
        last_seq: 0,
        last_seen_ms: 0,
        frames: 0,
        last_seen: None,
    }
}

// ---------------------------------------------------------------------------
// Kafka consumer
// ---------------------------------------------------------------------------

async fn consume(state: Arc<AppState>) -> Result<()> {
    let consumer: StreamConsumer = ClientConfig::new()
        .set("bootstrap.servers", &state.cfg.kafka_brokers)
        .set("group.id", "api-gateway")
        .set("enable.auto.commit", "true")
        .set("auto.offset.reset", "latest")
        .set("session.timeout.ms", "10000")
        .set("fetch.wait.max.ms", "50")
        .create()
        .context("consumer")?;
    consumer.subscribe(&["neuro.features", "neuro.alerts", "hil.raw"])?;
    info!("consuming neuro.features, neuro.alerts, hil.raw (group api-gateway)");
    loop {
        match consumer.recv().await {
            Ok(m) => {
                state.kafka_ok.store(true, Ordering::Relaxed);
                let Some(p) = m.payload() else { continue };
                match m.topic() {
                    "neuro.features" => match wire::decode_feature_frame(p) {
                        Ok(f) => {
                            state.apply_feature_frame(&f);
                            state.metrics.feature_frames.inc();
                        }
                        Err(e) => {
                            state.metrics.decode_errors.inc();
                            if state.metrics.decode_errors.get() % 100 == 1 {
                                warn!("FEAT decode: {e}");
                            }
                        }
                    },
                    "neuro.alerts" => {
                        state.metrics.alerts.inc();
                        match serde_json::from_slice::<serde_json::Value>(p) {
                            Ok(v) => {
                                if let Some(id) = v.get("node_id").and_then(|x| x.as_u64()) {
                                    let txt: Arc<str> = Arc::from(String::from_utf8_lossy(p).as_ref());
                                    let _ = state.alerts_tx.send((id as u32, txt));
                                }
                            }
                            Err(e) => {
                                state.metrics.decode_errors.inc();
                                warn!("alert JSON: {e}");
                            }
                        }
                    }
                    "hil.raw" => match wire::decode_hil_header(p) {
                        Ok(h) => {
                            state.metrics.hil_frames.inc();
                            state.note_hil_frame(&h);
                            let _ = state.hil_raw_tx.send((h.node_id, Bytes::copy_from_slice(p)));
                        }
                        Err(e) => {
                            state.metrics.decode_errors.inc();
                            if state.metrics.decode_errors.get() % 100 == 1 {
                                warn!("HILR decode: {e}");
                            }
                        }
                    },
                    _ => {}
                }
            }
            Err(e) => {
                warn!("consumer error: {e}");
                state.kafka_ok.store(false, Ordering::Relaxed);
                tokio::time::sleep(Duration::from_secs(1)).await;
            }
        }
    }
}

/// Periodic broker reachability check (drives `/health` `kafka` flag).
async fn kafka_health(state: Arc<AppState>) {
    loop {
        let p = state.producer.clone();
        let ok = tokio::task::spawn_blocking(move || {
            use rdkafka::producer::Producer;
            p.client().fetch_metadata(Some("interventions"), Duration::from_secs(3)).is_ok()
        })
        .await
        .unwrap_or(false);
        state.kafka_ok.store(ok, Ordering::Relaxed);
        tokio::time::sleep(Duration::from_secs(10)).await;
    }
}

// ---------------------------------------------------------------------------
// HTTP handlers
// ---------------------------------------------------------------------------

async fn health(State(s): State<Arc<AppState>>) -> Json<serde_json::Value> {
    Json(serde_json::json!({ "status": "ok", "kafka": s.kafka_ok.load(Ordering::Relaxed) }))
}

async fn nodes_hil(State(s): State<Arc<AppState>>) -> Json<Vec<HilStatus>> {
    Json(s.hil_list())
}

async fn metrics(State(s): State<Arc<AppState>>) -> Response {
    let mut buf = Vec::new();
    TextEncoder::new().encode(&s.metrics.registry.gather(), &mut buf).ok();
    ([(axum::http::header::CONTENT_TYPE, "text/plain; version=0.0.4")], buf).into_response()
}

async fn post_intervention(State(s): State<Arc<AppState>>, body: Result<Json<serde_json::Value>, axum::extract::rejection::JsonRejection>) -> Response {
    let Json(v) = match body {
        Ok(b) => b,
        Err(e) => return (StatusCode::BAD_REQUEST, Json(serde_json::json!({ "error": e.body_text() }))).into_response(),
    };
    let rec = match intervention::validate(v, &intervention::Limits { grid_w: s.cfg.grid_w, grid_h: s.cfg.grid_h, nodes: s.cfg.nodes }, now_ms()) {
        Ok(r) => r,
        Err(e) => return (StatusCode::BAD_REQUEST, Json(serde_json::json!({ "error": e }))).into_response(),
    };
    let payload = serde_json::to_vec(&rec).unwrap();
    match s.producer.send(FutureRecord::to("interventions").key(&rec.id).payload(&payload), Duration::from_secs(5)).await {
        Ok(_) => {
            s.metrics.interventions.inc();
            info!(id = %rec.id, kind = %rec.kind, "intervention accepted");
            (StatusCode::ACCEPTED, Json(rec)).into_response()
        }
        Err((e, _)) => {
            error!("intervention produce failed: {e}");
            s.kafka_ok.store(false, Ordering::Relaxed);
            (StatusCode::SERVICE_UNAVAILABLE, Json(serde_json::json!({ "error": format!("kafka: {e}") }))).into_response()
        }
    }
}

// ---------------------------------------------------------------------------
// WebSockets
// ---------------------------------------------------------------------------

async fn ws_grid(ws: WebSocketUpgrade, State(s): State<Arc<AppState>>) -> Response {
    ws.on_upgrade(move |sock| grid_session(sock, s))
}

fn hello_json(s: &AppState) -> String {
    let hil: Vec<serde_json::Value> = s
        .hil_list()
        .into_iter()
        .map(|h| serde_json::json!({ "node_id": h.node_id, "node_hex": h.node_hex, "online": h.online, "mode": h.mode }))
        .collect();
    serde_json::json!({ "type": "hello", "width": s.cfg.grid_w, "height": s.cfg.grid_h, "nodes": s.cfg.nodes, "hil": hil }).to_string()
}

async fn grid_session(sock: WebSocket, s: Arc<AppState>) {
    s.metrics.ws_clients.inc();
    let (mut tx, mut rx) = sock.split();
    if tx.send(Message::Text(hello_json(&s))).await.is_err() {
        s.metrics.ws_clients.dec();
        return;
    }
    let mut tick = tokio::time::interval(Duration::from_millis(1000 / GRID_HZ));
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            _ = tick.tick() => {
                let frame = {
                    let g = s.grid.read().unwrap();
                    let t = s.grid_t_ms.load(Ordering::Relaxed);
                    wire::encode_grid_frame(s.cfg.grid_w as u16, s.cfg.grid_h as u16, if t == 0 { now_ms() } else { t }, &g)
                };
                if tx.send(Message::Binary(frame)).await.is_err() { break; }
                s.metrics.grid_frames_sent.inc();
            }
            m = rx.next() => match m {
                Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break,
                Some(Ok(Message::Ping(p))) => { let _ = tx.send(Message::Pong(p)).await; }
                _ => {}
            }
        }
    }
    s.metrics.ws_clients.dec();
}

fn parse_node_id(s: &str) -> Option<u32> {
    if let Ok(v) = s.parse::<u32>() {
        return Some(v);
    }
    if s.len() == 8 {
        return u32::from_str_radix(s, 16).ok();
    }
    None
}

/// Missing FeatureFrames must not look like a live offline/zero reading.
fn features_payload(node_id: u32, rec: Option<(u64, NodeRecord)>) -> Option<String> {
    let (t_ms, rec) = rec?;
    Some(
        serde_json::json!({
            "type": "features",
            "node_id": node_id,
            "t_ms": t_ms,
            "stress": rec.stress,
            "state": rec.state,
            "features": rec.features
        })
        .to_string(),
    )
}

async fn ws_node(ws: WebSocketUpgrade, Path(id): Path<String>, State(s): State<Arc<AppState>>) -> Response {
    match parse_node_id(&id) {
        Some(node_id) if is_hil(node_id) || node_id < s.cfg.nodes => ws.on_upgrade(move |sock| node_session(sock, s, node_id)),
        _ => (StatusCode::NOT_FOUND, "unknown node id").into_response(),
    }
}

async fn node_session(sock: WebSocket, s: Arc<AppState>, node_id: u32) {
    s.metrics.ws_clients.inc();
    let (mut tx, mut rx) = sock.split();
    let mut alerts = s.alerts_tx.subscribe();
    let mut raw = s.hil_raw_tx.subscribe();
    let hil = is_hil(node_id);
    let mut tick = tokio::time::interval(Duration::from_secs(1));
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            _ = tick.tick() => {
                let Some(txt) = features_payload(node_id, s.node_record(node_id)) else { continue };
                if tx.send(Message::Text(txt)).await.is_err() { break; }
            }
            a = alerts.recv() => match a {
                Ok((id, json)) if id == node_id => {
                    // ensure the alert is tagged as type=alert without re-serialising heavy payloads
                    let txt = match serde_json::from_str::<serde_json::Value>(&json) {
                        Ok(serde_json::Value::Object(mut o)) => { o.insert("type".into(), "alert".into()); serde_json::Value::Object(o).to_string() }
                        _ => json.to_string(),
                    };
                    if tx.send(Message::Text(txt)).await.is_err() { break; }
                }
                Ok(_) => {}
                Err(broadcast::error::RecvError::Lagged(n)) => warn!(node_id, "alert stream lagged by {n}"),
                Err(_) => break,
            },
            r = raw.recv(), if hil => match r {
                Ok((id, bytes)) if id == node_id => {
                    if tx.send(Message::Binary(bytes.to_vec())).await.is_err() { break; }
                }
                Ok(_) => {}
                Err(broadcast::error::RecvError::Lagged(_)) => {}
                Err(_) => break,
            },
            m = rx.next() => match m {
                Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break,
                Some(Ok(Message::Ping(p))) => { let _ = tx.send(Message::Pong(p)).await; }
                _ => {}
            }
        }
    }
    s.metrics.ws_clients.dec();
}

// ---------------------------------------------------------------------------

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info,rdkafka=warn,tower_http=warn".into()))
        .init();
    let cfg = Cfg::from_env();
    info!(?cfg, "api-gateway starting");
    let producer: FutureProducer = ClientConfig::new()
        .set("bootstrap.servers", &cfg.kafka_brokers)
        .set("message.timeout.ms", "5000")
        .create()
        .context("producer")?;
    let (alerts_tx, _) = broadcast::channel(4096);
    let (hil_raw_tx, _) = broadcast::channel(256);
    let cells = cfg.grid_w as usize * cfg.grid_h as usize;
    let state = Arc::new(AppState {
        grid: RwLock::new(vec![0u8; cells]),
        grid_t_ms: AtomicU64::new(0),
        records: RwLock::new(vec![None; cfg.nodes as usize]),
        hil_records: Mutex::new(HashMap::new()),
        hil_status: Mutex::new(HashMap::new()),
        alerts_tx,
        hil_raw_tx,
        producer,
        kafka_ok: AtomicBool::new(false),
        metrics: Metrics::new(),
        cfg: cfg.clone(),
    });

    {
        let st = state.clone();
        tokio::spawn(async move {
            if let Err(e) = consume(st).await {
                error!("consumer task failed: {e}");
            }
        });
    }
    tokio::spawn(kafka_health(state.clone()));

    let origins: Vec<HeaderValue> = cfg.cors_origins.iter().filter_map(|o| o.parse().ok()).collect();
    let cors = CorsLayer::new()
        .allow_origin(AllowOrigin::list(origins))
        .allow_methods([Method::GET, Method::POST, Method::OPTIONS])
        .allow_headers(tower_http::cors::Any);

    let app = Router::new()
        .route("/health", get(health))
        .route("/nodes/hil", get(nodes_hil))
        .route("/interventions", post(post_intervention))
        .route("/metrics", get(metrics))
        .route("/ws/grid", get(ws_grid))
        .route("/ws/node/:id", get(ws_node))
        .layer(cors)
        .with_state(state);

    let listener = tokio::net::TcpListener::bind(("0.0.0.0", cfg.api_port)).await.with_context(|| format!("bind port {}", cfg.api_port))?;
    info!("listening on http://0.0.0.0:{}", cfg.api_port);
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
            info!("shutting down");
        })
        .await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_state() -> Arc<AppState> {
        let cfg = Cfg {
            api_port: 0,
            kafka_brokers: "localhost:1".into(),
            grid_w: 40,
            grid_h: 25,
            nodes: 1000,
            hil_count: 2,
            cors_origins: vec![],
        };
        let producer: FutureProducer = ClientConfig::new().set("bootstrap.servers", &cfg.kafka_brokers).create().unwrap();
        let (alerts_tx, _) = broadcast::channel(16);
        let (hil_raw_tx, _) = broadcast::channel(16);
        Arc::new(AppState {
            grid: RwLock::new(vec![0u8; 1000]),
            grid_t_ms: AtomicU64::new(0),
            records: RwLock::new(vec![None; 1000]),
            hil_records: Mutex::new(HashMap::new()),
            hil_status: Mutex::new(HashMap::new()),
            alerts_tx,
            hil_raw_tx,
            producer,
            kafka_ok: AtomicBool::new(false),
            metrics: Metrics::new(),
            cfg,
        })
    }

    #[test]
    fn feature_frame_updates_grid_and_records() {
        let s = test_state();
        let mut records = vec![NodeRecord { stress: 0.0, state: 0, features: [0.0; 8] }; 100];
        records[5] = NodeRecord { stress: 0.5, state: 1, features: [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0] };
        records[99] = NodeRecord { stress: 1.0, state: 5, features: [0.0; 8] };
        let f = wire::FeatureFrame { shard: 3, first_node_id: 300, t_ms: 777, window_ms: 1000, records };
        s.apply_feature_frame(&f);
        let g = s.grid.read().unwrap();
        assert_eq!(g[305], 128);
        assert_eq!(g[399], 255);
        assert_eq!(g[304], 0);
        drop(g);
        assert_eq!(s.grid_t_ms.load(Ordering::Relaxed), 777);
        let (t, r) = s.node_record(305).unwrap();
        assert_eq!(t, 777);
        assert_eq!(r.state, 1);
        assert_eq!(r.features[7], 8.0);
        assert!(s.node_record(0).is_none());
        // frames beyond configured nodes are ignored, not panicking
        let f2 = wire::FeatureFrame { shard: 99, first_node_id: 990, t_ms: 778, window_ms: 1000, records: vec![NodeRecord { stress: 0.2, state: 0, features: [0.0; 8] }; 20] };
        s.apply_feature_frame(&f2);
        assert_eq!(s.grid.read().unwrap()[999], 51);
        // GridFrame built from the grid has the right byte at (x=25,y=7) -> id 305
        let frame = wire::encode_grid_frame(40, 25, 1, &s.grid.read().unwrap());
        assert_eq!(frame[24 + 7 * 40 + 25], 128);
    }

    #[test]
    fn hil_feature_frame_and_status() {
        let s = test_state();
        let f = wire::FeatureFrame { shard: 0xFFFF, first_node_id: 0xFFFF0001, t_ms: 5, window_ms: 1000, records: vec![NodeRecord { stress: 0.9, state: 3, features: [0.0; 8] }] };
        s.apply_feature_frame(&f);
        assert_eq!(s.node_record(0xFFFF0001).unwrap().1.state, 3);
        // unseen HIL nodes are listed offline
        let l = s.hil_list();
        assert_eq!(l.len(), 2);
        assert!(l.iter().all(|h| !h.online));
        assert_eq!(l[0].node_hex, "ffff0000");
        s.note_hil_frame(&wire::HilRawHeader { mode: 1, node_id: 0xFFFF0000, seq: 9, ts_us: 0, sample_rate_hz: 10000, n_samples: 1000 });
        let l = s.hil_list();
        assert!(l[0].online && l[0].mode == 1 && l[0].last_seq == 9 && l[0].frames == 1);
        assert!(!l[1].online);
        let hello: serde_json::Value = serde_json::from_str(&hello_json(&s)).unwrap();
        assert_eq!(hello["type"], "hello");
        assert_eq!(hello["width"], 40);
        assert_eq!(hello["height"], 25);
        assert_eq!(hello["nodes"], 1000);
        assert_eq!(hello["hil"][0]["node_id"], 4294901760u64);
        assert_eq!(hello["hil"][0]["online"], true);
    }

    #[test]
    fn missing_node_record_does_not_emit_fake_features() {
        let s = test_state();
        assert!(features_payload(432, s.node_record(432)).is_none());
        s.apply_feature_frame(&wire::FeatureFrame {
            shard: 0,
            first_node_id: 400,
            t_ms: 9,
            window_ms: 1000,
            records: vec![NodeRecord { stress: 0.0, state: 0, features: [0.0; 8] }; 50],
        });
        let txt = features_payload(432, s.node_record(432)).expect("real record");
        let v: serde_json::Value = serde_json::from_str(&txt).unwrap();
        assert_eq!(v["type"], "features");
        assert_eq!(v["node_id"], 432);
        assert_eq!(v["t_ms"], 9);
        assert_eq!(v["state"], 0);
    }

    #[test]
    fn node_id_parsing() {
        assert_eq!(parse_node_id("80125"), Some(80125));
        assert_eq!(parse_node_id("ffff0000"), Some(0xFFFF0000));
        assert_eq!(parse_node_id("4294901760"), Some(0xFFFF0000));
        assert_eq!(parse_node_id("zzz"), None);
        assert_eq!(parse_node_id("-1"), None);
    }
}
