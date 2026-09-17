using Test

include("../src/wire.jl")
include("../src/features.jl")
include(get(ENV, "MODELS_JL",
            joinpath(@__DIR__, "..", "..", "..", "ml", "src", "models.jl")))
include("../src/worker.jl")

using .Wire
using .Worker
using .Models

@testset "feature worker" begin
    @testset "aggregates ten 100 ms swarm bins into one feature frame" begin
        engine = FeatureEngine()
        result = nothing
        for i in 1:10
            frame = SwarmBinFrame(UInt16(0), UInt32(0), UInt32(2), UInt16(100),
                                  UInt64(i * 100), UInt16[1, 2])
            result = accept_swarm!(engine, frame)
        end

        @test result !== nothing
        output, transitions = result
        @test output.window_ms == 1000
        @test output.records[1].features[1] == 10.0f0
        @test output.records[2].features[1] == 20.0f0
        @test all(record -> record.stress == 0.0f0, output.records)
        @test isempty(transitions)
        @test engine.detector == "zscore"
    end

    @testset "uses the HIL device mode and preserves low-amplitude spikes" begin
        engine = FeatureEngine()
        result = nothing
        for seq in 0:9
            samples = fill(Int16(30), 1000)
            samples[101] = Int16(-500)
            frame = HilRawFrame(MODE_TOXIN, HIL_BASE, UInt32(seq),
                                UInt64(seq * 100_000), UInt32(10_000), samples)
            result = accept_hil!(engine, frame)
        end

        @test result !== nothing
        output, transitions = result
        @test output.records[1].state == MODE_TOXIN
        @test output.records[1].features[1] > 0
        @test length(transitions) == 1
    end

    @testset "does not learn an already-collapsed twin as a zero baseline" begin
        engine = FeatureEngine()
        result = nothing
        for window in 1:21, bin in 1:10
            frame = SwarmBinFrame(UInt16(1), UInt32(1000), UInt32(1), UInt16(100),
                                  UInt64((window * 10 + bin) * 100), UInt16[0])
            result = accept_swarm!(engine, frame)
        end

        @test result !== nothing
        output, transitions = result
        @test output.records[1].stress == 1.0f0
        @test output.records[1].state == MODE_COLLAPSED
        @test length(transitions) == 1
    end

    @testset "alert detector field follows the active path" begin
        z = alert_payload(1, UInt32(1), 0x00, 0x01, 0.83, "zscore")
        g = alert_payload(1, UInt32(1), 0x00, 0x01, 0.83, "gru")
        @test occursin("\"detector\":\"zscore\"", z)
        @test occursin("\"detector\":\"gru\"", g)
    end

    @testset "DETECTOR=gru with missing artifact fail-closes to z-score" begin
        old_d = get(ENV, "DETECTOR", nothing)
        old_p = get(ENV, "GRU_MODEL", nothing)
        ENV["DETECTOR"] = "gru"
        ENV["GRU_MODEL"] = joinpath(tempdir(), "no-such-gru-$(rand(UInt32)).bson")
        try
            engine = resolve_engine()
            @test engine.detector == "zscore"
            @test engine.requested == "gru"
            @test engine.fallback_reason == "missing_artifact"
            @test engine.gru === nothing
            payload = alert_payload(1, UInt32(1), 0x00, 0x01, 0.5, engine.detector)
            @test occursin("\"detector\":\"zscore\"", payload)
        finally
            old_d === nothing ? delete!(ENV, "DETECTOR") : (ENV["DETECTOR"] = old_d)
            old_p === nothing ? delete!(ENV, "GRU_MODEL") : (ENV["GRU_MODEL"] = old_p)
        end
    end

    @testset "in-memory GRU path names alerts gru and stays silent in warm-up" begin
        net = Models.build_gru()
        gru = Models.GRUDetector(net, zeros(Float32, 8), ones(Float32, 8);
                                 n_baseline = 5, seq_len = 5)
        engine = FeatureEngine(; detector = "gru", gru = gru)
        result = nothing
        for i in 1:10
            frame = SwarmBinFrame(UInt16(2), UInt32(50), UInt32(1), UInt16(100),
                                  UInt64(i * 100), UInt16[12])
            result = accept_swarm!(engine, frame)
        end
        @test result !== nothing
        output, transitions = result
        @test engine.detector == "gru"
        @test output.records[1].stress == 0.0f0
        @test output.records[1].state == MODE_BASELINE
        @test isempty(transitions)
        @test occursin("\"detector\":\"gru\"",
                       alert_payload(output.t_ms, UInt32(50), 0x00, 0x01, 0.2, engine.detector))
    end

    @testset "alert detector field accepts transformer" begin
        t = alert_payload(1, UInt32(1), 0x00, 0x01, 0.5, "transformer")
        @test occursin("\"detector\":\"transformer\"", t)
    end

    @testset "DETECTOR=transformer with missing artifact fail-closes toward z-score" begin
        old_d = get(ENV, "DETECTOR", nothing)
        old_g = get(ENV, "GRU_MODEL", nothing)
        old_t = get(ENV, "TRANSFORMER_MODEL", nothing)
        ENV["DETECTOR"] = "transformer"
        ENV["GRU_MODEL"] = joinpath(tempdir(), "no-such-gru-$(rand(UInt32)).bson")
        ENV["TRANSFORMER_MODEL"] = joinpath(tempdir(), "no-such-tx-$(rand(UInt32)).bson")
        try
            engine = resolve_engine()
            @test engine.detector == "zscore"
            @test engine.requested == "transformer"
            @test engine.fallback_reason == "missing_artifact"
            @test occursin("\"detector\":\"zscore\"",
                           alert_payload(1, UInt32(1), 0x00, 0x01, 0.5, engine.detector))
        finally
            old_d === nothing ? delete!(ENV, "DETECTOR") : (ENV["DETECTOR"] = old_d)
            old_g === nothing ? delete!(ENV, "GRU_MODEL") : (ENV["GRU_MODEL"] = old_g)
            old_t === nothing ? delete!(ENV, "TRANSFORMER_MODEL") : (ENV["TRANSFORMER_MODEL"] = old_t)
        end
    end

    @testset "Transformer logits have the GRU contract" begin
        net = Models.build_transformer()
        x = randn(Float32, 8, 3, 30)
        logits = net(x)
        @test size(logits) == (4, 3)
        p = Models.seq_forward(net, x)
        @test all(≈(1.0f0; atol = 1e-5), sum(p; dims = 1))
    end

    @testset "in-memory Transformer path stays silent in warm-up" begin
        net = Models.build_transformer()
        det = Models.GRUDetector(net, zeros(Float32, 8), ones(Float32, 8);
                                 n_baseline = 5, seq_len = 5)
        engine = FeatureEngine(; detector = "transformer", gru = det)
        result = nothing
        for i in 1:10
            frame = SwarmBinFrame(UInt16(3), UInt32(70), UInt32(1), UInt16(100),
                                  UInt64(i * 100), UInt16[12])
            result = accept_swarm!(engine, frame)
        end
        @test result !== nothing
        output, transitions = result
        @test engine.detector == "transformer"
        @test output.records[1].stress == 0.0f0
        @test output.records[1].state == MODE_BASELINE
        @test isempty(transitions)
    end
end
