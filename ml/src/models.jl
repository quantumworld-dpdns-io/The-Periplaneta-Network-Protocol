# models.jl -- the two detectors that turn an 8-feature window into a
# Neural Stress Index in [0,1] and a state enum (WIRE section 2).
#
#   (a) ZScoreDetector  -- unsupervised, per-node online baseline over the
#       first BASELINE_S seconds, then Hotelling T^2 drift on the feature
#       vector. No training, runs in the worker with no model file.
#   (b) GRUDetector     -- Flux GRU(8 => 32) over 30 consecutive windows,
#       4-class softmax; stress = 1 - P(baseline).
#   (c) Transformer     -- 2-layer encoder (d=32, 4 heads) over the same
#       30-window sequences; same softmax head. Not a day-scale clinical
#       model: same 8×30 twin features as the GRU.
#
# All three expose observe!(d, node_id, features, t_ms) -> (stress, state)
# so feature-worker can switch on DETECTOR=zscore|gru|transformer.

module Models

using Statistics, LinearAlgebra, Random, Flux, BSON
using NNlib: batched_mul

export ZScoreDetector, GRUDetector, GRUNet, TransformerNet, TransformerBlock,
       observe!, reset!, stress_state, build_gru, build_transformer,
       gru_forward, seq_forward,
       load_gru_artifact, load_gru_detector,
       load_transformer_artifact, load_transformer_detector,
       BASELINE_S, SEQ_LEN, N_CLASSES, N_FEATURES_USED,
       STATE_BASELINE, STATE_TOXIN, STATE_DRUG, STATE_RADIATION,
       normalize_features, feature_stats

const BASELINE_S      = 30.0    # per-node online baseline window, seconds
const SEQ_LEN         = 30      # GRU input: 30 x 1 s windows
const N_CLASSES       = 4
const N_FEATURES_USED = 8

const STATE_BASELINE  = 0
const STATE_TOXIN     = 1
const STATE_DRUG      = 2
const STATE_RADIATION = 3

"""Sinusoidal positional encoding `(d_model, seq_len)`, not trained."""
function _sinusoidal_pe(d_model::Int, seq_len::Int)
    pe = zeros(Float32, d_model, seq_len)
    @inbounds for t in 1:seq_len, i in 1:d_model
        angle = Float32(t - 1) / Float32(10000^((i - 1) / d_model))
        pe[i, t] = isodd(i) ? sin(angle) : cos(angle)
    end
    return pe
end
const TRANSFORMER_PE = _sinusoidal_pe(32, SEQ_LEN)

# ============================================================ z-score / T2 ===

"""
    NodeBaseline

Running mean/M2 (Welford) over the first `BASELINE_S` seconds of a node's
feature stream, plus the count of windows seen. Frozen once the baseline
window closes, so later drift cannot contaminate the reference.
"""
mutable struct NodeBaseline
    n::Int
    mean::Vector{Float64}
    m2::Vector{Float64}
    frozen::Bool
    t0_ms::Int64
    last_state::Int
    alarm_run::Int
end
NodeBaseline(nf::Int, t0_ms::Int64) =
    NodeBaseline(0, zeros(nf), zeros(nf), false, t0_ms, STATE_BASELINE, 0)

"""
    ZScoreDetector(; nf=8, baseline_s=BASELINE_S, stress_thresh=0.9, sustain=2)

Unsupervised drift detector. For each node it learns a diagonal Gaussian
baseline online (the first `baseline_s` seconds), then scores every window
with a diagonal Hotelling T^2 statistic

    T2 = sum_j ((x_j - mu_j) / sigma_j)^2

over the features that actually vary at baseline. Stress is a squashed T^2,
`1 - exp(-T2 / (2 * nf))`, which is 0 at the baseline mean and saturates
smoothly. A state is only declared after `sustain` consecutive windows with
`stress >= stress_thresh`, which is what stops single noisy windows from
raising alerts. Thresholding the same `stress` quantity both detectors emit
is what lets `ml/scripts/eval.jl` calibrate them to a matched false-alarm
rate before comparing lead times.

Class assignment is rule-based (the z-score detector is unsupervised and
cannot name a class): rate falling and CV rising -> toxin; rate rising
sharply / very bursty -> radiation; rate recovering with CV falling ->
drug.
"""
mutable struct ZScoreDetector
    nf::Int
    baseline_s::Float64
    stress_thresh::Float64
    sustain::Int
    nodes::Dict{UInt32,NodeBaseline}
end
ZScoreDetector(; nf::Int = N_FEATURES_USED, baseline_s::Real = BASELINE_S,
               stress_thresh::Real = 0.9, sustain::Int = 2) =
    ZScoreDetector(nf, Float64(baseline_s), Float64(stress_thresh), sustain,
                   Dict{UInt32,NodeBaseline}())

reset!(d::ZScoreDetector) = (empty!(d.nodes); d)

function _update_baseline!(b::NodeBaseline, x::AbstractVector{<:Real})
    b.n += 1
    @inbounds for j in eachindex(x)
        delta = x[j] - b.mean[j]
        b.mean[j] += delta / b.n
        b.m2[j] += delta * (x[j] - b.mean[j])
    end
    return b
end

_sigma(b::NodeBaseline) = b.n > 1 ? sqrt.(max.(b.m2 ./ (b.n - 1), 0.0)) : zeros(length(b.mean))

"""
    observe!(d::ZScoreDetector, node_id, x, t_ms) -> (stress, state)

Feed one 1 s feature window. During the baseline window the detector
returns `(0.0, STATE_BASELINE)` and learns; afterwards it scores.
"""
function observe!(d::ZScoreDetector, node_id::Integer, x::AbstractVector{<:Real}, t_ms::Integer)
    key = UInt32(node_id)
    b = get!(d.nodes, key) do
        NodeBaseline(d.nf, Int64(t_ms))
    end
    elapsed_s = (Int64(t_ms) - b.t0_ms) / 1000

    if !b.frozen
        _update_baseline!(b, x)
        if elapsed_s >= d.baseline_s && b.n >= 5
            b.frozen = true
        end
        return (0.0, STATE_BASELINE)
    end

    sig = _sigma(b)
    # A feature that never moved at baseline gets a floor so it cannot make
    # T^2 explode; a feature that is identically zero is skipped entirely.
    t2 = 0.0
    used = 0
    @inbounds for j in eachindex(x)
        s = sig[j]
        scale = max(abs(b.mean[j]), 1e-6)
        s = max(s, 0.05 * scale)
        (isfinite(x[j]) && isfinite(b.mean[j])) || continue
        (b.mean[j] == 0 && x[j] == 0) && continue
        z = (x[j] - b.mean[j]) / s
        t2 += z * z
        used += 1
    end
    used == 0 && return (0.0, STATE_BASELINE)

    stress = 1 - exp(-t2 / (2 * used))
    over = stress >= d.stress_thresh
    b.alarm_run = over ? b.alarm_run + 1 : 0

    state = STATE_BASELINE
    if b.alarm_run >= d.sustain
        # rule-based class from the direction of the drift
        drate = (x[1] - b.mean[1]) / max(abs(b.mean[1]), 1e-6)
        dcv   = (x[3] - b.mean[3]) / max(abs(b.mean[3]), 1e-6)
        dburst = (x[4] - b.mean[4]) / max(abs(b.mean[4]), 1e-6)
        if drate > 0.5 || dburst > 1.0
            state = STATE_RADIATION
        elseif drate < -0.2
            state = STATE_TOXIN
        elseif dcv < -0.15
            state = STATE_DRUG
        else
            state = STATE_TOXIN
        end
    end
    b.last_state = state
    return (clamp(stress, 0.0, 1.0), state)
end

# ==================================================================== GRU ====

"""
    GRUNet(gru, head)

Flux GRU(8 => 32) followed by Dense(32 => 4). Input is
`(features, batch, time)`; only the final hidden state feeds the head, so
the output is `(4, batch)` class logits.
"""
struct GRUNet
    gru::Any
    head::Any
end
Flux.@layer GRUNet

"""
    build_gru(; nf=8, hidden=32, nclasses=4, rng=Random.default_rng())
"""
function build_gru(; nf::Int = N_FEATURES_USED, hidden::Int = 32,
                   nclasses::Int = N_CLASSES, rng::AbstractRNG = Random.default_rng())
    return GRUNet(Flux.GRU(nf => hidden), Flux.Dense(hidden => nclasses))
end

"""
    (m::GRUNet)(x)

`x` is `(nf, batch, time)`; returns `(nclasses, batch)` logits from the last
timestep's hidden state.
"""
function (m::GRUNet)(x::AbstractArray{<:Real,3})
    h = m.gru(x)              # (hidden, batch, time)
    return m.head(h[:, :, end])
end

"""
    gru_forward(m, x) -> probabilities (nclasses, batch)
"""
gru_forward(m, x::AbstractArray{<:Real,3}) = seq_forward(m, x)
seq_forward(m, x::AbstractArray{<:Real,3}) = Flux.softmax(m(x))

# ============================================================ Transformer ===

"""
    TransformerBlock(nhead, Wqkv, Wo, ln1, ff, ln2)

Pre-norm block: LayerNorm → 4-head self-attention → residual →
LayerNorm → GELU FFN → residual. Attention is over the time axis of an
`(d, batch, time)` tensor.
"""
struct TransformerBlock
    nhead::Int
    Wqkv::Any
    Wo::Any
    ln1::Any
    ff::Any
    ln2::Any
end
Flux.@layer TransformerBlock

function _mha(blk::TransformerBlock, x::AbstractArray{<:Real,3})
    d, B, T = size(x)
    nhead = blk.nhead
    hd = d ÷ nhead
    qkv = reshape(blk.Wqkv(reshape(x, d, :)), d, 3, B, T)
    q = reshape(permutedims(reshape(qkv[:, 1, :, :], hd, nhead, B, T), (1, 4, 2, 3)),
                hd, T, nhead * B)
    k = reshape(permutedims(reshape(qkv[:, 2, :, :], hd, nhead, B, T), (1, 4, 2, 3)),
                hd, T, nhead * B)
    v = reshape(permutedims(reshape(qkv[:, 3, :, :], hd, nhead, B, T), (1, 4, 2, 3)),
                hd, T, nhead * B)
    scale = Float32(inv(sqrt(Float64(hd))))
    scores = scale .* batched_mul(permutedims(q, (2, 1, 3)), k)
    weights = softmax(scores; dims = 2)
    ctx = batched_mul(v, permutedims(weights, (2, 1, 3)))
    ctx = permutedims(reshape(ctx, hd, T, nhead, B), (1, 3, 4, 2))
    ctx = reshape(ctx, d, B, T)
    return reshape(blk.Wo(reshape(ctx, d, :)), d, B, T)
end

function (blk::TransformerBlock)(x::AbstractArray{<:Real,3})
    d, B, T = size(x)
    y = _mha(blk, blk.ln1(x)) .+ x
    h = reshape(blk.ff(reshape(blk.ln2(y), d, :)), d, B, T)
    return h .+ y
end

"""
    TransformerNet(embed, block1, block2, head)

Tiny encoder matching the GRU I/O contract: input `(nf, batch, time)`,
output `(nclasses, batch)` logits from a mean-pool over time.
d_model=32, 4 heads, 2 layers — sized for 8×30 twin windows, not for
day-scale clinical sequences.
"""
struct TransformerNet
    embed::Any
    block1::TransformerBlock
    block2::TransformerBlock
    head::Any
end
Flux.@layer TransformerNet

function _make_block(d_model::Int, nhead::Int)
    return TransformerBlock(
        nhead,
        Flux.Dense(d_model => 3 * d_model),
        Flux.Dense(d_model => d_model),
        Flux.LayerNorm(d_model),
        Flux.Chain(Flux.Dense(d_model => 4 * d_model, Flux.gelu),
                   Flux.Dense(4 * d_model => d_model)),
        Flux.LayerNorm(d_model),
    )
end

function build_transformer(; nf::Int = N_FEATURES_USED, d_model::Int = 32,
                           nhead::Int = 4, nclasses::Int = N_CLASSES)
    d_model % nhead == 0 || throw(ArgumentError("nhead must divide d_model"))
    return TransformerNet(
        Flux.Dense(nf => d_model),
        _make_block(d_model, nhead),
        _make_block(d_model, nhead),
        Flux.Dense(d_model => nclasses),
    )
end

function (m::TransformerNet)(x::AbstractArray{<:Real,3})
    nf, B, T = size(x)
    T > size(TRANSFORMER_PE, 2) && throw(ArgumentError("sequence length $T exceeds PE"))
    d = size(m.embed.weight, 1)
    h = reshape(m.embed(reshape(x, nf, :)), d, B, T)
    pe = TRANSFORMER_PE[:, 1:T]
    h = h .+ reshape(pe, d, 1, T)
    h = m.block2(m.block1(h))
    pooled = dropdims(mean(h; dims = 3); dims = 3)
    return m.head(pooled)
end

"""
    feature_stats(X) -> (mu, sd)

Per-feature mean/std over a `(nf, n)` matrix, used to standardise GRU input.
Saved alongside the weights so inference matches training exactly.
"""
function feature_stats(X::AbstractMatrix{<:Real})
    mu = vec(mean(X; dims = 2))
    sd = vec(std(X; dims = 2))
    sd = map(s -> (isfinite(s) && s > 1e-8) ? s : 1.0, sd)
    return (Float32.(mu), Float32.(sd))
end

normalize_features(x::AbstractArray, mu, sd) = (Float32.(x) .- mu) ./ sd

"""
    GRUDetector(net, mu, sd; seq_len=SEQ_LEN, sustain=2, stress_thresh=0.5,
                n_baseline=30, clip=10.0)

Sliding-window wrapper around the trained GRU.

Per node it keeps (a) the same online baseline the z-score detector learns --
the first `n_baseline` windows -- and (b) a ring buffer of the last `seq_len`
**baseline z-scored** feature vectors. Working in z-scores is what lets one
model serve nodes whose absolute firing rates differ 20-fold; it is also
exactly the reference the feature-worker maintains at runtime, so training
and inference see identical inputs.

`mu`/`sd` are a final global standardisation of those z-scores, carried over
from training. Until the ring buffer fills the detector reports baseline,
matching the z-score detector's warm-up so the lead-time comparison is fair.
`stress = 1 - P(baseline)`.
"""
mutable struct GRUDetector
    net::Any
    mu::Vector{Float32}
    sd::Vector{Float32}
    seq_len::Int
    sustain::Int
    stress_thresh::Float64
    n_baseline::Int
    clip::Float64
    base::Dict{UInt32,NodeBaseline}
    buffers::Dict{UInt32,Matrix{Float32}}   # (nf, seq_len) ring, oldest first
    filled::Dict{UInt32,Int}
    runs::Dict{UInt32,Int}
    last::Dict{UInt32,Int}
end
GRUDetector(net, mu, sd; seq_len::Int = SEQ_LEN, sustain::Int = 2,
            stress_thresh::Real = 0.5, n_baseline::Int = 30, clip::Real = 10.0) =
    GRUDetector(net, Float32.(mu), Float32.(sd), seq_len, sustain, Float64(stress_thresh),
                n_baseline, Float64(clip),
                Dict{UInt32,NodeBaseline}(), Dict{UInt32,Matrix{Float32}}(),
                Dict{UInt32,Int}(), Dict{UInt32,Int}(), Dict{UInt32,Int}())

function reset!(d::GRUDetector)
    empty!(d.base); empty!(d.buffers); empty!(d.filled); empty!(d.runs); empty!(d.last)
    return d
end

function observe!(d::GRUDetector, node_id::Integer, x::AbstractVector{<:Real}, t_ms::Integer)
    key = UInt32(node_id)
    nf = length(d.mu)
    b = get!(d.base, key) do
        NodeBaseline(nf, Int64(t_ms))
    end

    # --- ring buffer of RAW features: shift left, append newest.
    # Buffering raw (not z-scored) values means the warm-up windows can be
    # re-scored against the finished baseline, so the GRU becomes live at the
    # same window as the z-score detector rather than seq_len windows later.
    buf = get!(d.buffers, key) do
        zeros(Float32, nf, d.seq_len)
    end
    @inbounds for t in 1:(d.seq_len - 1), j in 1:nf
        buf[j, t] = buf[j, t + 1]
    end
    @inbounds for j in 1:nf
        buf[j, d.seq_len] = isfinite(x[j]) ? Float32(x[j]) : 0.0f0
    end
    filled = get(d.filled, key, 0) + 1
    d.filled[key] = filled

    # --- per-node baseline, frozen after n_baseline windows
    if b.n < d.n_baseline
        _update_baseline!(b, x)
        return (0.0, STATE_BASELINE)
    end
    filled < d.seq_len && return (0.0, STATE_BASELINE)

    sig = _sigma(b)
    zbuf = Matrix{Float32}(undef, nf, d.seq_len)
    @inbounds for j in 1:nf
        s = max(sig[j], 0.05 * max(abs(b.mean[j]), 1e-6), 1e-6)
        (b.mean[j] == 0 && s <= 1e-6) && (s = 1.0)
        for t in 1:d.seq_len
            v = (buf[j, t] - b.mean[j]) / s
            z = isfinite(v) ? clamp(v, -d.clip, d.clip) : 0.0
            zbuf[j, t] = Float32((z - d.mu[j]) / d.sd[j])
        end
    end

    p = vec(seq_forward(d.net, reshape(zbuf, nf, 1, d.seq_len)))
    stress = 1.0 - Float64(p[1])
    cls = argmax(p) - 1
    over = stress >= d.stress_thresh && cls != STATE_BASELINE
    d.runs[key] = over ? get(d.runs, key, 0) + 1 : 0
    state = get(d.runs, key, 0) >= d.sustain ? cls : STATE_BASELINE
    d.last[key] = state
    return (clamp(stress, 0.0, 1.0), state)
end

"""
    stress_state(d, node_id, x, t_ms)

Alias of `observe!` for call sites that read better that way.
"""
stress_state(d, node_id, x, t_ms) = observe!(d, node_id, x, t_ms)

"""
    load_gru_artifact(path) -> (net, mu, sd, meta)

Load `gru.bson` written by `ml/scripts/train.jl`. The Flux parameter tree is
restored into a freshly built `GRUNet` so inference matches training.
"""
function load_gru_artifact(path::AbstractString)
    isfile(path) || error("GRU artifact not found: $path")
    data = BSON.load(path, @__MODULE__)
    haskey(data, :model_state) || error("GRU artifact missing :model_state")
    haskey(data, :mu) || error("GRU artifact missing :mu")
    haskey(data, :sd) || error("GRU artifact missing :sd")
    net = build_gru()
    Flux.loadmodel!(net, data[:model_state])
    meta = get(data, :meta, Dict{String,Any}())
    return (net, data[:mu], data[:sd], meta)
end

"""
    load_gru_detector(path; kwargs...) -> GRUDetector

`load_gru_artifact` plus a live `GRUDetector`. Keyword args are forwarded
(`seq_len`, `sustain`, `stress_thresh`, `n_baseline`, `clip`).
"""
function load_gru_detector(path::AbstractString; kwargs...)
    net, mu, sd, _ = load_gru_artifact(path)
    return GRUDetector(net, mu, sd; kwargs...)
end

"""
    load_transformer_artifact(path) -> (net, mu, sd, meta)

Load `transformer.bson` written by `ml/scripts/train.jl --arch transformer`.
"""
function load_transformer_artifact(path::AbstractString)
    isfile(path) || error("Transformer artifact not found: $path")
    data = BSON.load(path, @__MODULE__)
    haskey(data, :model_state) || error("Transformer artifact missing :model_state")
    haskey(data, :mu) || error("Transformer artifact missing :mu")
    haskey(data, :sd) || error("Transformer artifact missing :sd")
    net = build_transformer()
    Flux.loadmodel!(net, data[:model_state])
    meta = get(data, :meta, Dict{String,Any}())
    return (net, data[:mu], data[:sd], meta)
end

function load_transformer_detector(path::AbstractString; kwargs...)
    net, mu, sd, _ = load_transformer_artifact(path)
    return GRUDetector(net, mu, sd; kwargs...)
end

end # module Models
