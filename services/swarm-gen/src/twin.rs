//! Digital-twin population model.
//!
//! Each virtual node is a gamma-renewal inter-spike-interval (ISI) process:
//! `ISI = refractory + Gamma(shape k, scale θ)` with `k·θ = 1000/rate − refractory`.
//! Per-node parameters are sampled (seeded) from distributions loaded from
//! `ml/artifacts/twin_params.json` (fit by WS-C) or built-in defaults.
//!
//! Interventions (WIRE §5) arrive as radial waves from a target origin; each
//! node switches mode when the front reaches it and then follows a mode
//! specific curve for its rate multiplier / ISI shape:
//!   toxin     : rate decays over `onset_ms` toward `(1-intensity)^2`, CV rises
//!               (shape shrinks), then continues collapsing exponentially.
//!   drug      : recovery toward baseline, `intensity` = fraction recovered.
//!   radiation : acute burst (×(1+3·intensity)) then noisy suppression.
//!   baseline  : reset to intrinsic parameters.
//! Ground truth (WIRE §6): `symptom_onset` when the node's rate is < 5 % of
//! baseline for ≥ 2 s; `recovered` when a symptomatic node climbs back above
//! 50 % of baseline.

use rand::{Rng, SeedableRng};
use rand_chacha::ChaCha8Rng;
use rand_distr::{Distribution, Gamma};
use serde::{Deserialize, Serialize};

pub const MODE_BASELINE: u8 = 0;
pub const MODE_TOXIN: u8 = 1;
pub const MODE_DRUG: u8 = 2;
pub const MODE_RADIATION: u8 = 3;

/// Rate multiplier below which a node counts as symptomatic (WIRE §6).
pub const SYMPTOM_FRACTION: f32 = 0.05;
/// Sustained duration required for `symptom_onset`.
pub const SYMPTOM_HOLD_MS: u32 = 2000;
/// Recovery threshold for the `recovered` truth event.
pub const RECOVER_FRACTION: f32 = 0.5;
const MIN_MULT: f32 = 1e-3;
const MIN_SHAPE: f32 = 0.3;

// ---------------------------------------------------------------------------
// Parameter distributions
// ---------------------------------------------------------------------------

/// A closed-form marginal distribution for one parameter.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "dist", rename_all = "lowercase")]
pub enum ParamDist {
    Uniform { min: f32, max: f32 },
    LogNormal { mu: f32, sigma: f32 },
    Normal { mean: f32, sd: f32 },
    Const { value: f32 },
}

impl ParamDist {
    fn sample<R: Rng>(&self, rng: &mut R) -> f32 {
        match *self {
            ParamDist::Uniform { min, max } => rng.gen_range(min..=max.max(min)),
            ParamDist::LogNormal { mu, sigma } => {
                let n: f32 = rand_distr::StandardNormal.sample(rng);
                (mu + sigma * n).exp()
            }
            ParamDist::Normal { mean, sd } => {
                let n: f32 = rand_distr::StandardNormal.sample(rng);
                mean + sd * n
            }
            ParamDist::Const { value } => value,
        }
    }
}

/// One fitted unit (from real spike trains). Sampled with replacement if present.
///
/// Field semantics follow `ml/src/CockroachML.jl`: `rate_hz` is the **gamma
/// rate parameter** (1/θ) of the ISI distribution, *not* the firing rate. The
/// firing rate is `mean_rate_hz` = 1 / (refractory_s + shape / rate_hz); older
/// files without it get the derived value via [`UnitParams::firing_rate_hz`].
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UnitParams {
    pub shape: f32,
    pub rate_hz: f32,
    #[serde(default = "default_refractory")]
    pub refractory_ms: f32,
    #[serde(default)]
    pub mean_rate_hz: Option<f32>,
}

impl UnitParams {
    /// Spikes per second implied by the fitted triple (or the explicit field).
    pub fn firing_rate_hz(&self) -> f32 {
        match self.mean_rate_hz {
            Some(r) if r.is_finite() && r > 0.0 => r,
            _ => 1.0 / (self.refractory_ms / 1000.0 + self.shape / self.rate_hz),
        }
    }
}

fn default_refractory() -> f32 {
    2.0
}

/// Contents of `ml/artifacts/twin_params.json`. Either `units` (empirical
/// per-unit fits) or marginal distributions; missing fields use defaults.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct TwinParams {
    #[serde(default)]
    pub units: Vec<UnitParams>,
    #[serde(default)]
    pub shape: Option<ParamDist>,
    #[serde(default)]
    pub rate_hz: Option<ParamDist>,
    #[serde(default)]
    pub refractory_ms: Option<ParamDist>,
    #[serde(default)]
    pub source: Option<String>,
}

impl TwinParams {
    pub fn defaults() -> Self {
        TwinParams {
            units: vec![],
            shape: Some(ParamDist::Uniform { min: 1.5, max: 3.0 }),
            rate_hz: Some(ParamDist::Uniform { min: 5.0, max: 40.0 }),
            refractory_ms: Some(ParamDist::Const { value: 2.0 }),
            source: Some("builtin-defaults".into()),
        }
    }

    /// Load from JSON file; returns `None` on any error (caller logs + falls back).
    pub fn load(path: &std::path::Path) -> Option<Self> {
        let s = std::fs::read_to_string(path).ok()?;
        serde_json::from_str::<TwinParams>(&s).ok()
    }

    fn sample_node<R: Rng>(&self, rng: &mut R) -> (f32, f32, f32) {
        let d = TwinParams::defaults();
        let (shape, rate, refr) = if !self.units.is_empty() {
            let u = &self.units[rng.gen_range(0..self.units.len())];
            (u.shape, u.firing_rate_hz(), u.refractory_ms)
        } else {
            (
                self.shape.as_ref().or(d.shape.as_ref()).unwrap().sample(rng),
                self.rate_hz.as_ref().or(d.rate_hz.as_ref()).unwrap().sample(rng),
                self.refractory_ms.as_ref().or(d.refractory_ms.as_ref()).unwrap().sample(rng),
            )
        };
        (shape.clamp(MIN_SHAPE, 50.0), rate.clamp(0.5, 400.0), refr.clamp(0.0, 20.0))
    }
}

// ---------------------------------------------------------------------------
// Interventions & truth
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum Target {
    Region { x: f32, y: f32, r: f32 },
    Node { node_id: u32 },
    All,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct InterventionParams {
    #[serde(default = "one")]
    pub intensity: f32,
    #[serde(default = "default_spread")]
    pub spread_ms: f32,
    #[serde(default = "default_onset")]
    pub onset_ms: f32,
}
impl Default for InterventionParams {
    fn default() -> Self {
        InterventionParams { intensity: one(), spread_ms: default_spread(), onset_ms: default_onset() }
    }
}
fn one() -> f32 {
    1.0
}
fn default_spread() -> f32 {
    20_000.0
}
fn default_onset() -> f32 {
    5_000.0
}

/// WIRE §5 intervention record.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Intervention {
    pub id: String,
    pub t_ms: u64,
    pub kind: String,
    pub target: Target,
    #[serde(default)]
    pub params: InterventionParams,
    #[serde(default)]
    pub source: Option<String>,
    /// Dashboard agent label; ignored by the twin physics (kind is authoritative).
    #[serde(default)]
    pub subtype: Option<String>,
}

impl Intervention {
    pub fn mode(&self) -> Option<u8> {
        match self.kind.as_str() {
            "baseline" => Some(MODE_BASELINE),
            "toxin" => Some(MODE_TOXIN),
            "drug" => Some(MODE_DRUG),
            "radiation" => Some(MODE_RADIATION),
            _ => None,
        }
    }
}

/// WIRE §6 ground-truth event.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TruthEvent {
    pub t_ms: u64,
    pub event: &'static str,
    pub intervention_id: String,
    pub node_id: u32,
    pub kind: String,
}

// ---------------------------------------------------------------------------
// Node state
// ---------------------------------------------------------------------------

const FLAG_SYMPTOM: u8 = 1;
const FLAG_RECOVERED: u8 = 2;

#[derive(Debug, Clone)]
pub struct Node {
    // intrinsic
    base_rate: f32,
    base_shape: f32,
    refractory: f32,
    // state at last mode switch
    mult0: f32,
    shape0: f32,
    t_switch: u64,
    onset_ms: f32,
    intensity: f32,
    mode: u8,
    flags: u8,
    iv: u32, // index into Swarm::interventions (u32::MAX = none)
    // effective
    mult: f32,
    shape: f32,
    gamma: Gamma<f32>,
    next_spike_ms: f64,
    low_ms: u32,
    // pending wave arrival
    pending_arrive: u64,
    pending_iv: u32,
}

#[cfg_attr(not(test), allow(dead_code))]
impl Node {
    fn isi_mean_ms(rate: f32, mult: f32, refractory: f32) -> f32 {
        (1000.0 / (rate * mult.max(MIN_MULT)) - refractory).max(0.1)
    }

    fn rebuild_gamma(&mut self) {
        let mean = Self::isi_mean_ms(self.base_rate, self.mult, self.refractory);
        let shape = self.shape.max(MIN_SHAPE);
        self.gamma = Gamma::new(shape, mean / shape).expect("valid gamma");
    }

    pub fn mode(&self) -> u8 {
        self.mode
    }
    pub fn rate_mult(&self) -> f32 {
        self.mult
    }
    pub fn base_rate_hz(&self) -> f32 {
        self.base_rate
    }
    pub fn is_collapsed(&self) -> bool {
        self.mult < SYMPTOM_FRACTION
    }
}

/// Geometry of the grid (WIRE §1).
#[derive(Debug, Clone, Copy)]
pub struct Grid {
    pub w: u32,
    pub h: u32,
}

#[cfg_attr(not(test), allow(dead_code))]
impl Grid {
    pub fn xy(&self, node_id: u32) -> (f32, f32) {
        ((node_id % self.w) as f32, (node_id / self.w) as f32)
    }
    pub fn id(&self, x: f32, y: f32) -> u32 {
        let xi = (x.round().max(0.0) as u32).min(self.w - 1);
        let yi = (y.round().max(0.0) as u32).min(self.h - 1);
        yi * self.w + xi
    }
}

/// The whole population. Nodes are `0..n`; shard = id / nodes_per_shard.
pub struct Swarm {
    pub grid: Grid,
    pub nodes: Vec<Node>,
    pub nodes_per_shard: usize,
    pub interventions: Vec<Intervention>,
    shard_rngs: Vec<ChaCha8Rng>,
}

#[cfg_attr(not(test), allow(dead_code))]
impl Swarm {
    pub fn new(n_nodes: usize, n_shards: usize, grid: Grid, seed: u64, params: &TwinParams, t0_ms: u64) -> Self {
        let nodes_per_shard = n_nodes.div_ceil(n_shards.max(1));
        let mut init_rng = ChaCha8Rng::seed_from_u64(seed);
        let mut nodes = Vec::with_capacity(n_nodes);
        for _ in 0..n_nodes {
            let (shape, rate, refr) = params.sample_node(&mut init_rng);
            let mean = Node::isi_mean_ms(rate, 1.0, refr);
            let phase: f32 = init_rng.gen::<f32>() * (mean + refr);
            let mut node = Node {
                base_rate: rate,
                base_shape: shape,
                refractory: refr,
                mult0: 1.0,
                shape0: shape,
                t_switch: t0_ms,
                onset_ms: 1.0,
                intensity: 0.0,
                mode: MODE_BASELINE,
                flags: 0,
                iv: u32::MAX,
                mult: 1.0,
                shape,
                gamma: Gamma::new(1.0, 1.0).unwrap(),
                next_spike_ms: t0_ms as f64 + phase as f64,
                low_ms: 0,
                pending_arrive: u64::MAX,
                pending_iv: u32::MAX,
            };
            node.rebuild_gamma();
            nodes.push(node);
        }
        let shard_rngs = (0..n_shards)
            .map(|s| ChaCha8Rng::seed_from_u64(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15).wrapping_add(s as u64 + 1)))
            .collect();
        Swarm { grid, nodes, nodes_per_shard, interventions: Vec::new(), shard_rngs }
    }

    pub fn n_shards(&self) -> usize {
        self.shard_rngs.len()
    }

    pub fn shard_range(&self, shard: usize) -> std::ops::Range<usize> {
        let a = (shard * self.nodes_per_shard).min(self.nodes.len());
        let b = ((shard + 1) * self.nodes_per_shard).min(self.nodes.len());
        a..b
    }

    /// Register an intervention (WIRE §5). Schedules the radial wave arrival
    /// per node and returns the `intervention_applied` truth event.
    pub fn apply(&mut self, iv: Intervention) -> Option<TruthEvent> {
        iv.mode()?;
        let idx = self.interventions.len() as u32;
        let t0 = iv.t_ms;
        let spread = iv.params.spread_ms.max(0.0);
        let origin_id;
        match &iv.target {
            Target::Node { node_id } => {
                origin_id = *node_id;
                if let Some(n) = self.nodes.get_mut(*node_id as usize) {
                    n.pending_arrive = t0;
                    n.pending_iv = idx;
                }
            }
            Target::Region { x, y, r } => {
                origin_id = self.grid.id(*x, *y);
                let r = r.max(0.5);
                self.schedule_wave(*x, *y, r, t0, spread, idx);
            }
            Target::All => {
                let (cx, cy) = ((self.grid.w as f32 - 1.0) / 2.0, (self.grid.h as f32 - 1.0) / 2.0);
                origin_id = self.grid.id(cx, cy);
                let r = (cx * cx + cy * cy).sqrt() + 1.0;
                self.schedule_wave(cx, cy, r, t0, spread, idx);
            }
        }
        let ev = TruthEvent {
            t_ms: t0,
            event: "intervention_applied",
            intervention_id: iv.id.clone(),
            node_id: origin_id,
            kind: iv.kind.clone(),
        };
        self.interventions.push(iv);
        Some(ev)
    }

    fn schedule_wave(&mut self, cx: f32, cy: f32, r: f32, t0: u64, spread_ms: f32, idx: u32) {
        let w = self.grid.w;
        let r2 = r * r;
        for (id, n) in self.nodes.iter_mut().enumerate() {
            let x = (id as u32 % w) as f32;
            let y = (id as u32 / w) as f32;
            let d2 = (x - cx) * (x - cx) + (y - cy) * (y - cy);
            if d2 <= r2 {
                let frac = (d2.sqrt() / r).min(1.0);
                n.pending_arrive = t0 + (frac * spread_ms) as u64;
                n.pending_iv = idx;
            }
        }
    }

    /// Advance one bin for one shard, writing spike counts into `counts`
    /// (len >= shard size) and appending truth events. Deterministic given seed.
    pub fn step_shard(&mut self, shard: usize, t_ms: u64, bin_ms: u32, counts: &mut [u16], truth: &mut Vec<TruthEvent>) {
        let range = self.shard_range(shard);
        let first = range.start as u32;
        step_nodes(&mut self.nodes[range], first, &mut self.shard_rngs[shard], &self.interventions, t_ms, bin_ms, counts, truth);
    }

    /// Advance one bin for all shards using `threads` OS threads (scoped).
    /// `counts[s]` receives shard `s` counts (resized), `truth[s]` gets that
    /// shard's truth events. Result is identical to calling `step_shard` per shard.
    pub fn step_all(&mut self, t_ms: u64, bin_ms: u32, threads: usize, counts: &mut [Vec<u16>], truth: &mut [Vec<TruthEvent>]) {
        let nps = self.nodes_per_shard;
        let ivs = &self.interventions;
        let n_shards = self.shard_rngs.len();
        let mut work: Vec<(usize, &mut [Node], &mut ChaCha8Rng, &mut Vec<u16>, &mut Vec<TruthEvent>)> = Vec::with_capacity(n_shards);
        let mut node_chunks = self.nodes.chunks_mut(nps.max(1));
        for (s, ((rng, c), tr)) in self.shard_rngs.iter_mut().zip(counts.iter_mut()).zip(truth.iter_mut()).enumerate() {
            let chunk: &mut [Node] = node_chunks.next().unwrap_or(&mut []);
            c.resize(chunk.len(), 0);
            work.push((s, chunk, rng, c, tr));
        }
        let threads = threads.max(1).min(n_shards.max(1));
        let per = n_shards.div_ceil(threads);
        std::thread::scope(|scope| {
            for group in work.chunks_mut(per.max(1)) {
                scope.spawn(move || {
                    for (s, nodes, rng, c, tr) in group.iter_mut() {
                        step_nodes(nodes, (*s * nps) as u32, rng, ivs, t_ms, bin_ms, c, tr);
                    }
                });
            }
        });
    }

    pub fn collapsed_count(&self) -> usize {
        self.nodes.iter().filter(|n| n.is_collapsed()).count()
    }
}


/// Core per-bin update for a contiguous block of nodes starting at `first_id`.
#[allow(clippy::too_many_arguments)]
pub fn step_nodes(
    nodes: &mut [Node],
    first_id: u32,
    rng: &mut ChaCha8Rng,
    ivs: &[Intervention],
    t_ms: u64,
    bin_ms: u32,
    counts: &mut [u16],
    truth: &mut Vec<TruthEvent>,
) {
    let bin_end = (t_ms + bin_ms as u64) as f64;
    for (i, n) in nodes.iter_mut().enumerate() {
        let id = first_id + i as u32;
        // 1. wave arrival -> mode switch
        if n.pending_arrive <= t_ms {
            let iv = &ivs[n.pending_iv as usize];
            n.mult0 = n.mult;
            n.shape0 = n.shape;
            n.t_switch = t_ms;
            n.onset_ms = iv.params.onset_ms.max(bin_ms as f32);
            n.intensity = iv.params.intensity.clamp(0.0, 1.0);
            n.mode = iv.mode().unwrap_or(MODE_BASELINE);
            n.iv = n.pending_iv;
            n.pending_arrive = u64::MAX;
            n.pending_iv = u32::MAX;
            if n.mode == MODE_BASELINE {
                n.flags = 0;
                n.low_ms = 0;
            }
        }
        // 2. per-mode dynamics (skipped for untouched baseline nodes)
        if n.mode != MODE_BASELINE || n.mult != 1.0 || n.shape != n.base_shape {
            let tau = (t_ms.saturating_sub(n.t_switch)) as f32 / n.onset_ms;
            let i = n.intensity;
            let (mult, shape) = match n.mode {
                MODE_TOXIN => {
                    let floor = (1.0 - i) * (1.0 - i);
                    let ramp = 1.0 - (-3.0 * tau).exp();
                    let mut m = n.mult0 + (floor - n.mult0) * ramp;
                    if tau > 1.0 {
                        // continued collapse after onset completes
                        m *= (-(tau - 1.0) * (0.5 + 1.5 * i)).exp();
                    }
                    let sh = n.shape0 + (n.base_shape / (1.0 + 3.0 * i) - n.shape0) * ramp;
                    (m, sh)
                }
                MODE_DRUG => {
                    let target = n.mult0 + (1.0 - n.mult0) * i;
                    let ramp = 1.0 - (-2.0 * tau).exp();
                    let m = n.mult0 + (target - n.mult0) * ramp;
                    let sh = n.shape0 + (n.base_shape - n.shape0) * ramp * i;
                    (m, sh)
                }
                MODE_RADIATION => {
                    let burst = 1.0 + 3.0 * i * (-4.0 * tau).exp();
                    let supp = 1.0 - 0.95 * i * (1.0 - (-2.0 * (tau - 0.5).max(0.0)).exp());
                    let noise: f32 = rand_distr::StandardNormal.sample(rng);
                    let m = (n.mult0 * burst * supp * (1.0 + 0.4 * i * noise)).max(MIN_MULT);
                    let sh = (n.base_shape / (1.0 + 4.0 * i * (1.0 - (-tau).exp()))).max(MIN_SHAPE);
                    (m, sh)
                }
                _ => {
                    // baseline: relax to intrinsic
                    let ramp = 1.0 - (-3.0 * tau).exp();
                    (n.mult0 + (1.0 - n.mult0) * ramp, n.shape0 + (n.base_shape - n.shape0) * ramp)
                }
            };
            let mult = mult.clamp(MIN_MULT, 8.0);
            let shape = shape.clamp(MIN_SHAPE, 50.0);
            if (mult - n.mult).abs() > 1e-4 * n.mult.max(1e-3) || (shape - n.shape).abs() > 1e-3 {
                let old_mult = n.mult;
                n.mult = mult;
                n.shape = shape;
                n.rebuild_gamma();
                // if the rate jumped up, a stale far-future spike must not silence the node
                if mult > old_mult * 1.5 {
                    let mean = Node::isi_mean_ms(n.base_rate, mult, n.refractory) + n.refractory;
                    let horizon = bin_end + 2.0 * mean as f64;
                    if n.next_spike_ms > horizon {
                        n.next_spike_ms = t_ms as f64 + (rng.gen::<f32>() * mean) as f64;
                    }
                }
            }
        }
        // 3. spikes in this bin
        let mut c: u32 = 0;
        while n.next_spike_ms < bin_end {
            c += 1;
            n.next_spike_ms += (n.refractory + n.gamma.sample(rng)) as f64;
        }
        counts[i] = c.min(u16::MAX as u32) as u16;
        // 4. ground truth
        if n.iv != u32::MAX {
            if n.mult < SYMPTOM_FRACTION {
                n.low_ms = n.low_ms.saturating_add(bin_ms);
                if n.low_ms >= SYMPTOM_HOLD_MS && n.flags & FLAG_SYMPTOM == 0 {
                    n.flags |= FLAG_SYMPTOM;
                    n.flags &= !FLAG_RECOVERED;
                    let iv = &ivs[n.iv as usize];
                    truth.push(TruthEvent {
                        t_ms,
                        event: "symptom_onset",
                        intervention_id: iv.id.clone(),
                        node_id: id,
                        kind: iv.kind.clone(),
                    });
                }
            } else {
                n.low_ms = 0;
                if n.flags & FLAG_SYMPTOM != 0 && n.flags & FLAG_RECOVERED == 0 && n.mult >= RECOVER_FRACTION {
                    n.flags |= FLAG_RECOVERED;
                    let iv = &ivs[n.iv as usize];
                    truth.push(TruthEvent {
                        t_ms,
                        event: "recovered",
                        intervention_id: iv.id.clone(),
                        node_id: id,
                        kind: iv.kind.clone(),
                    });
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const T0: u64 = 1_757_400_000_000;

    fn small_swarm(seed: u64) -> Swarm {
        Swarm::new(2000, 2, Grid { w: 40, h: 50 }, seed, &TwinParams::defaults(), T0)
    }

    fn run(swarm: &mut Swarm, from_bin: u64, n_bins: u64, truth: &mut Vec<TruthEvent>) -> Vec<u64> {
        let mut totals = vec![0u64; swarm.nodes.len()];
        let mut counts = vec![0u16; swarm.nodes_per_shard];
        for b in from_bin..from_bin + n_bins {
            let t = T0 + b * 100;
            for s in 0..swarm.n_shards() {
                let range = swarm.shard_range(s);
                swarm.step_shard(s, t, 100, &mut counts[..range.len()], truth);
                for (i, id) in range.enumerate() {
                    totals[id] += counts[i] as u64;
                }
            }
        }
        totals
    }

    #[test]
    fn same_seed_same_counts() {
        let mut a = small_swarm(7);
        let mut b = small_swarm(7);
        let mut c = small_swarm(8);
        let mut tr = Vec::new();
        let ta = run(&mut a, 0, 50, &mut tr);
        let tb = run(&mut b, 0, 50, &mut tr);
        let tc = run(&mut c, 0, 50, &mut tr);
        assert_eq!(ta, tb);
        assert_ne!(ta, tc);
    }

    #[test]
    fn step_all_matches_step_shard() {
        let mut a = small_swarm(9);
        let mut b = small_swarm(9);
        let mut tr = Vec::new();
        b.apply(toxin("x", 20.0, 25.0, 6.0, 1.0, T0)).unwrap();
        a.apply(toxin("x", 20.0, 25.0, 6.0, 1.0, T0)).unwrap();
        let ta = run(&mut a, 0, 60, &mut tr);
        let mut counts = vec![Vec::new(); 2];
        let mut truth = vec![Vec::new(); 2];
        let mut tb = vec![0u64; b.nodes.len()];
        for k in 0..60u64 {
            b.step_all(T0 + k * 100, 100, 3, &mut counts, &mut truth);
            for s in 0..2 {
                for (i, id) in b.shard_range(s).enumerate() {
                    tb[id] += counts[s][i] as u64;
                }
            }
        }
        assert_eq!(ta, tb);
        let n_truth: usize = truth.iter().map(|t| t.len()).sum();
        assert_eq!(n_truth, tr.len());
    }

    #[test]
    fn baseline_rates_match_parameters() {
        let mut s = small_swarm(1);
        let mut tr = Vec::new();
        let totals = run(&mut s, 0, 200, &mut tr); // 20 s
        let mean_rate: f64 = totals.iter().sum::<u64>() as f64 / (20.0 * totals.len() as f64);
        // defaults: rate U(5,40) -> population mean 22.5 Hz
        assert!((mean_rate - 22.5).abs() < 2.0, "mean rate {mean_rate}");
        let expected: f64 = s.nodes.iter().map(|n| n.base_rate_hz() as f64).sum::<f64>() / totals.len() as f64;
        assert!((mean_rate - expected).abs() < 1.0, "{mean_rate} vs {expected}");
        assert!(tr.is_empty());
    }

    fn toxin(id: &str, x: f32, y: f32, r: f32, intensity: f32, t_ms: u64) -> Intervention {
        Intervention {
            id: id.into(),
            t_ms,
            kind: "toxin".into(),
            subtype: None,
            target: Target::Region { x, y, r },
            params: InterventionParams { intensity, spread_ms: 2000.0, onset_ms: 3000.0 },
            source: Some("script".into()),
        }
    }

    #[test]
    fn toxin_lowers_counts_and_fires_symptom() {
        let mut s = small_swarm(3);
        let mut tr = Vec::new();
        let before = run(&mut s, 0, 100, &mut tr); // 10 s baseline
        let t_iv = T0 + 100 * 100;
        let ev = s.apply(toxin("iv-1", 20.0, 25.0, 10.0, 0.9, t_iv)).unwrap();
        assert_eq!(ev.event, "intervention_applied");
        assert_eq!(ev.node_id, s.grid.id(20.0, 25.0));
        let after = run(&mut s, 100, 300, &mut tr); // 30 s
        let inside: Vec<usize> = (0..s.nodes.len())
            .filter(|&id| {
                let (x, y) = s.grid.xy(id as u32);
                (x - 20.0).powi(2) + (y - 25.0).powi(2) <= 100.0
            })
            .collect();
        assert!(inside.len() > 200);
        let sum_before: u64 = inside.iter().map(|&i| before[i]).sum();
        let sum_after: u64 = inside.iter().map(|&i| after[i]).sum();
        // 3x longer window yet far fewer spikes
        assert!((sum_after as f64) < 0.5 * sum_before as f64, "before={sum_before} after={sum_after}");
        // nodes outside the region are untouched (baseline)
        let outside = (0..s.nodes.len()).find(|id| !inside.contains(id)).unwrap();
        assert_eq!(s.nodes[outside].mode(), MODE_BASELINE);
        assert_eq!(s.nodes[outside].rate_mult(), 1.0);
        // symptom onset fires for the exposed nodes
        let symptoms: Vec<&TruthEvent> = tr.iter().filter(|e| e.event == "symptom_onset").collect();
        assert!(symptoms.len() >= inside.len() * 9 / 10, "{} symptoms for {} nodes", symptoms.len(), inside.len());
        assert!(symptoms.iter().all(|e| e.intervention_id == "iv-1" && e.kind == "toxin"));
        let first = symptoms.iter().map(|e| e.t_ms).min().unwrap();
        assert!(first >= t_iv + 2000, "symptom must follow >=2 s of low rate");
        assert!(s.collapsed_count() >= inside.len() * 9 / 10);
        // each node fires symptom_onset at most once
        let mut ids: Vec<u32> = symptoms.iter().map(|e| e.node_id).collect();
        ids.sort_unstable();
        ids.dedup();
        assert_eq!(ids.len(), symptoms.len());
    }

    #[test]
    fn drug_recovers_and_baseline_resets() {
        let mut s = small_swarm(5);
        let mut tr = Vec::new();
        s.apply(toxin("tox", 20.0, 25.0, 8.0, 1.0, T0)).unwrap();
        run(&mut s, 0, 150, &mut tr);
        assert!(tr.iter().any(|e| e.event == "symptom_onset"));
        let center = s.grid.id(20.0, 25.0) as usize;
        assert!(s.nodes[center].is_collapsed());
        let drug = Intervention {
            id: "drug".into(),
            t_ms: T0 + 150 * 100,
            kind: "drug".into(),
            subtype: None,
            target: Target::Region { x: 20.0, y: 25.0, r: 8.0 },
            params: InterventionParams { intensity: 1.0, spread_ms: 0.0, onset_ms: 2000.0 },
            source: None,
        };
        s.apply(drug).unwrap();
        let after = run(&mut s, 150, 200, &mut tr);
        assert_eq!(s.nodes[center].mode(), MODE_DRUG);
        assert!(s.nodes[center].rate_mult() > 0.9, "{}", s.nodes[center].rate_mult());
        assert!(after[center] > 0, "node must spike again after drug");
        assert!(tr.iter().any(|e| e.event == "recovered" && e.intervention_id == "drug"));
        // baseline reset for everyone
        let reset = Intervention {
            id: "base".into(),
            t_ms: T0 + 350 * 100,
            kind: "baseline".into(),
            subtype: None,
            target: Target::All,
            params: InterventionParams { intensity: 1.0, spread_ms: 0.0, onset_ms: 1000.0 },
            source: None,
        };
        s.apply(reset).unwrap();
        run(&mut s, 350, 50, &mut tr);
        assert!(s.nodes.iter().all(|n| n.mode() == MODE_BASELINE && n.rate_mult() > 0.95));
    }

    #[test]
    fn radiation_bursts_then_suppresses() {
        let mut s = small_swarm(11);
        let mut tr = Vec::new();
        let base = run(&mut s, 0, 100, &mut tr);
        let rad = Intervention {
            id: "rad".into(),
            t_ms: T0 + 100 * 100,
            kind: "radiation".into(),
            subtype: None,
            target: Target::All,
            params: InterventionParams { intensity: 1.0, spread_ms: 0.0, onset_ms: 4000.0 },
            source: None,
        };
        s.apply(rad).unwrap();
        let burst = run(&mut s, 100, 10, &mut tr); // first 1 s
        run(&mut s, 110, 90, &mut tr); // advance 9 s
        let late = run(&mut s, 200, 10, &mut tr); // 10 s after onset, 1 s
        let sb: u64 = base.iter().sum::<u64>() / 10; // per second baseline
        let bb: u64 = burst.iter().sum();
        let lb: u64 = late.iter().sum();
        assert!(bb as f64 > 1.5 * sb as f64, "burst {bb} vs base {sb}");
        assert!((lb as f64) < 0.5 * sb as f64, "late {lb} vs base {sb}");
    }

    #[test]
    fn params_json_parses() {
        // explicit firing rate wins
        let j = r#"{"units":[{"shape":2.2,"rate_hz":12.0,"mean_rate_hz":12.0}],"source":"zenodo"}"#;
        let p: TwinParams = serde_json::from_str(j).unwrap();
        assert_eq!(p.units.len(), 1);
        let s = Swarm::new(10, 1, Grid { w: 5, h: 2 }, 1, &p, T0);
        assert!(s.nodes.iter().all(|n| (n.base_rate_hz() - 12.0).abs() < 1e-6));
        // without it, `rate_hz` is the gamma rate parameter: firing rate = 1/(refr + shape/rate)
        // (unit 1 of ml/artifacts/twin_params.json: 0.6492, 3.8746 Hz, 2.1523 ms -> 5.893 Hz)
        let j0 = r#"{"units":[{"shape":0.6492,"rate_hz":3.8746,"refractory_ms":2.1523}]}"#;
        let p0: TwinParams = serde_json::from_str(j0).unwrap();
        assert!((p0.units[0].firing_rate_hz() - 5.893).abs() < 1e-2);
        let s0 = Swarm::new(10, 1, Grid { w: 5, h: 2 }, 1, &p0, T0);
        assert!(s0.nodes.iter().all(|n| (n.base_rate_hz() - 5.893).abs() < 1e-2));
        let j2 = r#"{"shape":{"dist":"lognormal","mu":0.7,"sigma":0.2},"rate_hz":{"dist":"uniform","min":10,"max":11}}"#;
        let p2: TwinParams = serde_json::from_str(j2).unwrap();
        let s2 = Swarm::new(10, 1, Grid { w: 5, h: 2 }, 1, &p2, T0);
        assert!(s2.nodes.iter().all(|n| n.base_rate_hz() >= 10.0 && n.base_rate_hz() <= 11.0));
    }

    #[test]
    fn intervention_json_roundtrip() {
        let j = r#"{"id":"u","t_ms":1,"kind":"toxin","target":{"type":"region","x":200,"y":125,"r":40},
                    "params":{"intensity":0.8,"spread_ms":20000,"onset_ms":5000},"source":"dashboard"}"#;
        let iv: Intervention = serde_json::from_str(j).unwrap();
        assert_eq!(iv.target, Target::Region { x: 200.0, y: 125.0, r: 40.0 });
        assert_eq!(iv.mode(), Some(MODE_TOXIN));
        let bad: Intervention = serde_json::from_str(r#"{"id":"u","t_ms":1,"kind":"laser","target":{"type":"all"}}"#).unwrap();
        assert_eq!(bad.mode(), None);
    }
}
