#!/usr/bin/env julia
# make_template.jl -- emit firmware/hil-node/include/template.h, the spike-train
# template the ESP32-S3 HIL firmware replays at 10 kHz.
#
#   julia --project=ml ml/scripts/make_template.jl [--seconds 10] [--max-kb 250] [--out path]
#
# The firmware does NOT store a rendered waveform. It stores the *ingredients*
# (see firmware/hil-node/tools/make_template.py and src/playback.cpp) and
# renders  y[n] = sum_k amp_k * wave[n - t_k] + pink_noise[n]  in the timer ISR:
#
#   TPL_SPIKE_T[]    spike onset sample indices, sorted, < TPL_LEN - TPL_WAVE_N
#   TPL_SPIKE_AMP[]  per-spike amplitude scale, uint8, 128 == 1.0
#   TPL_WAVE[]       biphasic spike waveform, int16, 0.1 uV units
#   TPL_NOISE_RMS    pink-noise RMS the device synthesises at run time (0.1 uV units)
#
# This script emits exactly that symbol set, so the header it writes is a
# drop-in replacement for the placeholder produced by the Python tool. Spike
# times come from a REAL unit in the Zenodo recordings (data/fetch.sh), or from
# the clearly labelled synthetic fallback when no data is on disk -- the
# `// Source:` line in the header says which.

using Printf, Random, Statistics, Dates

include(joinpath(@__DIR__, "..", "src", "CockroachML.jl"))
using .CockroachML
using .CockroachML.DataIO
using .CockroachML.Features

const ROOT        = CockroachML.REPO_ROOT
const DEFAULT_OUT = joinpath(ROOT, "firmware", "hil-node", "include", "template.h")
const RATE_HZ     = 10_000
const NOISE_RMS   = 22          # 2.2 uV RMS pink floor, 0.1 uV units -- a clean tetrode recording
const SPIKE_AMP   = 780.0       # 78 uV negative peak, 0.1 uV units
const AMP_JITTER  = (0.82, 0.36) # per-spike scale = 0.82 + 0.36 * U(0,1), matches the old renderer
const SEED        = 42

"""
    biphasic(fs; neg_ms=0.30, pos_ms=0.55, ratio=0.42) -> Vector{Float64}

Unit-amplitude biphasic extracellular action potential: a fast negative
Gaussian (the Na+ current) followed by a slower, smaller positive rebound
(the K+ current), which is the shape a high-pass-filtered electrode sees.
Peak value is exactly -1; the first sample is shifted to 0 so the waveform
starts on the baseline like the firmware expects.
"""
function biphasic(fs::Real; neg_ms::Real = 0.30, pos_ms::Real = 0.55, ratio::Real = 0.42)
    total_ms = 2.2
    n = round(Int, total_ms * 1e-3 * fs)
    t = ((0:(n - 1)) .- 0.28n) ./ fs .* 1000        # ms, peak near the front
    neg = exp.(-(t ./ (neg_ms / 2)) .^ 2)
    pos = exp.(-((t .- 0.75) ./ (pos_ms / 2)) .^ 2)
    w = -neg .+ ratio .* pos
    w .-= w[1]
    return w ./ maximum(abs, w)
end

"""
    pick_train(units, dur_s) -> unit or nothing

Choose the unit whose spike train best fills `dur_s`: the longest recording
with a firing rate near 12 Hz, which reads well on an oscilloscope.
"""
function pick_train(units, dur_s::Real)
    best = nothing
    best_score = -Inf
    for u in units
        length(u.spikes) < 30 && continue
        span = u.spikes[end] - u.spikes[1]
        span <= 0 && continue
        rate = length(u.spikes) / span
        score = min(span, dur_s) - abs(rate - 12.0)
        if score > best_score
            best_score = score
            best = u
        end
    end
    return best
end

"""
    build_ingredients(; seconds, seed) -> NamedTuple

Tile the chosen unit's real spike times (preserving its ISIs) to cover
`seconds`, then quantise everything to the firmware's fixed-point formats.
"""
function build_ingredients(; seconds::Real = 10.0, seed::Integer = SEED)
    units, source, is_real = DataIO.load_units(ROOT, Features.detect_spikes)
    u = pick_train(units, seconds)
    u === nothing && error("no usable spike train for the template")

    rng = MersenneTwister(seed)
    len = round(Int, seconds * RATE_HZ)

    wave = biphasic(RATE_HZ)
    wave_i16 = Int16.(clamp.(round.(Int, wave .* SPIKE_AMP), -32768, 32767))
    nw = length(wave_i16)

    st = u.spikes .- u.spikes[1]
    span = st[end]
    idx = Int[]
    off = 0.0
    while off < seconds
        for t in st
            tt = off + t
            tt >= seconds && break
            push!(idx, round(Int, tt * RATE_HZ))
        end
        off += span + 0.05
    end
    idx = unique(sort(filter(i -> 0 <= i < len - nw, idx)))

    amps = UInt8[clamp(round(Int, (AMP_JITTER[1] + AMP_JITTER[2] * rand(rng)) * 128), 32, 255)
                 for _ in idx]

    return (len = len, wave = wave_i16, spike_t = UInt32.(idx), spike_amp = amps,
            noise_rms = NOISE_RMS, seed = seed, unit = u, source = source, is_real = is_real)
end

function c_array(io::IO, name::AbstractString, ctype::AbstractString, values; per_line::Int = 16)
    @printf(io, "static const %s %s[%d] = {\n", ctype, name, length(values))
    for i in 1:per_line:length(values)
        chunk = values[i:min(i + per_line - 1, length(values))]
        println(io, "    ", join(string.(Int.(chunk)), ", "), i + per_line - 1 < length(values) ? "," : "")
    end
    println(io, "};")
end

"""
    write_header(path, ing) -> nbytes

Same layout as firmware/hil-node/tools/make_template.py::emit_header so the
firmware, its host tests and docs/WS-A.md need no changes.
"""
function write_header(path::AbstractString, ing::NamedTuple)
    mkpath(dirname(path))
    nspk = length(ing.spike_t)
    open(path, "w") do io
        println(io, "// GENERATED FILE -- do not edit. Regenerate with:")
        println(io, "//   julia --project=ml ml/scripts/make_template.jl   (just template)")
        println(io, "// Generated ", Dates.format(now(UTC), dateformat"yyyy-mm-dd HH:MM\\Z"))
        @printf(io, "// Source: %s unit %s -- %s\n", ing.is_real ? "REAL recording," : "SYNTHETIC fallback,",
                ing.unit.id, ing.source)
        println(io, "//")
        println(io, "// Spike-train template for the HIL playback engine (see playback.h).")
        println(io, "// Units: samples @ TPL_RATE_HZ; amplitudes in 0.1 uV (value/10 = uV).")
        println(io, "#pragma once")
        println(io, "#include <stdint.h>")
        println(io)
        @printf(io, "#define TPL_RATE_HZ    %d\n", RATE_HZ)
        @printf(io, "#define TPL_LEN        %d      // loop length in samples (%.1f s)\n", ing.len, ing.len / RATE_HZ)
        @printf(io, "#define TPL_WAVE_N     %d\n", length(ing.wave))
        @printf(io, "#define TPL_N_SPIKES   %d\n", nspk)
        @printf(io, "#define TPL_NOISE_RMS  %d          // pink-noise RMS, 0.1 uV units\n", ing.noise_rms)
        @printf(io, "#define TPL_MEAN_RATE_HZ %.2ff\n", nspk * RATE_HZ / ing.len)
        @printf(io, "#define TPL_SEED       %d\n", ing.seed)
        println(io)
        @printf(io, "// Biphasic spike waveform, 0.1 uV units, %.1f ms.\n", length(ing.wave) / RATE_HZ * 1e3)
        c_array(io, "TPL_WAVE", "int16_t", ing.wave)
        println(io)
        println(io, "// Spike onset sample indices, sorted, all < TPL_LEN - TPL_WAVE_N.")
        c_array(io, "TPL_SPIKE_T", "uint32_t", ing.spike_t; per_line = 12)
        println(io)
        println(io, "// Per-spike amplitude scale, 128 == 1.0.")
        c_array(io, "TPL_SPIKE_AMP", "uint8_t", ing.spike_amp; per_line = 24)
    end
    return filesize(path)
end

function main(args = ARGS)
    seconds = 10.0
    max_kb = 250.0
    out = DEFAULT_OUT
    i = 1
    while i <= length(args)
        if args[i] == "--seconds" && i < length(args)
            seconds = parse(Float64, args[i + 1]); i += 2
        elseif args[i] == "--max-kb" && i < length(args)
            max_kb = parse(Float64, args[i + 1]); i += 2
        elseif args[i] == "--out" && i < length(args)
            out = args[i + 1]; i += 2
        else
            i += 1
        end
    end

    ing = build_ingredients(; seconds = seconds)
    nbytes = write_header(out, ing)
    while nbytes > max_kb * 1024 && seconds > 1.0
        seconds = round(seconds * (max_kb * 1024) / nbytes * 0.97; digits = 2)
        @printf("  header was %.1f KB (> %.0f KB budget) -- regenerating at %.2f s\n", nbytes / 1024, max_kb, seconds)
        ing = build_ingredients(; seconds = seconds)
        nbytes = write_header(out, ing)
    end

    @printf("wrote %s: %d spikes over %.1f s (%.1f Hz), wave %d samples, noise %d LSB rms, %.1f KB\n",
            relpath(out, ROOT), length(ing.spike_t), ing.len / RATE_HZ,
            length(ing.spike_t) * RATE_HZ / ing.len, length(ing.wave), ing.noise_rms, nbytes / 1024)
    println("source: ", ing.source, ing.is_real ? "" : "  [SYNTHETIC]")
    return 0
end

if abspath(PROGRAM_FILE) == @__FILE__
    main()
end
