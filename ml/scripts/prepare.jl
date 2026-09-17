#!/usr/bin/env julia
# prepare.jl -- fit gamma ISI parameters to every available unit and write
# ml/artifacts/twin_params.json, which WS-B's swarm-gen reads at boot.
#
#   julia --project=ml ml/scripts/prepare.jl
#
# Real recordings are used when data/raw/ has them (run data/fetch.sh first);
# otherwise a clearly labelled synthetic fallback is generated so the rest of
# the pipeline still runs. The `source` field of the JSON always says which.

using Printf
using Statistics: median

include(joinpath(@__DIR__, "..", "src", "CockroachML.jl"))
using .CockroachML
using .CockroachML.DataIO
using .CockroachML.Features

const ROOT     = CockroachML.REPO_ROOT
const OUT      = joinpath(ROOT, "ml", "artifacts", "twin_params.json")

function main()
    units, source, is_real = DataIO.load_units(ROOT, Features.detect_spikes)
    if isempty(units)
        error("no units loaded and synthetic fallback failed")
    end

    @printf("loaded %d units  (%s)\n", length(units), is_real ? "REAL" : "SYNTHETIC")
    println("source: ", source)

    fits = NamedTuple[]
    by_source = Dict{String,Int}()
    for u in units
        p = fit_isi_params(u.spikes)
        # discard degenerate fits: too few spikes, or an implausible rate
        (p.n_spikes >= 30 && isfinite(p.shape) && 0 < p.mean_rate_hz < 500) || continue
        push!(fits, p)
        by_source[u.source] = get(by_source, u.source, 0) + 1
    end
    isempty(fits) && error("every unit failed the quality filter")

    d = write_twin_params(OUT, fits; source = source)

    shapes = [f.shape for f in fits]
    rates  = [f.mean_rate_hz for f in fits]
    refrs  = [f.refractory_ms for f in fits]
    cvs    = [f.isi_cv for f in fits]
    @printf("kept %d/%d units -> %s (%.1f KB)\n", length(d["units"]), length(units),
            relpath(OUT, ROOT), filesize(OUT) / 1024)
    for (k, v) in sort(collect(by_source), by = first)
        @printf("  %-52s %4d units\n", k, v)
    end
    @printf("  gamma shape    : median %.2f  [%.2f, %.2f]\n",
            median(shapes), minimum(shapes), maximum(shapes))
    @printf("  firing rate Hz : median %.2f  [%.2f, %.2f]\n",
            median(rates), minimum(rates), maximum(rates))
    @printf("  refractory ms  : median %.2f  [%.2f, %.2f]\n",
            median(refrs), minimum(refrs), maximum(refrs))
    @printf("  ISI CV         : median %.2f  [%.2f, %.2f]\n",
            median(cvs), minimum(cvs), maximum(cvs))
    is_real || println("\nNOTE: twin_params.json is SYNTHETIC. Run data/fetch.sh for real data.")
    return 0
end

if abspath(PROGRAM_FILE) == @__FILE__
    main()
end
