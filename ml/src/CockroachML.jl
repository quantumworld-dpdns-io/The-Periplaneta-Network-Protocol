# CockroachML.jl -- WS-C: ISI fitting, twin parameter export, simulation and
# the two detectors (z-score/Hotelling-T2 and Flux GRU).
#
# Layering:
#   Features / Wire   come from services/feature-worker/src (single source of
#                     truth; the feature index order is pinned by WIRE 4.3)
#   sim.jl            Julia reimplementation of the twin dynamics, so ml/ never
#                     depends on a WS-B binary
#   models.jl         the two detectors

module CockroachML

using Statistics, StatsBase, Random, Distributions, Dates, Printf, JSON3
using SpecialFunctions: digamma, trigamma, loggamma

const REPO_ROOT = normpath(joinpath(@__DIR__, "..", ".."))
const FW_SRC    = joinpath(REPO_ROOT, "services", "feature-worker", "src")

include(joinpath(FW_SRC, "wire.jl"))
include(joinpath(FW_SRC, "features.jl"))
using .Wire
using .Features

include("dataio.jl")
include("sim.jl")
include("dataset.jl")
include("models.jl")

using .DataIO
using .Sim
using .Dataset
using .Models

export fit_isi_params, make_twin_params, write_twin_params, load_twin_params,
       MAX_UNITS, REPO_ROOT, Wire, Features, DataIO, Sim, Dataset, Models

"Hard cap on units written to twin_params.json (WS-B reads this file at boot)."
const MAX_UNITS = 500

# ------------------------------------------------------------ ISI fitting ----
"""
    fit_isi_params(spike_times; min_spikes=20) -> NamedTuple

Maximum-likelihood fit of a **gamma renewal process with an absolute
refractory period** to one unit's spike train.

The model is `ISI = r + G`, where `r` is the refractory period and
`G ~ Gamma(shape, 1/rate)`. All three parameters are fitted by maximum
likelihood: given `r`, the shape and rate have closed-form MLEs (the shape
solves `log(k) - digamma(k) = log(mean x) - mean(log x)`, seeded with
Minka's approximation and polished by Newton; the rate is then `k/mean(x)`),
so only `r` is searched, by golden section on the profile log-likelihood
over `[0, 0.95 * min(ISI)]`. On synthetic trains this recovers the true
parameters to within a few percent (see `ml/test/runtests.jl`).

Returns `(; shape, rate_hz, refractory_ms, n_spikes, duration_s,
mean_rate_hz, isi_cv)`. `rate_hz` is the gamma **rate parameter** in Hz, not
the firing rate -- the mean firing rate is reported separately as
`mean_rate_hz`.

Degenerate trains (fewer than `min_spikes` spikes, or no ISI variability)
fall back to an exponential fit (`shape = 1`), which is the maximum-entropy
choice given only a rate.
"""
function fit_isi_params(spike_times::AbstractVector{<:Real};
                        min_spikes::Integer = 20)
    st = sort(Float64.(collect(spike_times)))
    n = length(st)
    dur = n >= 2 ? st[end] - st[1] : 0.0
    mean_rate = dur > 0 ? (n - 1) / dur : 0.0

    if n < max(3, min_spikes) || dur <= 0
        return (shape = 1.0, rate_hz = max(mean_rate, 1e-3),
                refractory_ms = 1.0, n_spikes = n, duration_s = dur,
                mean_rate_hz = mean_rate, isi_cv = 1.0)
    end

    isi = diff(st)
    isi = isi[isi .> 0]
    length(isi) < 3 && return (shape = 1.0, rate_hz = max(mean_rate, 1e-3),
                               refractory_ms = 1.0, n_spikes = n, duration_s = dur,
                               mean_rate_hz = mean_rate, isi_cv = 1.0)

    cv = std(isi; corrected = false) / mean(isi)

    # The refractory period is chosen by *profile likelihood*: for each
    # candidate r the gamma shape/rate have closed-form (Newton) MLEs given
    # the shifted ISIs, so only the single parameter r has to be searched.
    # The upper bound sits just below the smallest observed ISI, which is
    # what an absolute refractory period means.
    hi = min(0.95 * minimum(isi), 0.05)
    lo = 0.0
    if !(hi > lo)
        return (shape = 1.0, rate_hz = max(mean_rate, 1e-3),
                refractory_ms = max(minimum(isi), 0.0005) * 1000,
                n_spikes = n, duration_s = dur, mean_rate_hz = mean_rate, isi_cv = cv)
    end

    # golden-section maximisation of the profile log-likelihood
    invphi = (sqrt(5) - 1) / 2
    a, b = lo, hi
    c = b - invphi * (b - a)
    d = a + invphi * (b - a)
    fc = _profile_loglik(isi, c)
    fd = _profile_loglik(isi, d)
    for _ in 1:60
        if fc > fd
            b, d, fd = d, c, fc
            c = b - invphi * (b - a)
            fc = _profile_loglik(isi, c)
        else
            a, c, fc = c, d, fd
            d = a + invphi * (b - a)
            fd = _profile_loglik(isi, d)
        end
        (b - a) < 1e-7 && break
    end
    r = clamp((a + b) / 2, 0.0, hi)

    k, rate = _gamma_mle(isi .- r)
    r_ms = clamp(r * 1000, 0.1, 50.0)

    return (shape = k, rate_hz = rate, refractory_ms = r_ms,
            n_spikes = n, duration_s = dur, mean_rate_hz = mean_rate,
            isi_cv = cv)
end

"""
    _gamma_mle(x) -> (shape, rate)

MLE of a gamma distribution from positive samples. The shape solves
`log(k) - digamma(k) = log(mean x) - mean(log x)`; seeded with Minka's
approximation and polished by Newton. The rate is then `k / mean(x)`.
"""
function _gamma_mle(x::AbstractVector{<:Real})
    xp = filter(v -> v > 1e-12, x)
    length(xp) < 3 && return (1.0, 1.0)
    xbar = mean(xp)
    s = log(xbar) - mean(log.(xp))
    k = 1.0
    if s > 0 && isfinite(s)
        k = (3 - s + sqrt((3 - s)^2 + 24s)) / (12s)
        for _ in 1:64
            g = log(k) - digamma(k) - s
            dg = 1 / k - trigamma(k)
            abs(dg) < 1e-12 && break
            knew = k - g / dg
            (knew <= 0 || !isfinite(knew)) && break
            done = abs(knew - k) < 1e-10 * max(k, 1.0)
            k = knew
            done && break
        end
    end
    k = clamp(k, 0.05, 50.0)
    return (k, k / xbar)
end

"Profile log-likelihood of the shifted-gamma model at refractory period `r`."
function _profile_loglik(isi::AbstractVector{<:Real}, r::Real)
    x = isi .- r
    all(>(1e-12), x) || return -Inf
    k, rate = _gamma_mle(x)
    (isfinite(k) && isfinite(rate) && rate > 0) || return -Inf
    m = length(x)
    # sum log pdf(Gamma(k, 1/rate), x)
    return m * (k * log(rate) - loggamma(k)) + (k - 1) * sum(log, x) - rate * sum(x)
end

# ------------------------------------------------------- twin_params.json ----
"""
    make_twin_params(units; source="", max_units=MAX_UNITS) -> Dict

Build the `twin_params.json` payload consumed by WS-B's `swarm-gen`.
`units` is an iterable of `fit_isi_params` NamedTuples (or of raw spike-time
vectors, which are fitted on the way in).

Schema (kept deliberately small -- at most `max_units` entries):

```json
{"units":[{"shape":1.6,"rate_hz":12.4,"refractory_ms":2.1,"mean_rate_hz":6.9}],
 "source":"...", "fitted_at":"2026-09-09T07:00:00Z"}
```

Field semantics (this bit the Rust consumer once, so it is spelled out):
`rate_hz` is the **gamma rate parameter** `1/theta` of the ISI distribution;
`mean_rate_hz` is the **firing rate** `1 / (refractory_s + shape / rate_hz)`.
Consumers that need spikes per second must read `mean_rate_hz` (or derive it
from the other three) -- never use `rate_hz` as a firing rate.
"""
function make_twin_params(units; source::AbstractString = "", max_units::Integer = MAX_UNITS)
    fitted = Any[]
    for u in units
        p = u isa NamedTuple ? u : fit_isi_params(u)
        (isfinite(p.shape) && isfinite(p.rate_hz) && p.rate_hz > 0) || continue
        mean_rate = 1 / (Float64(p.refractory_ms) / 1000 + Float64(p.shape) / Float64(p.rate_hz))
        push!(fitted, Dict(
            "shape"         => round(Float64(p.shape); digits = 4),
            "rate_hz"       => round(Float64(p.rate_hz); digits = 4),
            "refractory_ms" => round(Float64(p.refractory_ms); digits = 4),
            "mean_rate_hz"  => round(mean_rate; digits = 4),
        ))
        length(fitted) >= max_units && break
    end
    return Dict{String,Any}(
        "units"     => fitted,
        "source"    => String(source),
        "fitted_at" => Dates.format(now(UTC), dateformat"yyyy-mm-dd\THH:MM:SS\Z"),
    )
end

"""
    write_twin_params(path, units; source="") -> Dict

`make_twin_params` + write JSON to `path` (creating parent directories).
"""
function write_twin_params(path::AbstractString, units; source::AbstractString = "",
                           max_units::Integer = MAX_UNITS)
    d = make_twin_params(units; source = source, max_units = max_units)
    mkpath(dirname(path))
    open(path, "w") do io
        JSON3.pretty(io, d)
    end
    return d
end

"""
    load_twin_params(path) -> Vector{NamedTuple}

Read `twin_params.json` back as `(; shape, rate_hz, refractory_ms, mean_rate_hz)`
tuples; `mean_rate_hz` is derived when an older file lacks it.
"""
function load_twin_params(path::AbstractString)
    d = JSON3.read(read(path, String))
    return [(shape = Float64(u.shape), rate_hz = Float64(u.rate_hz),
             refractory_ms = Float64(u.refractory_ms),
             mean_rate_hz = haskey(u, :mean_rate_hz) ? Float64(u.mean_rate_hz) :
                            1 / (Float64(u.refractory_ms) / 1000 + Float64(u.shape) / Float64(u.rate_hz)))
            for u in d.units]
end

end # module CockroachML
