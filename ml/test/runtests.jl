# runtests.jl -- `julia --project=ml -e 'using Pkg; Pkg.test()'`
#
# Two things are worth testing here and they are both cheap:
#   1. the feature functions, against synthetic trains whose rate/CV/Fano are
#      known analytically;
#   2. the binary codecs, byte-for-byte against proto/WIRE.md section 4.
# Everything else (models, sim) is exercised end-to-end by train.jl/eval.jl.

using Test, Random, Statistics, Distributions

include(joinpath(@__DIR__, "..", "src", "CockroachML.jl"))
using .CockroachML
using .CockroachML.Wire
using .CockroachML.Features
using .CockroachML.Sim
using .CockroachML.Models
using .CockroachML.Dataset
import Flux

"Homogeneous Poisson spike train of mean rate `rate` over `dur` seconds."
function poisson_train(rate::Real, dur::Real, rng::AbstractRNG)
    sp = Float64[]
    t = 0.0
    while true
        t += -log(rand(rng)) / rate
        t > dur && break
        push!(sp, t)
    end
    return sp
end

"Gamma renewal train with an absolute refractory period."
function gamma_train(shape::Real, rate::Real, refr::Real, n::Integer, rng::AbstractRNG)
    return cumsum([refr + rand(rng, Gamma(shape, 1 / rate)) for _ in 1:n])
end

@testset "CockroachML" begin

# ======================================================== feature functions ===
@testset "features on synthetic trains" begin
    rng = MersenneTwister(20260909)
    dur = 120.0

    @testset "rate and ISI mean" begin
        for rate in (5.0, 20.0, 50.0)
            sp = poisson_train(rate, dur, rng)
            f = features_from_spikes(sp, dur)
            @test f[1] ≈ rate rtol = 0.15                 # rate_hz
            @test f[2] ≈ 1000 / rate rtol = 0.15          # isi_mean_ms
        end
    end

    @testset "CV: Poisson ~ 1, regular ~ 0" begin
        sp = poisson_train(25.0, 300.0, rng)
        @test isi_cv(sp) ≈ 1.0 atol = 0.12

        regular = collect(0.04:0.04:dur)
        @test isi_cv(regular) ≈ 0.0 atol = 1e-9
        @test rate_hz(regular, dur) ≈ 25.0 rtol = 0.02
    end

    @testset "CV of a gamma renewal is 1/sqrt(shape)" begin
        for shape in (0.5, 2.0, 8.0)
            st = gamma_train(shape, shape * 20.0, 0.0, 20_000, rng)
            @test isi_cv(st) ≈ 1 / sqrt(shape) rtol = 0.08
        end
    end

    @testset "Fano factor: Poisson ~ 1, deterministic ~ 0" begin
        sp = poisson_train(30.0, 600.0, rng)
        nb = 600
        counts = zeros(Int, nb)
        for t in sp
            counts[clamp(floor(Int, t) + 1, 1, nb)] += 1
        end
        @test fano_factor(counts) ≈ 1.0 atol = 0.15
        @test fano_factor(fill(7, 100)) ≈ 0.0 atol = 1e-12
    end

    @testset "burst_index counts short ISIs" begin
        # 10 spikes 2 ms apart (9 short ISIs), then 10 spikes 200 ms apart
        sp = vcat(collect(0.0:0.002:0.018), collect(0.5:0.2:2.3))
        bi = burst_index(sp; short_ms = 10.0)
        @test 0.4 < bi < 0.6
        @test burst_index(collect(0.0:0.5:10.0); short_ms = 10.0) == 0.0
    end

    @testset "rate_slope recovers a known ramp" begin
        rates = collect(10.0:2.0:30.0)          # +2 Hz per 1 s step
        @test rate_slope(rates, 1.0) ≈ 2.0 atol = 1e-9
        @test rate_slope(fill(12.0, 10), 1.0) ≈ 0.0 atol = 1e-12
        @test rate_slope([5.0], 1.0) == 0.0
    end

    @testset "band_power is a band energy fraction" begin
        fs = 10_000.0
        n = 10_000
        t = (0:(n - 1)) ./ fs
        @test band_power(sin.(2pi * 1000 .* t), fs; lo = 300.0, hi = 3000.0) > 0.9
        @test band_power(sin.(2pi * 4200 .* t), fs; lo = 300.0, hi = 3000.0) < 0.05
        @test band_power(sin.(2pi * 100 .* t),  fs; lo = 300.0, hi = 3000.0) < 0.05
        @test 0.0 <= band_power(randn(MersenneTwister(1), n), fs) <= 1.0
    end

    @testset "detect_spikes finds planted spikes and honours refractoriness" begin
        fs = 10_000.0
        n = 20_000
        x = randn(MersenneTwister(5), n) .* 10.0
        planted = collect(0.1:0.05:1.9)
        wave = [0.0, -20.0, -60.0, -110.0, -60.0, 10.0, 40.0, 25.0, 8.0, 0.0]
        for tt in planted
            i = round(Int, tt * fs)
            for (d, a) in zip(-3:6, wave)
                x[i + d] += a
            end
        end
        found = detect_spikes(x, fs)
        @test length(found) == length(planted)
        @test maximum(abs.(found .- planted)) < 2e-3      # within 2 ms
        @test minimum(diff(found)) >= 1.5e-3 - 1e-9       # refractory respected

        # pure noise must not produce a storm of detections
        @test length(detect_spikes(randn(MersenneTwister(6), n) .* 10.0, fs)) < 0.02 * n

        f = features_from_raw(x, fs)
        @test f[1] ≈ length(planted) / (n / fs) rtol = 0.05
        @test f[8] > 12.0                                  # snr_db, planted 11x sigma
        @test f[7] > 0.0                                   # band_power filled in
    end

    @testset "features_from_counts preserves rate and Fano" begin
        counts = [3, 0, 2, 5, 1, 0, 4, 2, 2, 1]
        f = features_from_counts(counts, 100.0)
        @test f[1] ≈ sum(counts) / 1.0                     # 10 x 100 ms = 1 s
        @test f[5] ≈ fano_factor(counts)
        @test f[7] == 0.0 && f[8] == 0.0                   # raw-only features
    end

    @testset "degenerate inputs do not throw" begin
        @test features_from_spikes(Float64[], 1.0) == zeros(8)
        @test features_from_counts(Int[], 100.0) == zeros(8)
        @test isi_cv([1.0]) == 0.0
        @test detect_spikes(Float64[], 10_000.0) == Float64[]
        @test detect_spikes(zeros(100), 10_000.0) == Float64[]
    end
end

# ============================================================== ISI fitting ===
@testset "fit_isi_params recovers known gamma parameters" begin
    rng = MersenneTwister(7)
    for (shape, rate, refr_ms) in ((2.5, 20.0, 3.0), (1.2, 30.0, 2.0), (4.0, 50.0, 1.5))
        st = gamma_train(shape, rate, refr_ms / 1000, 20_000, rng)
        p = fit_isi_params(st)
        @test p.shape ≈ shape rtol = 0.15
        @test p.rate_hz ≈ rate rtol = 0.15
        # The location (refractory) parameter of a shifted gamma is only
        # weakly identified -- the more so the higher the shape, since the
        # density near the shift then vanishes and the smallest observed ISI
        # carries little information. Sub-millisecond accuracy is all that is
        # claimed, and all the twin model needs.
        @test p.refractory_ms ≈ refr_ms atol = 1.0
        @test p.n_spikes == 20_000
        # the fitted mean ISI must match the truth much more tightly than
        # any single parameter does
        @test p.refractory_ms / 1000 + p.shape / p.rate_hz ≈
              refr_ms / 1000 + shape / rate rtol = 0.05
    end

    # too few spikes -> documented exponential fallback, no exception
    p = fit_isi_params([0.1, 0.2, 0.3])
    @test p.shape == 1.0
    @test isfinite(p.rate_hz)
end

@testset "make_twin_params schema" begin
    rng = MersenneTwister(11)
    fits = [fit_isi_params(gamma_train(2.0, 20.0, 0.002, 500, rng)) for _ in 1:5]
    d = make_twin_params(fits; source = "test")
    @test Set(keys(d)) == Set(["units", "source", "fitted_at"])
    @test d["source"] == "test"
    @test length(d["units"]) == 5
    for u in d["units"]
        @test Set(keys(u)) == Set(["shape", "rate_hz", "refractory_ms"])
        @test u["shape"] > 0 && u["rate_hz"] > 0 && u["refractory_ms"] >= 0
    end
    @test length(make_twin_params(fits; source = "x", max_units = 2)["units"]) == 2

    mktempdir() do dir
        path = joinpath(dir, "twin_params.json")
        write_twin_params(path, fits; source = "roundtrip")
        back = load_twin_params(path)
        @test length(back) == 5
        @test back[1].shape ≈ d["units"][1]["shape"]
    end
end

# ============================================================== wire codecs ===
@testset "codec round-trips (WIRE section 4)" begin

    @testset "HilRawFrame layout" begin
        samples = Int16[0, 1, -1, 32767, -32768, 300, -4000]
        f = HilRawFrame(MODE_TOXIN, UInt32(0xFFFF0001), UInt32(7),
                        UInt64(1234567890), UInt32(10_000), samples)
        b = encode(f)
        @test length(b) == 32 + 2 * length(samples)
        @test b[1:4] == UInt8['H', 'I', 'L', 'R']
        @test b[5] == 0x01
        @test b[6] == MODE_TOXIN
        @test b[7] == 0x00 && b[8] == 0x00                 # reserved
        @test b[31] == 0x00 && b[32] == 0x00               # reserved

        g = decode_hil_raw(b)
        @test g.mode == f.mode
        @test g.node_id == f.node_id
        @test g.seq == f.seq
        @test g.ts_us == f.ts_us
        @test g.sample_rate_hz == f.sample_rate_hz
        @test g.samples == f.samples

        # the documented 100 ms @ 10 kHz frame is exactly 2032 bytes
        @test length(encode(HilRawFrame(MODE_BASELINE, UInt32(0xFFFF0000), UInt32(0),
                                        UInt64(0), UInt32(10_000), zeros(Int16, 1000)))) == 2032
    end

    @testset "SwarmBinFrame layout" begin
        counts = UInt16[0, 1, 2, 65535, 7]
        f = SwarmBinFrame(UInt16(3), UInt32(3000), UInt32(length(counts)),
                          UInt16(100), UInt64(1757400000000), counts)
        b = encode(f)
        @test length(b) == 32 + 2 * length(counts)
        @test b[1:4] == UInt8['S', 'W', 'B', 'N']
        @test b[5] == 0x01

        g = decode_swarm_bin(b)
        @test g.shard == f.shard
        @test g.first_node_id == f.first_node_id
        @test g.n_nodes == f.n_nodes
        @test g.bin_ms == f.bin_ms
        @test g.t_ms == f.t_ms
        @test g.counts == f.counts

        # a full 1000-node shard frame
        @test length(encode(SwarmBinFrame(UInt16(0), UInt32(0), UInt32(1000),
                                          UInt16(100), UInt64(0), zeros(UInt16, 1000)))) == 2032
    end

    @testset "FeatureFrame layout" begin
        recs = [NodeRecord(0.5, MODE_TOXIN, Float64.(1:8)),
                NodeRecord(0.0, MODE_BASELINE, zeros(8)),
                NodeRecord(1.0, MODE_COLLAPSED, fill(-2.5, 8))]
        f = FeatureFrame(UInt16(0xFFFF), UInt32(0xFFFF0000), UInt32(length(recs)),
                         UInt64(1757400003000), UInt32(1000), recs)
        b = encode(f)
        @test length(b) == 32 + 40 * length(recs)          # NodeRecord is 40 bytes
        @test b[1:4] == UInt8['F', 'E', 'A', 'T']
        @test b[5] == 0x01
        @test b[6] == UInt8(8)                             # n_feat

        g = decode_feature(b)
        @test g.shard == f.shard
        @test g.first_node_id == f.first_node_id
        @test g.n_nodes == f.n_nodes
        @test g.t_ms == f.t_ms
        @test g.window_ms == f.window_ms
        for (a, c) in zip(g.records, recs)
            @test a.stress ≈ c.stress
            @test a.state == c.state
            @test collect(a.features) ≈ collect(c.features)
        end
    end

    @testset "little-endian on the wire" begin
        # 0x0102 must serialise as 0x02 0x01 regardless of host endianness
        b = encode(SwarmBinFrame(UInt16(0x0102), UInt32(0x0A0B0C0D), UInt32(1),
                                 UInt16(100), UInt64(0), UInt16[0]))
        @test b[7] == 0x02 && b[8] == 0x01                 # shard
        @test b[9:12] == UInt8[0x0D, 0x0C, 0x0B, 0x0A]     # first_node_id
    end

    @testset "malformed frames are rejected" begin
        good = encode(SwarmBinFrame(UInt16(1), UInt32(0), UInt32(2), UInt16(100),
                                    UInt64(0), UInt16[1, 2]))
        bad_magic = copy(good); bad_magic[1] = UInt8('X')
        @test_throws ArgumentError decode_swarm_bin(bad_magic)
        bad_ver = copy(good); bad_ver[5] = 0x02
        @test_throws ArgumentError decode_swarm_bin(bad_ver)
        @test_throws ArgumentError decode_swarm_bin(good[1:20])          # short header
        @test_throws ArgumentError decode_swarm_bin(good[1:33])          # truncated body
        @test_throws ArgumentError decode_hil_raw(good)                  # wrong magic
    end

    @testset "node identity helpers (WIRE section 1)" begin
        @test node_hex(0xFFFF0000) == "ffff0000"
        @test node_hex(80125) == "000138fd"
        @test shard_of(80125) == 80
        @test shard_of(0) == 0
        @test shard_of(999) == 0
        @test shard_of(1000) == 1
        @test shard_of(0xFFFF0000) == 0xFFFF
        @test shard_of(0xFFFF0001) == 0xFFFF
    end

    @testset "alert JSON (WIRE section 7)" begin
        a = Wire.alert_json(1757400003000, 80125, 0, 1, 0.8312, "gru")
        @test a["t_ms"] == 1757400003000
        @test a["node_id"] == 80125
        @test a["node_hex"] == "000138fd"
        @test a["prev_state"] == 0 && a["state"] == 1
        @test a["stress"] ≈ 0.8312
        @test a["detector"] == "gru"
    end
end

# =================================================================== models ===
@testset "detectors" begin
    @testset "z-score stays silent on a stationary node and fires on a collapse" begin
        rng = MersenneTwister(3)
        d = ZScoreDetector()
        base = [12.0, 83.0, 1.0, 0.1, 1.0, 0.0, 0.0, 0.0]
        alarms = 0
        for w in 1:60
            x = base .+ randn(rng, 8) .* [0.6, 4.0, 0.05, 0.01, 0.05, 0.2, 0, 0]
            _, st = Models.observe!(d, 1, x, w * 1000)
            w > 31 && st != 0 && (alarms += 1)
        end
        @test alarms == 0

        for w in 61:75      # rate collapses to near zero
            x = [0.4, 900.0, 2.4, 0.0, 3.0, -4.0, 0.0, 0.0] .+ randn(rng, 8) .* 0.05
            _, st = Models.observe!(d, 1, x, w * 1000)
            st != 0 && (alarms += 1)
        end
        @test alarms > 0
    end

    @testset "detectors are silent during warm-up" begin
        rng = MersenneTwister(4)
        d = ZScoreDetector()
        for w in 1:30
            _, st = Models.observe!(d, 9, randn(rng, 8) .* 3 .+ 10, w * 1000)
            @test st == 0
        end
    end

    @testset "GRU forward shapes" begin
        net = Models.build_gru()
        x = randn(Float32, 8, 5, Models.SEQ_LEN)
        @test size(net(x)) == (4, 5)
        p = Models.gru_forward(net, x)
        @test size(p) == (4, 5)
        @test all(≈(1.0f0), sum(p; dims = 1))
    end

    @testset "GRUDetector obeys the same warm-up and emits valid stress" begin
        net = Models.build_gru()
        mu = zeros(Float32, 8); sd = ones(Float32, 8)
        d = Models.GRUDetector(net, mu, sd)
        rng = MersenneTwister(8)
        stresses = Float64[]
        for w in 1:80
            s, st = Models.observe!(d, 2, randn(rng, 8) .* 2 .+ 10, w * 1000)
            w <= Models.SEQ_LEN && @test st == 0
            push!(stresses, s)
        end
        @test all(0.0 .<= stresses .<= 1.0)
        @test any(stresses .> 0)          # becomes live once warmed up
    end

    @testset "Transformer forward shapes and one AD step" begin
        net = Models.build_transformer()
        x = randn(Float32, 8, 4, Models.SEQ_LEN)
        @test size(net(x)) == (4, 4)
        p = Models.seq_forward(net, x)
        @test size(p) == (4, 4)
        @test all(≈(1.0f0), sum(p; dims = 1))
        y = zeros(Float32, 4, 4)
        y[1, :] .= 1
        opt = Flux.setup(Flux.Adam(1.0f-2), net)
        loss, gs = Flux.withgradient(net) do m
            Flux.logitcrossentropy(m(x), y)
        end
        Flux.update!(opt, net, gs[1])
        @test isfinite(loss)
    end

    @testset "TransformerDetector warm-up matches GRU" begin
        net = Models.build_transformer()
        mu = zeros(Float32, 8); sd = ones(Float32, 8)
        d = Models.GRUDetector(net, mu, sd)
        for w in 1:Models.SEQ_LEN
            _, st = Models.observe!(d, 3, randn(Float32, 8) .+ 10, w * 1000)
            @test st == 0
        end
    end

    @testset "load_transformer_detector fail-closes on a missing artifact" begin
        @test_throws ErrorException Models.load_transformer_detector(joinpath(tempdir(), "missing-tx.bson"))
    end
end

# ====================================================== simulation dynamics ===
@testset "twin simulation" begin
    p = TwinParam(2.0, 20.0, 2.0)

    @testset "baseline nodes never collapse" begin
        for s in 1:3
            tr = simulate_node(p, 0, 120.0; rng = MersenneTwister(s))
            @test isnan(tr.symptom_s)
            pre  = sum(tr.counts[1:300]) / 30
            post = sum(tr.counts[901:1200]) / 30
            @test pre > 1.0
            @test post ≈ pre rtol = 0.35
        end
    end

    @testset "toxin suppresses the rate and raises symptom_onset" begin
        tr = simulate_node(p, 1, 150.0; rng = MersenneTwister(21),
                           onset_s = 40.0, intensity = 0.98, spread_s = 1.0)
        pre  = sum(tr.counts[1:300]) / 30
        post = sum(tr.counts[1201:1500]) / 30
        @test post < 0.25 * pre
        @test isfinite(tr.symptom_s)
        @test tr.symptom_s > 40.0
    end

    @testset "radiation bursts before it suppresses" begin
        # Single realisations of a renewal process are noisy, so average the
        # burst and the late suppression over several seeds.
        pres = Float64[]; bursts = Float64[]; lates = Float64[]
        for s in 1:8
            tr = simulate_node(p, 3, 150.0; rng = MersenneTwister(100 + s),
                               onset_s = 40.0, intensity = 0.95, spread_s = 0.0)
            push!(pres,   sum(tr.counts[1:300]) / 30)     #  0- 30 s, pre-onset
            push!(bursts, sum(tr.counts[401:410]) / 1)    # 40- 41 s, acute burst
            push!(lates,  sum(tr.counts[601:700]) / 10)   # 60- 70 s, suppression
        end
        @test mean(bursts) > 1.15 * mean(pres)            # acute burst
        @test mean(lates)  < 0.85 * mean(pres)            # then noisy suppression
    end

    @testset "drug keeps the node alive" begin
        tr = simulate_node(p, 2, 150.0; rng = MersenneTwister(23),
                           onset_s = 40.0, intensity = 0.9, spread_s = 0.0)
        @test isnan(tr.symptom_s)
        post = sum(tr.counts[1201:1500]) / 30
        @test post > 0.5 * (sum(tr.counts[1:300]) / 30)
    end

    @testset "seeded runs are reproducible" begin
        a = simulate_node(p, 1, 60.0; rng = MersenneTwister(99))
        b = simulate_node(p, 1, 60.0; rng = MersenneTwister(99))
        @test a.counts == b.counts
        @test isequal(a.symptom_s, b.symptom_s)
    end
end

# ================================================== windowing / sequencing ====
@testset "windows and sequences" begin
    p = TwinParam(2.0, 20.0, 2.0)
    tr = simulate_node(p, 1, 90.0; rng = MersenneTwister(31), onset_s = 40.0)
    nw = Dataset.windows_of(tr, Features.features_from_counts)
    @test size(nw.X, 1) == 8
    @test size(nw.X, 2) == 90
    @test length(nw.y) == 90
    @test all(nw.y[1:39] .== 0)          # pre-onset windows are baseline
    @test nw.y[end] == 1

    z = Dataset.zscore_windows(nw)
    @test size(z.X) == size(nw.X)
    @test all(isfinite, z.X)
    @test all(abs.(z.X) .<= 10.0 + 1e-9)
    @test abs(mean(z.X[1, 1:Dataset.N_BASELINE])) < 1e-9   # baseline is centred

    X, y = Dataset.build_sequences([z], Models.SEQ_LEN)
    @test size(X) == (8, 90 - Models.SEQ_LEN + 1, Models.SEQ_LEN)
    @test length(y) == size(X, 2)
    @test X[:, 1, :] == z.X[:, 1:Models.SEQ_LEN]
end

end # testset CockroachML
