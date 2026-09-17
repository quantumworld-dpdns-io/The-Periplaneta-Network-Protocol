#!/usr/bin/env julia
# train.jl -- train GRU or Transformer stress classifier on simulated twins.
#
#   julia --project=ml ml/scripts/train.jl [--arch gru|transformer] [--nodes 400]
#
# Output: ml/artifacts/gru.bson or ml/artifacts/transformer.bson

using Printf, Random, Statistics, BSON, Dates

include(joinpath(@__DIR__, "..", "src", "CockroachML.jl"))
using .CockroachML
using .CockroachML.Sim
using .CockroachML.Dataset
using .CockroachML.Models
using .CockroachML.Features
import Flux

const ROOT      = CockroachML.REPO_ROOT
const PARAMS_IN = joinpath(ROOT, "ml", "artifacts", "twin_params.json")

"""
    twin_params() -> Vector{TwinParam}

Load the fitted parameter pool, falling back to the built-in default if
prepare.jl has not been run yet.
"""
function twin_params()
    isfile(PARAMS_IN) || return (Sim.DEFAULT_PARAMS, "built-in default (run prepare.jl)")
    ps = load_twin_params(PARAMS_IN)
    isempty(ps) && return (Sim.DEFAULT_PARAMS, "built-in default (twin_params.json was empty)")
    return ([TwinParam(p.shape, p.rate_hz, p.refractory_ms) for p in ps], PARAMS_IN)
end

"One-hot encode 0-based class labels into a (nclasses, n) Float32 matrix."
function onehot(y::AbstractVector{<:Integer}, nclasses::Integer)
    Y = zeros(Float32, nclasses, length(y))
    for (i, c) in enumerate(y)
        Y[c + 1, i] = 1.0f0
    end
    return Y
end

"""
    make_data(params, n_nodes, seconds, seed) -> (X, y)

Simulate, window, z-score each node against its own first 30 windows, then
cut 30-window sequences. `onset_range` starts after the baseline window so a
node's reference is never contaminated by its own intervention -- which is
also true in production, where nodes are running long before anyone presses
a button.
"""
function make_data(params, n_nodes, seconds, seed)
    rng = MersenneTwister(seed)
    traces = simulate_dataset(params, n_nodes, seconds; rng = rng,
                              onset_range = (40.0, 80.0))
    nws = build_windows(traces, Features.features_from_counts)
    nws = [Dataset.zscore_windows(nw) for nw in nws]
    return build_sequences(nws, Models.SEQ_LEN)
end

function main(args = ARGS)
    n_nodes = 300
    seconds = 150.0
    epochs  = 6
    seed    = 42
    arch    = "gru"
    i = 1
    while i <= length(args)
        a = args[i]
        if a == "--nodes"   && i < length(args); n_nodes = parse(Int, args[i+1]);     i += 2
        elseif a == "--seconds" && i < length(args); seconds = parse(Float64, args[i+1]); i += 2
        elseif a == "--epochs"  && i < length(args); epochs  = parse(Int, args[i+1]);     i += 2
        elseif a == "--seed"    && i < length(args); seed    = parse(Int, args[i+1]);     i += 2
        elseif a == "--arch"    && i < length(args); arch    = lowercase(args[i+1]);      i += 2
        else; i += 1
        end
    end
    arch in ("gru", "transformer") || error("--arch must be gru or transformer, got $arch")
    OUT = joinpath(ROOT, "ml", "artifacts", arch == "transformer" ? "transformer.bson" : "gru.bson")

    t_start = time()
    params, psrc = twin_params()
    @printf("twin params : %d units from %s\n", length(params), psrc)
    @printf("simulating  : %d nodes x %.0f s (seed %d, arch %s)\n", n_nodes, seconds, seed, arch)

    Xtr, ytr = make_data(params, n_nodes, seconds, seed)
    Xva, yva = make_data(params, max(n_nodes ÷ 4, 40), seconds, seed + 10_000)
    @printf("sequences   : %d train / %d val  (%.1f s)\n",
            length(ytr), length(yva), time() - t_start)
    for c in 0:(Models.N_CLASSES - 1)
        @printf("  class %d (%-9s): %5d train\n", c, Sim.KIND_NAMES[c+1], count(==(c), ytr))
    end

    # Standardise on the training set only, and carry mu/sd into the artifact.
    flat = reshape(permutedims(Xtr, (1, 3, 2)), 8, :)
    mu, sd = Models.feature_stats(flat)
    Xtr32 = (Float32.(Xtr) .- mu) ./ sd
    Xva32 = (Float32.(Xva) .- mu) ./ sd
    replace!(Xtr32, NaN32 => 0.0f0, Inf32 => 0.0f0, -Inf32 => 0.0f0)
    replace!(Xva32, NaN32 => 0.0f0, Inf32 => 0.0f0, -Inf32 => 0.0f0)

    Random.seed!(seed)
    net = arch == "transformer" ? Models.build_transformer() : Models.build_gru()
    opt = Flux.setup(Flux.Adam(2.0f-3), net)

    Ytr = onehot(ytr, Models.N_CLASSES)
    Yva = onehot(yva, Models.N_CLASSES)
    n = length(ytr)
    batch = 512
    best_acc = -Inf
    best_state = Flux.state(net)

    for ep in 1:epochs
        perm = randperm(n)
        tot = 0.0
        nb = 0
        for s in 1:batch:n
            idx = perm[s:min(s + batch - 1, n)]
            xb = Xtr32[:, idx, :]
            yb = Ytr[:, idx]
            loss, gs = Flux.withgradient(net) do m
                Flux.logitcrossentropy(m(xb), yb)
            end
            Flux.update!(opt, net, gs[1])
            tot += loss
            nb += 1
        end
        pv = Flux.softmax(net(Xva32))
        acc = mean(map(i -> argmax(@view pv[:, i]) - 1, 1:length(yva)) .== yva)
        @printf("epoch %2d/%d  loss %.4f  val acc %.4f  (%.0f s)\n",
                ep, epochs, tot / max(nb, 1), acc, time() - t_start)
        if acc > best_acc
            best_acc = acc
            best_state = deepcopy(Flux.state(net))
        end
    end

    mkpath(dirname(OUT))
    meta = Dict(
        "trained_at"  => Dates.format(now(UTC), dateformat"yyyy-mm-dd\THH:MM:SS\Z"),
        "seed"        => seed,
        "nodes"       => n_nodes,
        "seconds"     => seconds,
        "epochs"      => epochs,
        "val_acc"     => best_acc,
        "twin_params" => string(psrc),
        "seq_len"     => Models.SEQ_LEN,
        "hidden"      => 32,
        "arch"        => arch,
        "features"    => collect(Features.feature_names()),
    )
    model_state = best_state
    BSON.@save OUT model_state mu sd meta

    @printf("\nwrote %s (%.1f KB)  best val acc %.4f  total %.0f s\n",
            relpath(OUT, ROOT), filesize(OUT) / 1024, best_acc, time() - t_start)
    return 0
end

if abspath(PROGRAM_FILE) == @__FILE__
    main()
end
