# dataset.jl -- turn simulated twin traces into the exact feature windows the
# feature-worker produces at runtime, then into GRU training sequences.
#
# Keeping this in one place is what makes train/eval honest: the windows the
# model is trained on are computed by the same `Features.features_from_counts`
# the live worker calls on `swarm.bins`.

module Dataset

using Random, Statistics

export NodeWindows, windows_of, build_windows, build_sequences, window_labels,
       baseline_stats, zscore_windows, HISTORY_S, N_BASELINE

"How many past 1 s rates feed the rate_slope feature."
const HISTORY_S = 10

"""
Windows used for a node's own baseline reference (Models.BASELINE_S seconds
at 1 window/s). Absolute firing rates vary 20-fold across units, so a model
fed raw rates cannot tell "this node slowed down" from "this node is slow".
Both detectors therefore work in per-node z-scores against this window --
the feature-worker maintains exactly the same reference online.
"""
const N_BASELINE = 30

"""
    NodeWindows(X, y, t_s, kind, onset_s, symptom_s)

`X` is `(8, n_windows)` -- one WIRE 4.3 feature vector per 1 s window --
`y` the per-window true class, `t_s[i]` the window's end time in seconds.
"""
struct NodeWindows
    X::Matrix{Float64}
    y::Vector{Int}
    t_s::Vector{Float64}
    kind::Int
    onset_s::Float64
    symptom_s::Float64
end

"""
    windows_of(trace, features_from_counts; bin_ms=100) -> NodeWindows

Slide a 1 s window (step 1 s, as WIRE 4.3 specifies) over one node's binned
counts. `rate_slope` uses a rolling `HISTORY_S`-second rate history, exactly
like the worker's ring buffer.
"""
function windows_of(trace, features_from_counts::Function; bin_ms::Integer = 100)
    bins_per_s = 1000 ÷ bin_ms
    counts = trace.counts
    nwin = length(counts) ÷ bins_per_s
    X = zeros(Float64, 8, nwin)
    y = zeros(Int, nwin)
    ts = zeros(Float64, nwin)
    hist = Float64[]
    # The class only becomes true once the intervention has actually reached
    # the node (onset + diffusion delay); before that it is still baseline.
    for w in 1:nwin
        a = (w - 1) * bins_per_s + 1
        b = a + bins_per_s - 1
        c = @view counts[a:b]
        f = features_from_counts(c, bin_ms; rate_history = hist, hist_dt_s = 1.0)
        X[:, w] = f
        ts[w] = Float64(w)
        y[w] = (trace.kind != 0 && w >= trace.onset_s) ? trace.kind : 0
        push!(hist, sum(c) / (bins_per_s * bin_ms / 1000))
        length(hist) > HISTORY_S && popfirst!(hist)
    end
    return NodeWindows(X, y, ts, trace.kind, trace.onset_s, trace.symptom_s)
end

"""
    build_windows(traces, features_from_counts) -> Vector{NodeWindows}
"""
build_windows(traces, features_from_counts::Function; bin_ms::Integer = 100) =
    [windows_of(tr, features_from_counts; bin_ms = bin_ms) for tr in traces]

"""
    baseline_stats(X; n=N_BASELINE) -> (mu, sd)

Per-feature mean and std over a node's first `n` windows. `sd` is floored
relative to `|mu|` so a feature that never moved at baseline cannot blow the
z-score up, and features that are identically zero (band_power and snr_db on
binned twin data) get `sd = 1` and stay at z = 0.
"""
function baseline_stats(X::AbstractMatrix{<:Real}; n::Integer = N_BASELINE)
    m = min(n, size(X, 2))
    B = @view X[:, 1:m]
    mu = vec(mean(B; dims = 2))
    sd = m > 1 ? vec(std(B; dims = 2)) : ones(size(X, 1))
    for j in eachindex(sd)
        floorv = 0.05 * max(abs(mu[j]), 1e-6)
        sd[j] = (isfinite(sd[j]) && sd[j] > floorv) ? sd[j] : max(floorv, 1e-6)
        (mu[j] == 0 && sd[j] <= 1e-6) && (sd[j] = 1.0)
    end
    return (mu, sd)
end

"""
    zscore_windows(nw; n_baseline=N_BASELINE, clip=10.0) -> NodeWindows

Rewrite a node's feature matrix as z-scores against its own baseline window.
"""
function zscore_windows(nw::NodeWindows; n_baseline::Integer = N_BASELINE, clip::Real = 10.0)
    mu, sd = baseline_stats(nw.X; n = n_baseline)
    Z = similar(nw.X)
    @inbounds for t in axes(nw.X, 2), j in axes(nw.X, 1)
        z = (nw.X[j, t] - mu[j]) / sd[j]
        Z[j, t] = isfinite(z) ? clamp(z, -clip, clip) : 0.0
    end
    return NodeWindows(Z, nw.y, nw.t_s, nw.kind, nw.onset_s, nw.symptom_s)
end

"""
    build_sequences(nws, seq_len) -> (X, y)

`X` is `(8, n_seq, seq_len)` (the layout Flux's GRU wants: features x batch x
time) and `y` the class of each sequence's **last** window, which is the one
the detector has to call in real time.
"""
function build_sequences(nws::AbstractVector{NodeWindows}, seq_len::Integer)
    seqs = Vector{Matrix{Float64}}()
    labels = Int[]
    for nw in nws
        n = size(nw.X, 2)
        for t in seq_len:n
            push!(seqs, nw.X[:, (t - seq_len + 1):t])
            push!(labels, nw.y[t])
        end
    end
    isempty(seqs) && return (zeros(Float64, 8, 0, seq_len), Int[])
    X = Array{Float64,3}(undef, 8, length(seqs), seq_len)
    for (i, s) in enumerate(seqs)
        X[:, i, :] = s
    end
    return (X, labels)
end

end # module Dataset
