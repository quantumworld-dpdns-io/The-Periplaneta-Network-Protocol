# features.jl -- pure feature functions shared by the feature-worker and by
# ml/ (training, eval). No Kafka, no IO: everything here is unit-testable.
#
# Feature vector order is FIXED by proto/WIRE.md section 4.3:
#   1 rate_hz  2 isi_mean_ms  3 isi_cv  4 burst_index
#   5 fano     6 rate_slope_hz_per_s    7 band_power (raw only, else 0)
#   8 snr_db   (raw only, else 0)

module Features

using Statistics

export detect_spikes, feature_names, N_FEATURES,
       rate_hz, isi_mean_ms, isi_cv, burst_index, fano_factor, rate_slope,
       band_power, snr_db, features_from_spikes, features_from_counts,
       features_from_raw

const N_FEATURES = 8
const FEATURE_NAMES = ("rate_hz", "isi_mean_ms", "isi_cv", "burst_index",
                       "fano", "rate_slope_hz_per_s", "band_power", "snr_db")
feature_names() = FEATURE_NAMES

# ------------------------------------------------------- spike detection ----
"""
    detect_spikes(x, fs; k=4.5, refractory_ms=1.5, polarity=:neg) -> Vector{Float64}

Threshold + refractory spike detector for a raw extracellular trace `x`
sampled at `fs` Hz. The threshold is `k` times the robust noise estimate
sigma = median(|x|)/0.6745 (Quiroga et al. 2004). Returns spike times in
seconds relative to the first sample.

`polarity` is `:neg` (extracellular spikes are usually negative-going),
`:pos`, or `:both`. Within one refractory period only the largest-|amplitude|
crossing is kept, so a spike is never double-counted.
"""
function detect_spikes(x::AbstractVector{<:Real}, fs::Real;
                       k::Real = 4.5, refractory_ms::Real = 1.5,
                       polarity::Symbol = :neg)
    n = length(x)
    n == 0 && return Float64[]
    xf = Float64.(x)
    sigma = median(abs.(xf)) / 0.6745
    sigma > 0 || (sigma = std(xf))
    sigma > 0 || return Float64[]
    thr = k * sigma
    refr = max(1, round(Int, refractory_ms * 1e-3 * fs))

    # score: how far past threshold, in the chosen polarity
    score = polarity === :neg  ? -xf :
            polarity === :pos  ?  xf :
            polarity === :both ? abs.(xf) :
            throw(ArgumentError("polarity must be :neg, :pos or :both"))

    times = Float64[]
    i = 1
    @inbounds while i <= n
        if score[i] >= thr
            # walk to the local peak inside the crossing, then lock out `refr`
            j = i
            best = i
            while j <= n && score[j] >= thr
                score[j] > score[best] && (best = j)
                j += 1
            end
            push!(times, (best - 1) / fs)
            i = max(j, best + refr)
        else
            i += 1
        end
    end
    return times
end

# ------------------------------------------------------ scalar features -----
"""rate_hz(spikes, dur_s) -- mean firing rate over the window."""
rate_hz(spikes::AbstractVector{<:Real}, dur_s::Real) =
    dur_s > 0 ? length(spikes) / dur_s : 0.0

"""isis_ms(spikes) -- inter-spike intervals in milliseconds."""
isis_ms(spikes::AbstractVector{<:Real}) =
    length(spikes) < 2 ? Float64[] : Float64.(diff(collect(spikes))) .* 1000

isi_mean_ms(spikes::AbstractVector{<:Real}) =
    (v = isis_ms(spikes); isempty(v) ? 0.0 : mean(v))

"""
    isi_cv(spikes)

Coefficient of variation of the ISI distribution. 1.0 for a Poisson
process, < 1 for regular firing, > 1 for bursty firing. 0.0 when fewer
than 3 spikes make the estimate meaningless.
"""
function isi_cv(spikes::AbstractVector{<:Real})
    v = isis_ms(spikes)
    length(v) < 2 && return 0.0
    m = mean(v)
    m > 0 || return 0.0
    return std(v; corrected = false) / m
end

"""
    burst_index(spikes; short_ms=10.0)

Fraction of ISIs shorter than `short_ms` -- a simple, robust burstiness
measure that does not require a burst-detection algorithm.
"""
function burst_index(spikes::AbstractVector{<:Real}; short_ms::Real = 10.0)
    v = isis_ms(spikes)
    isempty(v) && return 0.0
    return count(<(short_ms), v) / length(v)
end

"""
    fano_factor(counts)

Variance / mean of per-bin spike counts. 1.0 for Poisson.
"""
function fano_factor(counts::AbstractVector{<:Real})
    length(counts) < 2 && return 0.0
    m = mean(counts)
    m > 0 || return 0.0
    return var(counts; corrected = false) / m
end

"""
    rate_slope(rates_hz, dt_s)

Least-squares slope (Hz per second) of a short history of per-bin rates.
Negative values mean the node is shutting down -- the earliest toxin signal.
"""
function rate_slope(rates_hz::AbstractVector{<:Real}, dt_s::Real)
    n = length(rates_hz)
    (n < 2 || dt_s <= 0) && return 0.0
    t = (0:(n - 1)) .* dt_s
    tbar = mean(t)
    ybar = mean(rates_hz)
    den = sum((t .- tbar) .^ 2)
    den > 0 || return 0.0
    return sum((t .- tbar) .* (rates_hz .- ybar)) / den
end

"""
    biquad(x, b0, b1, b2, a1, a2)

Direct-form-I biquad, applied forward then backward (zero phase, 4th order
effective). Coefficients are already normalised by a0.
"""
function biquad(x::Vector{Float64}, b0, b1, b2, a1, a2)
    n = length(x)
    y = similar(x)
    x1 = 0.0; x2 = 0.0; y1 = 0.0; y2 = 0.0
    @inbounds for i in 1:n
        xi = x[i]
        yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2 = x1; x1 = xi; y2 = y1; y1 = yi
        y[i] = yi
    end
    x1 = 0.0; x2 = 0.0; y1 = 0.0; y2 = 0.0
    @inbounds for i in n:-1:1
        xi = y[i]
        yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2 = x1; x1 = xi; y2 = y1; y1 = yi
        y[i] = yi
    end
    return y
end

"RBJ 2-pole low-pass at fc (Butterworth Q = 1/sqrt(2))."
function lowpass(x::Vector{Float64}, fs::Real, fc::Real)
    w = 2pi * fc / fs
    cw = cos(w); sw = sin(w)
    alpha = sw / sqrt(2)
    a0 = 1 + alpha
    return biquad(x, (1 - cw) / 2 / a0, (1 - cw) / a0, (1 - cw) / 2 / a0,
                  -2cw / a0, (1 - alpha) / a0)
end

"RBJ 2-pole high-pass at fc (Butterworth Q = 1/sqrt(2))."
function highpass(x::Vector{Float64}, fs::Real, fc::Real)
    w = 2pi * fc / fs
    cw = cos(w); sw = sin(w)
    alpha = sw / sqrt(2)
    a0 = 1 + alpha
    return biquad(x, (1 + cw) / 2 / a0, -(1 + cw) / a0, (1 + cw) / 2 / a0,
                  -2cw / a0, (1 - alpha) / a0)
end

"""
    band_power(x, fs; lo=300.0, hi=3000.0)

Fraction of the trace's total power that falls in the [lo, hi] Hz band --
the spike band for extracellular recordings. Implemented as a zero-phase
Butterworth band-pass (2-pole HP + 2-pole LP, forward-backward, so no FFTW
dependency) followed by a power ratio, giving a scale-free value in [0, 1].
Toxin/radiation states change the spectral balance well before the rate
collapses, which is what makes this feature useful.
"""
function band_power(x::AbstractVector{<:Real}, fs::Real; lo::Real = 300.0, hi::Real = 3000.0)
    n = length(x)
    n < 16 && return 0.0
    xf = Float64.(x)
    xf .-= mean(xf)
    total = sum(abs2, xf)
    total > 0 || return 0.0
    nyq = fs / 2
    hi_c = min(Float64(hi), nyq * 0.95)
    lo_c = clamp(Float64(lo), 0.0, hi_c * 0.99)
    y = xf
    lo_c > 0 && (y = highpass(y, fs, lo_c))
    hi_c < nyq * 0.94 && (y = lowpass(y, fs, hi_c))
    return clamp(sum(abs2, y) / total, 0.0, 1.0)
end

"""
    snr_db(x, spikes, fs; win_ms=1.5)

Spike-amplitude SNR in dB: 20*log10(peak spike amplitude / robust noise
sigma). Returns 0.0 when there are no spikes.
"""
function snr_db(x::AbstractVector{<:Real}, spikes::AbstractVector{<:Real}, fs::Real;
                win_ms::Real = 1.5)
    (isempty(x) || isempty(spikes)) && return 0.0
    xf = Float64.(x)
    sigma = median(abs.(xf)) / 0.6745
    sigma > 0 || return 0.0
    half = max(1, round(Int, win_ms * 1e-3 * fs / 2))
    peaks = Float64[]
    n = length(xf)
    for t in spikes
        i = clamp(round(Int, t * fs) + 1, 1, n)
        a = max(1, i - half); b = min(n, i + half)
        push!(peaks, maximum(abs, @view xf[a:b]))
    end
    isempty(peaks) && return 0.0
    return 20 * log10(median(peaks) / sigma)
end

# ------------------------------------------------------- feature vectors ----
"""
    features_from_spikes(spikes, dur_s; bin_s=0.05, rate_history=Float64[],
                         hist_dt_s=1.0) -> Vector{Float64}

The 8-element WIRE section 4.3 vector from spike times alone. Indices 7 and 8
(band_power, snr_db) are 0 -- they need the raw waveform.
`rate_history` is the recent per-second rate history for the slope; if
empty the slope is estimated from within-window sub-bins.
"""
function features_from_spikes(spikes::AbstractVector{<:Real}, dur_s::Real;
                              bin_s::Real = 0.05,
                              rate_history::AbstractVector{<:Real} = Float64[],
                              hist_dt_s::Real = 1.0)
    f = zeros(Float64, N_FEATURES)
    dur_s <= 0 && return f
    nb = max(1, round(Int, dur_s / bin_s))
    counts = zeros(Int, nb)
    for t in spikes
        b = clamp(floor(Int, t / bin_s) + 1, 1, nb)
        counts[b] += 1
    end
    f[1] = rate_hz(spikes, dur_s)
    f[2] = isi_mean_ms(spikes)
    f[3] = isi_cv(spikes)
    f[4] = burst_index(spikes)
    f[5] = fano_factor(counts)
    f[6] = length(rate_history) >= 2 ? rate_slope(rate_history, hist_dt_s) :
                                       rate_slope(counts ./ bin_s, bin_s)
    return f
end

"""
    features_from_counts(counts, bin_ms; rate_history=Float64[], hist_dt_s=1.0)

The 8-element vector for a *virtual twin*, whose only observable is a
sequence of binned spike counts (WIRE section 4.2). ISI statistics are
reconstructed by placing `counts[i]` spikes uniformly inside bin `i`, which
preserves rate, Fano factor and the coarse ISI scale.
"""
function features_from_counts(counts::AbstractVector{<:Real}, bin_ms::Real;
                              rate_history::AbstractVector{<:Real} = Float64[],
                              hist_dt_s::Real = 1.0)
    f = zeros(Float64, N_FEATURES)
    nb = length(counts)
    nb == 0 && return f
    bin_s = bin_ms / 1000
    dur_s = nb * bin_s
    spikes = Float64[]
    for (i, c) in enumerate(counts)
        ci = Int(c)
        ci <= 0 && continue
        t0 = (i - 1) * bin_s
        for j in 1:ci
            push!(spikes, t0 + (j - 0.5) * bin_s / ci)
        end
    end
    f[1] = rate_hz(spikes, dur_s)
    f[2] = isi_mean_ms(spikes)
    f[3] = isi_cv(spikes)
    f[4] = burst_index(spikes)
    f[5] = fano_factor(counts)
    f[6] = length(rate_history) >= 2 ? rate_slope(rate_history, hist_dt_s) :
                                       rate_slope(Float64.(counts) ./ bin_s, bin_s)
    return f
end

"""
    features_from_raw(x, fs; k=4.5, refractory_ms=1.5, rate_history=Float64[])

The full 8-element vector for a HIL node: detect spikes in the raw trace,
then fill in band_power and snr_db as well.
"""
function features_from_raw(x::AbstractVector{<:Real}, fs::Real;
                           k::Real = 4.5, refractory_ms::Real = 1.5,
                           rate_history::AbstractVector{<:Real} = Float64[],
                           hist_dt_s::Real = 1.0)
    spikes = detect_spikes(x, fs; k = k, refractory_ms = refractory_ms)
    dur_s = length(x) / fs
    f = features_from_spikes(spikes, dur_s; rate_history = rate_history, hist_dt_s = hist_dt_s)
    f[7] = band_power(x, fs)
    f[8] = snr_db(x, spikes, fs)
    return f
end

end # module Features
