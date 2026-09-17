module Worker

using ..Features
using ..Wire
using ..Models

export FeatureEngine, accept_swarm!, accept_hil!, alert_payload, resolve_engine,
       repo_root, default_gru_path, default_transformer_path

const WINDOW_MS = 1000
const BASELINE_WINDOWS = 20

"""Repo root from `services/feature-worker/src` (three levels up)."""
repo_root() = normpath(joinpath(@__DIR__, "..", "..", ".."))

default_gru_path() = get(ENV, "GRU_MODEL",
                         joinpath(repo_root(), "ml", "artifacts", "gru.bson"))
default_transformer_path() = get(ENV, "TRANSFORMER_MODEL",
                                 joinpath(repo_root(), "ml", "artifacts", "transformer.bson"))

mutable struct Baseline
    n::Int
    mean_rate::Float64
    m2_rate::Float64
    last_state::UInt8
    candidate_state::UInt8
    candidate_run::Int
end
Baseline() = Baseline(0, 0.0, 0.0, MODE_BASELINE, MODE_BASELINE, 0)

mutable struct FeatureEngine
    bins::Dict{UInt16,Vector{SwarmBinFrame}}
    baselines::Dict{UInt32,Baseline}
    raw::Dict{UInt32,Vector{HilRawFrame}}
    detector::String
    requested::String
    fallback_reason::String
    gru::Any
end

function FeatureEngine(; detector::AbstractString = "zscore",
                       requested::AbstractString = detector,
                       fallback_reason::AbstractString = "",
                       gru = nothing)
    return FeatureEngine(Dict{UInt16,Vector{SwarmBinFrame}}(),
                         Dict{UInt32,Baseline}(),
                         Dict{UInt32,Vector{HilRawFrame}}(),
                         String(detector), String(requested),
                         String(fallback_reason), gru)
end

"""
    resolve_engine() -> FeatureEngine

`DETECTOR=zscore|gru|transformer` (default `transformer`). Missing weights
fail-close: transformer → gru → z-score. `fallback_reason` records why.
"""
function resolve_engine()
    requested = lowercase(strip(get(ENV, "DETECTOR", "transformer")))
    requested == "zscore" && return FeatureEngine(; detector = "zscore", requested = "zscore")

    function try_load(name::AbstractString, path::AbstractString, loader)
        if !isfile(path)
            @warn "DETECTOR=$name but artifact missing" path
            return nothing
        end
        try
            det = loader(path)
            @info "loaded $name detector" path
            return det
        catch err
            @warn "$name load failed" exception = (err, catch_backtrace())
            return nothing
        end
    end

    if requested == "transformer"
        tx = try_load("transformer", default_transformer_path(), Models.load_transformer_detector)
        tx !== nothing && return FeatureEngine(; detector = "transformer", requested = "transformer", gru = tx)
        gru = try_load("gru", default_gru_path(), Models.load_gru_detector)
        gru !== nothing && return FeatureEngine(; detector = "gru", requested = "transformer",
                                                fallback_reason = "missing_or_bad_transformer", gru = gru)
        @warn "transformer and GRU unavailable; falling back to z-score"
        return FeatureEngine(; detector = "zscore", requested = "transformer",
                             fallback_reason = "missing_artifact")
    end

    if requested == "gru"
        gru = try_load("gru", default_gru_path(), Models.load_gru_detector)
        gru !== nothing && return FeatureEngine(; detector = "gru", requested = "gru", gru = gru)
        @warn "DETECTOR=gru but artifact missing; falling back to z-score"
        return FeatureEngine(; detector = "zscore", requested = "gru",
                             fallback_reason = "missing_artifact")
    end

    @warn "unknown DETECTOR; using z-score" requested
    return FeatureEngine(; detector = "zscore", requested = requested,
                         fallback_reason = "unknown_detector")
end

function _update_rate_baseline!(b::Baseline, rate::Float64)
    b.n += 1
    delta = rate - b.mean_rate
    b.mean_rate += delta / b.n
    b.m2_rate += delta * (rate - b.mean_rate)
    return b
end

function score!(engine::FeatureEngine, node_id::UInt32, f::Vector{Float64},
                t_ms::Integer = 0)
    b = get!(engine.baselines, node_id, Baseline())
    rate = f[1]
    warming = b.n < BASELINE_WINDOWS
    warming && _update_rate_baseline!(b, rate)

    # Twin rates are clamped to >= 0.5 Hz by swarm-gen. Twenty consecutive
    # silent startup windows therefore indicate a node that was already
    # collapsed before this worker joined, not a valid zero-rate baseline.
    if !warming && b.mean_rate < 0.1 && rate < 0.1
        previous = b.last_state
        b.last_state = MODE_COLLAPSED
        return (1.0, MODE_COLLAPSED, previous)
    end

    if engine.gru !== nothing
        if warming
            Models.observe!(engine.gru, node_id, f, t_ms)
            return (0.0, MODE_BASELINE, MODE_BASELINE)
        end
        previous = b.last_state
        stress, state = Models.observe!(engine.gru, node_id, f, t_ms)
        st = UInt8(state)
        b.last_state = st
        return (Float64(stress), st, previous)
    end

    warming && return (0.0, MODE_BASELINE, MODE_BASELINE)

    variance = b.n > 1 ? b.m2_rate / (b.n - 1) : 0.0
    scale = max(sqrt(max(variance, 0.0)), 0.15 * abs(b.mean_rate), 1.0)
    z = abs(rate - b.mean_rate) / scale
    stress = clamp(1 - exp(-0.5 * z^2), 0.0, 1.0)
    proposed = if stress < 0.7
        MODE_BASELINE
    elseif rate < 0.1 && stress > 0.99
        MODE_COLLAPSED
    elseif rate < 0.8 * b.mean_rate
        MODE_TOXIN
    elseif rate > 1.35 * b.mean_rate
        MODE_RADIATION
    else
        MODE_DRUG
    end
    previous = b.last_state
    if proposed == b.candidate_state
        b.candidate_run += 1
    else
        b.candidate_state = proposed
        b.candidate_run = 1
    end
    b.candidate_run >= 3 && (b.last_state = proposed)
    return (stress, b.last_state, previous)
end

function accept_swarm!(engine::FeatureEngine, frame::SwarmBinFrame)
    frames = get!(engine.bins, frame.shard, SwarmBinFrame[])
    if !isempty(frames) &&
       (frames[1].first_node_id != frame.first_node_id ||
        frames[1].n_nodes != frame.n_nodes ||
        frames[1].bin_ms != frame.bin_ms)
        empty!(frames)
    end
    push!(frames, frame)
    needed = max(1, cld(WINDOW_MS, Int(frame.bin_ms)))
    length(frames) < needed && return nothing
    length(frames) > needed && deleteat!(frames, 1:(length(frames) - needed))

    records = Vector{NodeRecord}(undef, Int(frame.n_nodes))
    transitions = Tuple{UInt32,UInt8,UInt8,Float64}[]
    counts = Vector{UInt16}(undef, needed)
    for i in eachindex(records)
        for j in 1:needed
            counts[j] = frames[j].counts[i]
        end
        f = features_from_counts(counts, frame.bin_ms)
        node_id = frame.first_node_id + UInt32(i - 1)
        stress, state, previous = score!(engine, node_id, f, Int64(frame.t_ms))
        records[i] = NodeRecord(stress, state, f)
        state != previous && push!(transitions, (node_id, previous, state, stress))
    end
    empty!(frames)
    out = FeatureFrame(frame.shard, frame.first_node_id, frame.n_nodes,
                       frame.t_ms, UInt32(WINDOW_MS), records)
    return (out, transitions)
end

function accept_hil!(engine::FeatureEngine, frame::HilRawFrame)
    frames = get!(engine.raw, frame.node_id, HilRawFrame[])
    if !isempty(frames) && frames[1].sample_rate_hz != frame.sample_rate_hz
        empty!(frames)
    end
    push!(frames, frame)
    samples_needed = Int(frame.sample_rate_hz)
    available = sum(length(f.samples) for f in frames)
    available < samples_needed && return nothing

    samples = Vector{Int16}(undef, available)
    offset = 1
    for raw in frames
        copyto!(samples, offset, raw.samples, 1, length(raw.samples))
        offset += length(raw.samples)
    end
    empty!(frames)
    f = features_from_raw(samples, frame.sample_rate_hz)
    t_ms = Int64(frame.ts_us ÷ 1000)
    stress, detected, previous = score!(engine, frame.node_id, f, t_ms)
    # Device mode is authoritative for HIL while stress remains feature-based.
    state = frame.mode <= MODE_RADIATION ? frame.mode : detected
    baseline = engine.baselines[frame.node_id]
    baseline.last_state = state
    record = NodeRecord(stress, state, f)
    out = FeatureFrame(HIL_SHARD, frame.node_id, UInt32(1), frame.ts_us ÷ 1000,
                       UInt32(WINDOW_MS), [record])
    transitions = state == previous ? Tuple{UInt32,UInt8,UInt8,Float64}[] :
                  [(frame.node_id, previous, state, stress)]
    return (out, transitions)
end

function alert_payload(t_ms::Integer, node_id::UInt32, previous::UInt8,
                       state::UInt8, stress::Real,
                       detector::AbstractString = "zscore")
    det = detector in ("gru", "transformer") ? String(detector) : "zscore"
    return """{"t_ms":$(Int64(t_ms)),"node_id":$(UInt64(node_id)),"node_hex":"$(node_hex(node_id))","prev_state":$(Int(previous)),"state":$(Int(state)),"stress":$(round(Float64(stress); digits=4)),"detector":"$det"}"""
end

end
