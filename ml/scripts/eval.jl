#!/usr/bin/env julia
# eval.jl -- score the GRU against the unsupervised z-score baseline and
# regenerate docs/EVAL.md.
#
#   julia --project=ml ml/scripts/eval.jl [--seeds 5] [--nodes 120] [--seconds 150]
#
# Both detectors are run **online**, one 1 s window at a time, exactly as the
# feature-worker runs them: each learns its own per-node baseline from the
# first 30 windows and may only raise a state after 2 consecutive alarm
# windows. Neither sees a label.
#
# Headline metric: LEAD TIME = symptom_onset - first sustained alarm, in
# seconds, where symptom_onset is the WIRE section 6 ground-truth event
# (firing rate below 5% of the node's own baseline for >= 2 s). Positive =
# the detector warned before the node visibly collapsed.

using Printf, Random, Statistics, Dates

include(joinpath(@__DIR__, "..", "src", "CockroachML.jl"))
using .CockroachML
using .CockroachML.Sim
using .CockroachML.Dataset
using .CockroachML.Models
using .CockroachML.Features

const ROOT       = CockroachML.REPO_ROOT
const PARAMS_IN  = joinpath(ROOT, "ml", "artifacts", "twin_params.json")
const MODEL_IN   = joinpath(ROOT, "ml", "artifacts", "gru.bson")
const OUT_MD     = joinpath(ROOT, "docs", "EVAL.md")

# ------------------------------------------------------------------ metrics --
"""
    roc_auc(scores, labels) -> Float64

Rank-based (Mann-Whitney U) ROC-AUC for a binary problem, tie-corrected.
Returns NaN when one class is absent.
"""
function roc_auc(scores::AbstractVector{<:Real}, labels::AbstractVector{Bool})
    n1 = count(labels)
    n0 = length(labels) - n1
    (n1 == 0 || n0 == 0) && return NaN
    p = sortperm(scores)
    ranks = Vector{Float64}(undef, length(scores))
    i = 1
    while i <= length(p)
        j = i
        while j < length(p) && scores[p[j + 1]] == scores[p[i]]
            j += 1
        end
        r = (i + j) / 2
        for k in i:j
            ranks[p[k]] = r
        end
        i = j + 1
    end
    s1 = sum(ranks[labels])
    return (s1 - n1 * (n1 + 1) / 2) / (n1 * n0)
end

"""
    prf(confusion, c) -> (precision, recall)

Precision and recall for class `c` (1-based) from a confusion matrix indexed
`[true, predicted]`.
"""
function prf(cm::Matrix{Int}, c::Int)
    tp = cm[c, c]
    fp = sum(@view cm[:, c]) - tp
    fn = sum(@view cm[c, :]) - tp
    prec = tp + fp > 0 ? tp / (tp + fp) : NaN
    rec  = tp + fn > 0 ? tp / (tp + fn) : NaN
    return (prec, rec)
end

mean_std(v) = isempty(v) ? (NaN, NaN) : (mean(v), length(v) > 1 ? std(v) : 0.0)

fmt(x; d = 3) = isnan(x) ? "n/a" : string(round(x; digits = d))
fmt_pm(m, s; d = 2) = isnan(m) ? "n/a" : @sprintf("%.*f ± %.*f", d, m, d, s)

# ------------------------------------------------------------- one seed run --
struct SeedResult
    cm::Matrix{Int}                 # [true, predicted], 4x4
    auc::Float64
    leads::Vector{Float64}          # seconds, one per collapsing node detected
    missed::Int                     # collapsing nodes never alarmed
    n_collapse::Int
    fp_rate::Float64                # fraction of true-baseline windows alarmed
end

"""
    calibrate(detector_factory, params, n_nodes, seconds, seed, alpha) -> Float64

Pick the stress threshold that gives a `alpha` false-alarm rate on a
**calibration cohort** disjoint from the evaluation seeds.

Without this the comparison is meaningless: a detector that alarms more often
buys lead time with false positives. Both detectors emit the same `stress`
quantity, so we take the `1 - alpha` quantile of stress over true-baseline
live windows and use it as each detector's threshold. Lead times are then
compared at a matched operating point.
"""
function calibrate(detector_factory::Function, params, n_nodes::Integer,
                   seconds::Real, seed::Integer, alpha::Real)
    rng = MersenneTwister(seed)
    traces = simulate_dataset(params, n_nodes, seconds; rng = rng,
                              onset_range = (40.0, 80.0))
    nws = build_windows(traces, Features.features_from_counts)
    det = detector_factory(0.0)          # threshold 0 => never gated, we only read stress
    base_stress = Float64[]
    for (i, nw) in enumerate(nws)
        for w in axes(nw.X, 2)
            t_ms = Int64(round(nw.t_s[w] * 1000))
            stress, _ = Models.observe!(det, UInt32(i), @view(nw.X[:, w]), t_ms)
            (w > Dataset.N_BASELINE && nw.y[w] == 0) && push!(base_stress, stress)
        end
    end
    isempty(base_stress) && return 0.5
    return clamp(quantile(base_stress, 1 - alpha), 1e-6, 1 - 1e-9)
end

"""
    run_seed(detector_factory, params, n_nodes, seconds, seed) -> SeedResult

Simulate a fresh cohort, stream it through a freshly constructed detector one
window at a time, and collect everything the report needs.
"""
function run_seed(detector_factory::Function, params, n_nodes::Integer,
                  seconds::Real, seed::Integer)
    rng = MersenneTwister(seed)
    traces = simulate_dataset(params, n_nodes, seconds; rng = rng,
                              onset_range = (40.0, 80.0))
    nws = build_windows(traces, Features.features_from_counts)

    det = detector_factory()
    cm = zeros(Int, 4, 4)
    scores = Float64[]
    binlab = Bool[]
    leads = Float64[]
    missed = 0
    n_collapse = 0
    base_windows = 0
    base_alarms = 0

    for (i, nw) in enumerate(nws)
        node_id = UInt32(i)
        alarm_s = NaN
        for w in axes(nw.X, 2)
            t_ms = Int64(round(nw.t_s[w] * 1000))
            stress, state = Models.observe!(det, node_id, @view(nw.X[:, w]), t_ms)
            # Only score windows the detector is actually live for; during
            # warm-up it is required to stay silent, so counting those would
            # flatter both detectors equally but hide real behaviour.
            live = w > Dataset.N_BASELINE
            live || continue
            cm[nw.y[w] + 1, state + 1] += 1
            push!(scores, stress)
            push!(binlab, nw.y[w] != 0)
            if nw.y[w] == 0
                base_windows += 1
                state != 0 && (base_alarms += 1)
            end
            (isnan(alarm_s) && state != 0) && (alarm_s = nw.t_s[w])
        end
        if isfinite(nw.symptom_s)
            n_collapse += 1
            if isnan(alarm_s)
                missed += 1
            else
                push!(leads, nw.symptom_s - alarm_s)
            end
        end
    end

    return SeedResult(cm, roc_auc(scores, binlab), leads, missed, n_collapse,
                      base_windows > 0 ? base_alarms / base_windows : NaN)
end

# ------------------------------------------------------------------- report --
function aggregate(rs::Vector{SeedResult})
    cm = reduce(+, [r.cm for r in rs])
    aucs = [r.auc for r in rs if !isnan(r.auc)]
    all_leads = reduce(vcat, [r.leads for r in rs]; init = Float64[])
    per_seed_lead = [isempty(r.leads) ? NaN : mean(r.leads) for r in rs]
    per_seed_lead = filter(!isnan, per_seed_lead)
    det_rate = [(r.n_collapse - r.missed) / max(r.n_collapse, 1) for r in rs]
    return (cm = cm,
            auc = mean_std(aucs),
            lead = mean_std(per_seed_lead),
            lead_all = all_leads,
            det = mean_std(det_rate),
            fp = mean_std([r.fp_rate for r in rs if !isnan(r.fp_rate)]))
end

function class_table(io, cm::Matrix{Int})
    println(io, "| class | precision | recall | support |")
    println(io, "|---|---|---|---|")
    for c in 1:4
        p, r = prf(cm, c)
        @printf(io, "| %s | %s | %s | %d |\n",
                Sim.KIND_NAMES[c], fmt(p), fmt(r), sum(@view cm[c, :]))
    end
end

function confusion_table(io, cm::Matrix{Int})
    println(io, "| true \\\\ predicted | ", join(Sim.KIND_NAMES, " | "), " |")
    println(io, "|---|---|---|---|---|")
    for c in 1:4
        println(io, "| **", Sim.KIND_NAMES[c], "** | ", join(cm[c, :], " | "), " |")
    end
end

function main(args = ARGS)
    seeds   = 5
    n_nodes = 120
    seconds = 150.0
    alpha   = 0.01              # target false-alarm rate for BOTH detectors
    i = 1
    while i <= length(args)
        a = args[i]
        if a == "--seeds" && i < length(args); seeds = parse(Int, args[i+1]); i += 2
        elseif a == "--nodes" && i < length(args); n_nodes = parse(Int, args[i+1]); i += 2
        elseif a == "--seconds" && i < length(args); seconds = parse(Float64, args[i+1]); i += 2
        elseif a == "--alpha" && i < length(args); alpha = parse(Float64, args[i+1]); i += 2
        else; i += 1
        end
    end

    t0 = time()
    params = if isfile(PARAMS_IN)
        ps = load_twin_params(PARAMS_IN)
        isempty(ps) ? Sim.DEFAULT_PARAMS : [TwinParam(p.shape, p.rate_hz, p.refractory_ms) for p in ps]
    else
        Sim.DEFAULT_PARAMS
    end
    param_src = isfile(PARAMS_IN) ? relpath(PARAMS_IN, ROOT) : "built-in default"

    isfile(MODEL_IN) || error("$MODEL_IN not found -- run `just train` first")
    net, mu, sd, meta = Models.load_gru_artifact(MODEL_IN)

    zfac(th) = Models.ZScoreDetector(; stress_thresh = th)
    gfac(th) = Models.GRUDetector(net, mu, sd; stress_thresh = th)

    # Calibrate both detectors to the same false-alarm rate, on a cohort that
    # shares no seed with the evaluation cohorts.
    cal_seed = 999
    cal_nodes = max(n_nodes ÷ 2, 40)
    z_th = calibrate(zfac, params, cal_nodes, seconds, cal_seed, alpha)
    g_th = calibrate(gfac, params, cal_nodes, seconds, cal_seed, alpha)
    @printf("calibrated at alpha=%.3f: z-score stress>=%.4f, GRU stress>=%.4f (%.0f s)\n",
            alpha, z_th, g_th, time() - t0)

    seed_list = 1000 .+ (1:seeds)
    @printf("evaluating %d seeds x %d nodes x %.0f s\n", seeds, n_nodes, seconds)

    zs = SeedResult[]
    gs = SeedResult[]
    for s in seed_list
        push!(zs, run_seed(() -> zfac(z_th), params, n_nodes, seconds, s))
        push!(gs, run_seed(() -> gfac(g_th), params, n_nodes, seconds, s))
        @printf("  seed %d done (%.0f s)\n", s, time() - t0)
    end

    Z = aggregate(zs)
    G = aggregate(gs)

    mkpath(dirname(OUT_MD))
    open(OUT_MD, "w") do io
        println(io, "# EVAL.md — detector evaluation (WS-C)")
        println(io)
        println(io, "*Generated by `just eval` (`ml/scripts/eval.jl`) on ",
                Dates.format(now(UTC), dateformat"yyyy-mm-dd HH:MM:SS"), " UTC. Do not edit by hand.*")
        println(io)
        println(io, "## What was measured")
        println(io)
        @printf(io, "%d seeds x %d held-out virtual nodes x %.0f s, simulated by `ml/src/sim.jl`\n",
                seeds, n_nodes, seconds)
        println(io, "(the Julia reimplementation of the WS-B twin dynamics), with gamma-renewal")
        println(io, "parameters drawn from `", param_src, "`.")
        println(io)
        @printf(io, "Both detectors are calibrated to the **same false-alarm rate** (target α = %.1f %%) on a\n", alpha * 100)
        println(io, "separate calibration cohort before evaluation, so lead time is compared at a matched")
        println(io, "operating point rather than bought with extra false positives.")
        println(io)
        @printf(io, "Calibrated thresholds: z-score `stress >= %.4f`, GRU `stress >= %.4f`.\n", z_th, g_th)
        println(io)
        println(io, "Both detectors are run **online**, one 1 s feature window at a time, exactly")
        println(io, "as `services/feature-worker` runs them: each learns its own per-node baseline")
        println(io, "from the first 30 windows and may only declare a state after 2 consecutive")
        println(io, "alarm windows. Neither sees a label. Windows inside the warm-up are excluded")
        println(io, "from the scores, since both detectors are required to stay silent there.")
        println(io)
        println(io, "- **z-score**: unsupervised diagonal Hotelling *T²* drift on the 8-feature")
        println(io, "  vector, with a rule-based class assignment. No training.")
        @printf(io, "- **GRU**: Flux `GRU(8 => 32)` over 30-window sequences, 4-class softmax,\n")
        @printf(io, "  stress = 1 − P(baseline). Trained by `just train` (seed %s, %s nodes,\n",
                string(get(meta, "seed", "?")), string(get(meta, "nodes", "?")))
        @printf(io, "  val accuracy %s).\n", fmt(Float64(get(meta, "val_acc", NaN))))
        println(io)

        println(io, "## Headline: lead time")
        println(io)
        println(io, "**Lead time** = `symptom_onset − first sustained alarm`, in seconds.")
        println(io, "`symptom_onset` is the WIRE §6 ground-truth event (a node's firing rate below")
        println(io, "5 % of its own baseline for ≥ 2 s). Positive means the detector warned")
        println(io, "*before* the node visibly collapsed. Mean ± std across seeds.")
        println(io)
        println(io, "| detector | lead time (s) | collapsing nodes detected | ROC-AUC | false-alarm rate |")
        println(io, "|---|---|---|---|---|")
        @printf(io, "| z-score | %s | %s | %s | %s |\n",
                fmt_pm(Z.lead...), fmt_pm(Z.det[1] * 100, Z.det[2] * 100; d = 1) * " %",
                fmt_pm(Z.auc...; d = 3), fmt_pm(Z.fp[1] * 100, Z.fp[2] * 100; d = 2) * " %")
        @printf(io, "| GRU | %s | %s | %s | %s |\n",
                fmt_pm(G.lead...), fmt_pm(G.det[1] * 100, G.det[2] * 100; d = 1) * " %",
                fmt_pm(G.auc...; d = 3), fmt_pm(G.fp[1] * 100, G.fp[2] * 100; d = 2) * " %")
        println(io)
        if !isnan(G.lead[1]) && !isnan(Z.lead[1])
            delta = G.lead[1] - Z.lead[1]
            @printf(io, "The GRU warns **%.2f s %s** than the z-score baseline on average.\n",
                    abs(delta), delta >= 0 ? "earlier" : "later")
            println(io)
        end
        if !isempty(G.lead_all)
            @printf(io, "Per-node lead-time distribution (GRU, all seeds pooled, n = %d): ",
                    length(G.lead_all))
            @printf(io, "median %.1f s, 10th pct %.1f s, 90th pct %.1f s.\n",
                    median(G.lead_all), quantile(G.lead_all, 0.1), quantile(G.lead_all, 0.9))
            println(io)
        end

        println(io, "## Per-class precision / recall")
        println(io)
        println(io, "Pooled over all seeds and all live windows.")
        println(io)
        println(io, "### z-score")
        println(io)
        class_table(io, Z.cm)
        println(io)
        println(io, "### GRU")
        println(io)
        class_table(io, G.cm)
        println(io)

        println(io, "## Confusion matrices")
        println(io)
        println(io, "### z-score")
        println(io)
        confusion_table(io, Z.cm)
        println(io)
        println(io, "### GRU")
        println(io)
        confusion_table(io, G.cm)
        println(io)

        println(io, "## What this is not")
        println(io)
        println(io, "Lead time is measured in **seconds** against a *simulated* collapse")
        println(io, "endpoint (firing rate below 5 % of the node's own baseline), not days of")
        println(io, "clinical prodrome. The GRU is trained on labels from `ml/src/sim.jl` (the")
        println(io, "twin generator), not on in-vivo pharmacology. It is a 4-class sequence")
        println(io, "classifier (`GRU(8 => 32)`). A tiny Transformer encoder is trained by")
        println(io, "`just train --arch transformer` on the same 8×30 twin sequences; it is")
        println(io, "not a day-scale clinical model and does not ingest RNA-seq.")
        println(io, "See `docs/METHODS.md` and `docs/FUTURE.md`.")
        println(io)
        println(io, "Online `feature-worker` prefers `DETECTOR=transformer` then GRU then z-score.")
        println(io, "`/metrics` exports `feature_worker_transformer_loaded` and")
        println(io, "`feature_worker_gru_loaded`. The tables above are GRU vs z-score until")
        println(io, "the Transformer artifact is trained and `just eval` is re-run.")
        println(io)

        println(io, "## How to reproduce")
        println(io)
        println(io, "```sh")
        println(io, "just prepare   # fit twin_params.json from data/raw (or synthetic fallback)")
        println(io, "just train     # ml/artifacts/gru.bson")
        println(io, "just train --arch transformer")
        println(io, "just eval      # regenerates this file")
        println(io, "```")
        println(io)
        println(io, "Every number above comes from a seeded, re-runnable script; no result is")
        println(io, "hand-entered. See `docs/METHODS.md` for data provenance and what is real vs")
        println(io, "synthetic.")
    end

    @printf("\nz-score: lead %s s  AUC %s  detected %s\n",
            fmt_pm(Z.lead...), fmt_pm(Z.auc...; d = 3), fmt_pm(Z.det...; d = 3))
    @printf("GRU    : lead %s s  AUC %s  detected %s\n",
            fmt_pm(G.lead...), fmt_pm(G.auc...; d = 3), fmt_pm(G.det...; d = 3))
    @printf("wrote %s  (%.0f s total)\n", relpath(OUT_MD, ROOT), time() - t0)
    return 0
end

if abspath(PROGRAM_FILE) == @__FILE__
    main()
end
