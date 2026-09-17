//! swarm-gen: 100k digital-twin spike generator -> Redpanda `swarm.bins`.
//!
//! One `SwarmBinFrame` per shard per bin (WIRE §4.2), interventions consumed
//! from `interventions` (group `swarm-gen`), ground truth JSON to `swarm.truth`.

mod twin;
mod wire;

use anyhow::{Context, Result};
use clap::Parser;
use prometheus::{Encoder, Histogram, HistogramOpts, IntCounter, IntGauge, Registry, TextEncoder};
use rdkafka::config::ClientConfig;
use rdkafka::consumer::{Consumer, StreamConsumer};
use rdkafka::producer::{FutureProducer, FutureRecord, Producer};
use rdkafka::Message;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tracing::{error, info, warn};
use twin::{Grid, Intervention, Swarm, TruthEvent, TwinParams};

#[derive(Parser, Debug, Clone)]
#[command(name = "swarm-gen", about = "Cockroach Internet digital-twin swarm generator")]
struct Cfg {
    /// Number of virtual nodes (grid = GRID_W x GRID_H must be >= this)
    #[arg(long, env = "SWARM_NODES", default_value_t = 100_000)]
    nodes: usize,
    /// Number of shards (one frame per shard per bin)
    #[arg(long, env = "SWARM_SHARDS", default_value_t = 100)]
    shards: usize,
    /// Bin length in ms
    #[arg(long, env = "BIN_MS", default_value_t = 100)]
    bin_ms: u32,
    /// RNG seed (all twins + dynamics are deterministic given seed + interventions)
    #[arg(long, env = "SEED", default_value_t = 42)]
    seed: u64,
    #[arg(long, env = "KAFKA_BROKERS", default_value = "localhost:19092")]
    kafka_brokers: String,
    #[arg(long, env = "GRID_W", default_value_t = 400)]
    grid_w: u32,
    #[arg(long, env = "GRID_H", default_value_t = 250)]
    grid_h: u32,
    #[arg(long, env = "METRICS_PORT", default_value_t = 9100)]
    metrics_port: u16,
    /// Path to twin_params.json fit by WS-C; falls back to defaults if missing
    #[arg(long, env = "TWIN_PARAMS", default_value = "../../ml/artifacts/twin_params.json")]
    twin_params: String,
    /// Simulation threads (0 = available_parallelism)
    #[arg(long, env = "SIM_THREADS", default_value_t = 0)]
    threads: usize,
    /// Wall-clock pacing factor: 1.0 = real time, 0 = as fast as possible
    #[arg(long, env = "SPEED", default_value_t = 1.0)]
    speed: f64,
    /// Stop after this many seconds of simulated time (0 = run forever)
    #[arg(long, env = "DURATION_S", default_value_t = 0)]
    duration_s: u64,
    /// librdkafka compression.type for swarm.bins
    #[arg(long, env = "KAFKA_COMPRESSION", default_value = "lz4")]
    compression: String,
    /// Simulate only; do not connect to Kafka
    #[arg(long, env = "DRY_RUN", default_value_t = false)]
    dry_run: bool,
}

struct Metrics {
    registry: Registry,
    frames_produced: IntCounter,
    produce_errors: IntCounter,
    produce_latency: Histogram,
    sim_step: Histogram,
    nodes_collapsed: IntGauge,
    sim_lag_ms: IntGauge,
    truth_events: IntCounter,
    interventions_applied: IntCounter,
}

impl Metrics {
    fn new() -> Self {
        let registry = Registry::new();
        let frames_produced = IntCounter::new("swarm_frames_produced_total", "SwarmBinFrames acked by Kafka").unwrap();
        let produce_errors = IntCounter::new("swarm_produce_errors_total", "Kafka produce errors").unwrap();
        let produce_latency = Histogram::with_opts(
            HistogramOpts::new("swarm_produce_latency_seconds", "bin dispatch -> all shard frames acked")
                .buckets(vec![0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5]),
        )
        .unwrap();
        let sim_step = Histogram::with_opts(
            HistogramOpts::new("swarm_sim_step_seconds", "wall time to simulate one bin for all shards")
                .buckets(vec![0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25]),
        )
        .unwrap();
        let nodes_collapsed = IntGauge::new("swarm_nodes_collapsed", "nodes with rate < 5% of baseline").unwrap();
        let sim_lag_ms = IntGauge::new("swarm_sim_lag_ms", "how far the simulation is behind wall clock").unwrap();
        let truth_events = IntCounter::new("swarm_truth_events_total", "ground-truth events published").unwrap();
        let interventions_applied = IntCounter::new("swarm_interventions_applied_total", "interventions consumed").unwrap();
        for c in [&frames_produced, &produce_errors, &truth_events, &interventions_applied] {
            registry.register(Box::new(c.clone())).unwrap();
        }
        registry.register(Box::new(produce_latency.clone())).unwrap();
        registry.register(Box::new(sim_step.clone())).unwrap();
        registry.register(Box::new(nodes_collapsed.clone())).unwrap();
        registry.register(Box::new(sim_lag_ms.clone())).unwrap();
        Metrics {
            registry,
            frames_produced,
            produce_errors,
            produce_latency,
            sim_step,
            nodes_collapsed,
            sim_lag_ms,
            truth_events,
            interventions_applied,
        }
    }

    fn render(&self) -> Vec<u8> {
        let mut buf = Vec::new();
        TextEncoder::new().encode(&self.registry.gather(), &mut buf).ok();
        buf
    }
}

/// One simulated bin ready for publishing.
struct BinOut {
    t_ms: u64,
    frames: Vec<(u16, Vec<u8>)>,
    truth: Vec<TruthEvent>,
    dispatched: Instant,
}

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64
}

/// Minimal Prometheus text endpoint (GET /metrics on METRICS_PORT).
async fn serve_metrics(port: u16, metrics: Arc<Metrics>) {
    let listener = match tokio::net::TcpListener::bind(("0.0.0.0", port)).await {
        Ok(l) => l,
        Err(e) => {
            warn!("metrics port {port} unavailable: {e}");
            return;
        }
    };
    info!("metrics on http://0.0.0.0:{port}/metrics");
    loop {
        let Ok((mut sock, _)) = listener.accept().await else { continue };
        let m = metrics.clone();
        tokio::spawn(async move {
            let mut buf = [0u8; 1024];
            let _ = tokio::time::timeout(Duration::from_secs(2), sock.read(&mut buf)).await;
            let body = m.render();
            let head = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/plain; version=0.0.4\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                body.len()
            );
            let _ = sock.write_all(head.as_bytes()).await;
            let _ = sock.write_all(&body).await;
            let _ = sock.shutdown().await;
        });
    }
}

/// Consume `interventions` and forward parsed records to the simulation thread.
async fn consume_interventions(brokers: String, tx: std::sync::mpsc::Sender<Intervention>, metrics: Arc<Metrics>) -> Result<()> {
    let consumer: StreamConsumer = ClientConfig::new()
        .set("bootstrap.servers", &brokers)
        .set("group.id", "swarm-gen")
        .set("enable.auto.commit", "true")
        .set("auto.offset.reset", "latest")
        .set("session.timeout.ms", "10000")
        .create()
        .context("intervention consumer")?;
    consumer.subscribe(&["interventions"])?;
    info!("consuming interventions (group swarm-gen)");
    loop {
        match consumer.recv().await {
            Ok(m) => {
                let Some(p) = m.payload() else { continue };
                match serde_json::from_slice::<Intervention>(p) {
                    Ok(iv) if iv.mode().is_some() => {
                        info!(id = %iv.id, kind = %iv.kind, target = ?iv.target, "intervention received");
                        metrics.interventions_applied.inc();
                        if tx.send(iv).is_err() {
                            return Ok(());
                        }
                    }
                    Ok(iv) => warn!(id = %iv.id, kind = %iv.kind, "unknown intervention kind ignored"),
                    Err(e) => warn!("bad intervention JSON: {e}"),
                }
            }
            Err(e) => {
                warn!("intervention consumer error: {e}");
                tokio::time::sleep(Duration::from_secs(1)).await;
            }
        }
    }
}

/// Simulation loop on a dedicated OS thread. Paces bins against wall clock.
fn run_sim(cfg: Cfg, params: TwinParams, iv_rx: std::sync::mpsc::Receiver<Intervention>, out: tokio::sync::mpsc::Sender<BinOut>, metrics: Arc<Metrics>, stop: Arc<AtomicBool>) {
    let grid = Grid { w: cfg.grid_w, h: cfg.grid_h };
    let bin = cfg.bin_ms as u64;
    let t0 = now_ms() / bin * bin;
    let t_build = Instant::now();
    let mut swarm = Swarm::new(cfg.nodes, cfg.shards, grid, cfg.seed, &params, t0);
    let threads = if cfg.threads == 0 { std::thread::available_parallelism().map(|n| n.get()).unwrap_or(4) } else { cfg.threads };
    info!(
        nodes = cfg.nodes,
        shards = cfg.shards,
        per_shard = swarm.nodes_per_shard,
        threads,
        seed = cfg.seed,
        "swarm built in {:?}",
        t_build.elapsed()
    );
    let mut counts: Vec<Vec<u16>> = vec![Vec::new(); cfg.shards];
    let mut truth: Vec<Vec<TruthEvent>> = vec![Vec::new(); cfg.shards];
    let start = Instant::now();
    let max_bins = if cfg.duration_s == 0 { u64::MAX } else { cfg.duration_s * 1000 / bin };
    let mut k: u64 = 0;
    let mut last_log = Instant::now();
    let mut bins_since_log = 0u64;
    while !stop.load(Ordering::Relaxed) && k < max_bins {
        let t_ms = t0 + k * bin;
        // pacing
        if cfg.speed > 0.0 {
            let due = start + Duration::from_secs_f64((k * bin) as f64 / 1000.0 / cfg.speed);
            let now = Instant::now();
            if due > now {
                std::thread::sleep(due - now);
                metrics.sim_lag_ms.set(0);
            } else {
                let lag = (now - due).as_millis() as i64;
                metrics.sim_lag_ms.set(lag);
                if lag > 2 * bin as i64 {
                    warn!(lag_ms = lag, "simulation behind wall clock");
                }
            }
        }
        // interventions
        let mut applied = Vec::new();
        while let Ok(mut iv) = iv_rx.try_recv() {
            if iv.t_ms < t_ms {
                iv.t_ms = t_ms; // late/replayed records start now
            }
            if let Some(ev) = swarm.apply(iv) {
                applied.push(ev);
            }
        }
        // step
        let s = Instant::now();
        swarm.step_all(t_ms, cfg.bin_ms, threads, &mut counts, &mut truth);
        metrics.sim_step.observe(s.elapsed().as_secs_f64());
        // encode
        let mut frames = Vec::with_capacity(cfg.shards);
        for (shard, c) in counts.iter().enumerate() {
            if c.is_empty() {
                continue;
            }
            let mut buf = Vec::new();
            wire::encode_swarm_bin(&mut buf, shard as u16, (shard * swarm.nodes_per_shard) as u32, cfg.bin_ms as u16, t_ms, c);
            frames.push((shard as u16, buf));
        }
        let mut all_truth = applied;
        for t in truth.iter_mut() {
            all_truth.append(t);
        }
        if k % 10 == 0 {
            metrics.nodes_collapsed.set(swarm.collapsed_count() as i64);
        }
        if out.blocking_send(BinOut { t_ms, frames, truth: all_truth, dispatched: Instant::now() }).is_err() {
            break;
        }
        k += 1;
        bins_since_log += 1;
        if last_log.elapsed() >= Duration::from_secs(5) {
            let el = last_log.elapsed().as_secs_f64();
            info!(
                bins_per_s = format!("{:.1}", bins_since_log as f64 / el),
                frames_per_s = format!("{:.0}", (bins_since_log * cfg.shards as u64) as f64 / el),
                frames_acked = metrics.frames_produced.get(),
                collapsed = metrics.nodes_collapsed.get(),
                lag_ms = metrics.sim_lag_ms.get(),
                "sim"
            );
            last_log = Instant::now();
            bins_since_log = 0;
        }
    }
    info!("simulation finished after {k} bins");
}

/// Publish frames + truth for each bin.
async fn run_producer(cfg: Cfg, mut rx: tokio::sync::mpsc::Receiver<BinOut>, metrics: Arc<Metrics>) -> Result<()> {
    let producer: Option<FutureProducer> = if cfg.dry_run {
        None
    } else {
        Some(
            ClientConfig::new()
                .set("bootstrap.servers", &cfg.kafka_brokers)
                .set("compression.type", &cfg.compression)
                .set("linger.ms", "5")
                .set("batch.num.messages", "1000")
                .set("queue.buffering.max.messages", "200000")
                .set("queue.buffering.max.kbytes", "524288")
                .set("message.timeout.ms", "30000")
                .set("acks", "1")
                .create()
                .context("producer")?,
        )
    };
    let mut log_at = Instant::now();
    let mut n_since = 0u64;
    while let Some(b) = rx.recv().await {
        let Some(p) = producer.as_ref() else {
            metrics.frames_produced.inc_by(b.frames.len() as u64);
            continue;
        };
        let keys: Vec<String> = b.frames.iter().map(|(s, _)| s.to_string()).collect();
        let frame_futs = b.frames.iter().zip(keys.iter()).map(|((_, buf), key)| {
            p.send(FutureRecord::to("swarm.bins").key(key).payload(buf).timestamp(b.t_ms as i64), Duration::from_secs(5))
        });
        // truth events go out concurrently with the frames (never one-by-one:
        // a toxin wave can emit thousands of symptom_onset events per bin)
        let truth_payloads: Vec<(&str, Vec<u8>)> =
            b.truth.iter().map(|ev| (ev.intervention_id.as_str(), serde_json::to_vec(ev).expect("truth json"))).collect();
        let truth_futs = truth_payloads
            .iter()
            .map(|(k, payload)| p.send(FutureRecord::to("swarm.truth").key(*k).payload(payload).timestamp(b.t_ms as i64), Duration::from_secs(5)));
        let (frame_results, truth_results) = tokio::join!(futures::future::join_all(frame_futs), futures::future::join_all(truth_futs));
        let mut ok = 0u64;
        for r in frame_results {
            match r {
                Ok(_) => ok += 1,
                Err((e, _)) => {
                    metrics.produce_errors.inc();
                    if metrics.produce_errors.get() % 100 == 1 {
                        error!("produce error: {e}");
                    }
                }
            }
        }
        metrics.frames_produced.inc_by(ok);
        metrics.produce_latency.observe(b.dispatched.elapsed().as_secs_f64());
        n_since += ok;
        for r in truth_results {
            match r {
                Ok(_) => metrics.truth_events.inc(),
                Err((e, _)) => warn!("truth produce error: {e}"),
            }
        }
        if log_at.elapsed() >= Duration::from_secs(5) {
            info!(frames_per_s = format!("{:.0}", n_since as f64 / log_at.elapsed().as_secs_f64()), total = metrics.frames_produced.get(), errors = metrics.produce_errors.get(), "kafka");
            log_at = Instant::now();
            n_since = 0;
        }
    }
    if let Some(p) = producer {
        info!("flushing producer");
        let _ = p.flush(Duration::from_secs(10));
    }
    Ok(())
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info,rdkafka=warn".into()))
        .init();
    let cfg = Cfg::parse();
    anyhow::ensure!(cfg.shards >= 1 && cfg.shards <= 0xFFFE, "SWARM_SHARDS out of range");
    anyhow::ensure!(cfg.bin_ms >= 10 && cfg.bin_ms <= 60_000, "BIN_MS out of range");
    if (cfg.grid_w as usize) * (cfg.grid_h as usize) < cfg.nodes {
        warn!("GRID_W*GRID_H < SWARM_NODES; nodes beyond the grid get y >= GRID_H");
    }
    let params = {
        let mut found = None;
        for cand in [cfg.twin_params.clone(), "ml/artifacts/twin_params.json".into()] {
            let p = std::path::Path::new(&cand);
            if let Some(tp) = TwinParams::load(p) {
                info!(path = %cand, source = ?tp.source, units = tp.units.len(), "loaded twin params");
                found = Some(tp);
                break;
            }
        }
        found.unwrap_or_else(|| {
            info!("twin_params.json not found; using built-in defaults (shape U(1.5,3), rate U(5,40) Hz, refractory 2 ms)");
            TwinParams::defaults()
        })
    };
    let metrics = Arc::new(Metrics::new());
    let stop = Arc::new(AtomicBool::new(false));

    tokio::spawn(serve_metrics(cfg.metrics_port, metrics.clone()));

    let (iv_tx, iv_rx) = std::sync::mpsc::channel::<Intervention>();
    if !cfg.dry_run {
        let brokers = cfg.kafka_brokers.clone();
        let m = metrics.clone();
        tokio::spawn(async move {
            if let Err(e) = consume_interventions(brokers, iv_tx, m).await {
                error!("intervention consumer failed: {e}");
            }
        });
    }

    let (out_tx, out_rx) = tokio::sync::mpsc::channel::<BinOut>(8);
    let sim_cfg = cfg.clone();
    let sim_metrics = metrics.clone();
    let sim_stop = stop.clone();
    let sim = std::thread::Builder::new()
        .name("sim".into())
        .spawn(move || run_sim(sim_cfg, params, iv_rx, out_tx, sim_metrics, sim_stop))
        .expect("spawn sim thread");

    let producer = tokio::spawn(run_producer(cfg.clone(), out_rx, metrics.clone()));

    let stop_sig = stop.clone();
    tokio::spawn(async move {
        let _ = tokio::signal::ctrl_c().await;
        info!("ctrl-c: stopping");
        stop_sig.store(true, Ordering::Relaxed);
    });

    let res = producer.await?;
    stop.store(true, Ordering::Relaxed);
    let _ = tokio::task::spawn_blocking(move || sim.join()).await;
    info!(frames = metrics.frames_produced.get(), errors = metrics.produce_errors.get(), truth = metrics.truth_events.get(), "done");
    res
}
