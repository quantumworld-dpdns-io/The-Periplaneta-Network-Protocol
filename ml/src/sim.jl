# sim.jl -- Julia reimplementation of the WS-B twin dynamics (PLAN "WS-B 1/2").
#
# ml/ trains and evaluates against THIS, never against a WS-B binary, so
# `just train` / `just eval` work on a laptop with nothing else running.
# The generative model is the same one WS-B implements in twin.rs:
#
#   * each node is a gamma renewal process with an absolute refractory
#     period, parameters drawn from ml/artifacts/twin_params.json (which is
#     itself fitted to real insect recordings -- see docs/WS-C.md);
#   * an intervention multiplies the instantaneous firing rate by a
#     time-varying gain and, for toxin/radiation, inflates the ISI
#     variability (shape drops -> CV rises);
#   * ground truth `symptom_onset` fires when the firing rate has stayed
#     below 5% of the node's own baseline for >= 2 s (WIRE section 6).
#
# Everything is seeded: `simulate_node(...; rng = MersenneTwister(seed))`.

module Sim

using Random, Statistics, Distributions

export TwinParam, TwinTrace, simulate_node, simulate_dataset,
       rate_gain, shape_gain, KIND_NAMES, kind_id,
       DEFAULT_PARAMS, BIN_MS, WINDOW_S

const BIN_MS   = 100        # WIRE 4.2 bin size
const WINDOW_S = 1.0        # WIRE 4.3 feature window
const KIND_NAMES = ("baseline", "toxin", "drug", "radiation")
kind_id(k::AbstractString) = something(findfirst(==(k), KIND_NAMES), 1) - 1

"""
    TwinParam(shape, rate_hz, refractory_ms)

One virtual node's gamma-renewal parameters, as stored in twin_params.json.
`rate_hz` is the gamma rate parameter; the resulting mean firing rate is
`1 / (refractory_s + shape / rate_hz)`.
"""
struct TwinParam
    shape::Float64
    rate_hz::Float64
    refractory_ms::Float64
end

"Mean firing rate implied by a TwinParam, in Hz."
baseline_rate(p::TwinParam) = 1 / (p.refractory_ms / 1000 + p.shape / p.rate_hz)

const DEFAULT_PARAMS = [TwinParam(1.5, 12.0, 2.0)]

# ----------------------------------------------------- intervention gains ----
"""
    rate_gain(kind, t_s, onset_s, intensity, spread_s) -> Float64

Multiplicative gain applied to a node's firing rate at time `t_s`.

* `toxin`     -- sigmoidal shutdown after `onset_s + spread_s` diffusion
                 delay, asymptoting to `1 - intensity`; at intensity ~1 this
                 drives the node below the 5% collapse line.
* `drug`      -- an initial dip followed by a recovery curve that overshoots
                 baseline slightly before settling (the "rescue" signature).
* `radiation` -- an acute burst (rate x(1 + 2*intensity)) lasting ~3 s, then
                 a noisy partial suppression.
* `baseline`  -- 1.0 everywhere.
"""
function rate_gain(kind::Integer, t_s::Real, onset_s::Real, intensity::Real, spread_s::Real)
    dt = t_s - onset_s - spread_s
    dt < 0 && return 1.0
    if kind == 1            # toxin
        tau = 6.0
        floorv = max(0.0, 1 - intensity)
        return floorv + (1 - floorv) * exp(-dt / tau)
    elseif kind == 2        # drug
        dip = 1 - 0.45 * intensity
        rec = 1 - (1 - dip) * exp(-dt / 4.0)
        return rec * (1 + 0.15 * intensity * exp(-((dt - 12.0)^2) / 50.0))
    elseif kind == 3        # radiation
        burst = 1 + 2 * intensity * exp(-dt / 3.0)
        supp  = 1 - 0.5 * intensity * (1 - exp(-dt / 10.0))
        return burst * supp
    end
    return 1.0
end

"""
    shape_gain(kind, t_s, onset_s, intensity, spread_s) -> Float64

Multiplier on the gamma shape. Shape down => ISI CV up => burstier, more
irregular firing. Toxin and radiation make a node irregular long before its
rate has visibly collapsed, which is the signal the GRU learns to read.
"""
function shape_gain(kind::Integer, t_s::Real, onset_s::Real, intensity::Real, spread_s::Real)
    dt = t_s - onset_s - spread_s
    dt < 0 && return 1.0
    if kind == 1            # toxin: progressive loss of regularity
        return 1 / (1 + 1.6 * intensity * (1 - exp(-dt / 8.0)))
    elseif kind == 2        # drug: firing becomes *more* regular
        return 1 + 0.6 * intensity * (1 - exp(-dt / 6.0))
    elseif kind == 3        # radiation: strongly bursty
        return 1 / (1 + 2.2 * intensity * exp(-dt / 12.0))
    end
    return 1.0
end

# ------------------------------------------------------------ the process ----
"""
    TwinTrace

Result of one simulated node: per-100 ms-bin spike counts, the true class
label per 1 s window, the intervention onset and the ground-truth
`symptom_onset` time in seconds (`NaN` if the node never collapsed).
"""
struct TwinTrace
    counts::Vector{Int}          # per BIN_MS bin
    kind::Int                    # 0..3
    onset_s::Float64
    symptom_s::Float64           # NaN if no collapse
    baseline_rate_hz::Float64
    intensity::Float64
end

"""
    simulate_node(p, kind, dur_s; rng, onset_s, intensity, spread_s) -> TwinTrace

Draw one node's spike train by gamma renewal with a time-varying rate, bin it
at `BIN_MS`, and compute the WIRE section 6 `symptom_onset`: the first time the
firing rate has been under 5% of the node's own pre-intervention baseline for
2 consecutive seconds.
"""
function simulate_node(p::TwinParam, kind::Integer, dur_s::Real;
                       rng::AbstractRNG = Random.default_rng(),
                       onset_s::Real = 30.0,
                       intensity::Real = 0.85,
                       spread_s::Real = 2.0)
    nbins = round(Int, dur_s * 1000 / BIN_MS)
    counts = zeros(Int, nbins)
    refr = p.refractory_ms / 1000
    t = 0.0
    base_rate = baseline_rate(p)

    # Gamma renewal with rate/shape modulated by the intervention.
    while t < dur_s
        rg = rate_gain(kind, t, onset_s, intensity, spread_s)
        sg = shape_gain(kind, t, onset_s, intensity, spread_s)
        k = clamp(p.shape * sg, 0.05, 60.0)
        # Hold the *mean* ISI consistent with rate_gain: scale the gamma rate
        # by rg (rate up => ISI down) and by the shape change.
        eff_rate = p.rate_hz * max(rg, 1e-4) * (sg > 0 ? sg : 1.0)
        theta = 1 / max(eff_rate, 1e-6)
        isi = refr + rand(rng, Gamma(k, theta))
        isi = min(isi, 30.0)                      # keep a dead node advancing
        t += isi
        t >= dur_s && break
        b = floor(Int, t * 1000 / BIN_MS) + 1
        1 <= b <= nbins && (counts[b] += 1)
    end

    # ground truth: rate < 5% of baseline for >= 2 s  (WIRE section 6)
    bins_per_s = round(Int, 1000 / BIN_MS)
    symptom = NaN
    if kind != 0
        pre_end = max(1, floor(Int, onset_s * bins_per_s))
        pre = @view counts[1:min(pre_end, nbins)]
        base_obs = isempty(pre) ? base_rate : sum(pre) / (length(pre) * BIN_MS / 1000)
        thr = 0.05 * max(base_obs, 1e-6)
        run = 0
        for s in 1:(nbins ÷ bins_per_s)
            a = (s - 1) * bins_per_s + 1
            r = sum(@view counts[a:(a + bins_per_s - 1)])   # spikes in 1 s == Hz
            run = r < thr ? run + 1 : 0
            if run >= 2
                symptom = Float64(s)          # end of the 2nd sub-threshold second
                break
            end
        end
    end

    return TwinTrace(counts, Int(kind), Float64(onset_s), symptom, base_rate, Float64(intensity))
end

"""
    simulate_dataset(params, n_nodes, dur_s; rng, kinds, onset_range, intensity_range)

Simulate `n_nodes` nodes, cycling through `kinds` so the classes stay
balanced, each with its own randomly drawn intervention onset and intensity.
Returns a `Vector{TwinTrace}`.
"""
function simulate_dataset(params::AbstractVector{TwinParam}, n_nodes::Integer, dur_s::Real;
                          rng::AbstractRNG = Random.default_rng(),
                          kinds = (0, 1, 2, 3),
                          onset_range = (25.0, 45.0),
                          intensity_range = (0.6, 1.0),
                          spread_range = (0.0, 6.0))
    isempty(params) && (params = DEFAULT_PARAMS)
    traces = Vector{TwinTrace}(undef, n_nodes)
    for i in 1:n_nodes
        p = params[rand(rng, 1:length(params))]
        kind = kinds[(i - 1) % length(kinds) + 1]
        onset = onset_range[1] + rand(rng) * (onset_range[2] - onset_range[1])
        inten = intensity_range[1] + rand(rng) * (intensity_range[2] - intensity_range[1])
        spread = spread_range[1] + rand(rng) * (spread_range[2] - spread_range[1])
        traces[i] = simulate_node(p, kind, dur_s; rng = rng, onset_s = onset,
                                  intensity = inten, spread_s = spread)
    end
    return traces
end

end # module Sim
