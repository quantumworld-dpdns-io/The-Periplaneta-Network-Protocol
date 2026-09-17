# dataio.jl -- loading real insect spike trains, with a clearly labelled
# synthetic fallback so the pipeline always runs.
#
# Sources (see data/README.md and docs/WS-C.md for provenance):
#   data/raw/CockroachDataJNM_2009_181_119.h5  Zenodo 14281, CC-BY-4.0
#       Cockroach (Periplaneta americana) antennal lobe. Already SORTED:
#       each dataset is a vector of spike times in seconds.
#   data/raw/LocustDemoData.hdf5               Zenodo 14607, CC0-1.0
#       Locust (Schistocerca americana) antennal lobe tetrode, 4 channels x
#       20 s @ 15 kHz raw. Spikes are extracted here with the SAME detector
#       the feature-worker runs on live HIL traces.
#   data/raw/crcns-ia1/                        CRCNS ia-1, login required
#       Grasshopper auditory receptors, ASCII. Optional.
#   data/cache/synthetic_units.csv             labelled synthetic fallback.

module DataIO

using Random, Statistics, Distributions, CSV, DataFrames, HDF5

export load_units, load_cockroach_h5, load_locust_h5, load_crcns_ia1,
       write_synthetic_units, load_synthetic_units, UnitTrain

"""
    UnitTrain(id, source, spikes)

One unit's spike times in seconds, tagged with where it came from.
"""
struct UnitTrain
    id::String
    source::String
    spikes::Vector{Float64}
end

const COCKROACH_H5 = "CockroachDataJNM_2009_181_119.h5"
const LOCUST_H5    = "LocustDemoData.hdf5"

# ------------------------------------------------------- Zenodo: cockroach ---
"""
    load_cockroach_h5(path; min_spikes=30) -> Vector{UnitTrain}

Zenodo 14281 (CC-BY-4.0). Layout is `/<experiment>/<NeuronN>/spont` for
spontaneous activity plus `/<experiment>/<NeuronN>/<odour>/<trial>` for
odour trials; every dataset is a Float64 vector of spike times in seconds.
Only spontaneous trains are used for twin fitting -- odour-evoked responses
are non-stationary and would bias the renewal fit.
"""
function load_cockroach_h5(path::AbstractString; min_spikes::Integer = 30)
    units = UnitTrain[]
    isfile(path) || return units
    h5open(path) do h
        for e in sort(collect(keys(h)))
            e == "README" && continue
            g = h[e]
            g isa HDF5.Group || continue
            for nk in sort(collect(keys(g)))
                nk == "date" && continue
                ng = g[nk]
                ng isa HDF5.Group || continue
                haskey(ng, "spont") || continue
                st = Float64.(vec(read(ng["spont"])))
                length(st) >= min_spikes || continue
                push!(units, UnitTrain("$(e)/$(nk)", "zenodo:10.5281/zenodo.14281", st))
            end
        end
    end
    return units
end

# ---------------------------------------------------------- Zenodo: locust ---
"""
    load_locust_h5(path, detect; fs=15000.0, min_spikes=30) -> Vector{UnitTrain}

Zenodo 14607 (CC0-1.0): four raw tetrode channels, 20 s @ 15 kHz, already
band-passed 300-5000 Hz. `detect` is the shared `detect_spikes` function, so
these units are extracted by exactly the code path the feature-worker uses
on live HIL data -- one detector, one set of biases.
"""
function load_locust_h5(path::AbstractString, detect::Function;
                        fs::Real = 15000.0, min_spikes::Integer = 30, k::Real = 4.5)
    units = UnitTrain[]
    isfile(path) || return units
    h5open(path) do h
        for ch in sort(collect(keys(h)))
            ch == "README" && continue
            d = h[ch]
            d isa HDF5.Dataset || continue
            x = Float64.(vec(read(d)))
            length(x) < 1000 && continue
            st = detect(x, fs; k = k, refractory_ms = 1.5)
            length(st) >= min_spikes || continue
            push!(units, UnitTrain("locust/ch$(ch)", "zenodo:10.5281/zenodo.14607", st))
        end
    end
    return units
end

# ------------------------------------------------------------- CRCNS ia-1 ----
"""
    load_crcns_ia1(dir; min_spikes=30) -> Vector{UnitTrain}

Optional. CRCNS ia-1 ships ASCII files; spike-time files are whitespace- or
newline-separated numbers. We scan `dir` recursively for `*.dat`/`*.txt`
files whose contents parse as an increasing sequence of times and treat each
as one unit. Absent directory -> empty vector (the dataset needs a free
CRCNS account; see data/README.md).
"""
function load_crcns_ia1(dir::AbstractString; min_spikes::Integer = 30, max_units::Integer = 200)
    units = UnitTrain[]
    isdir(dir) || return units
    for (root, _, files) in walkdir(dir)
        for f in files
            length(units) >= max_units && return units
            endswith(lowercase(f), ".dat") || endswith(lowercase(f), ".txt") || continue
            occursin("readme", lowercase(f)) && continue
            p = joinpath(root, f)
            vals = Float64[]
            try
                for line in eachline(p)
                    for tok in split(line)
                        v = tryparse(Float64, tok)
                        v === nothing && continue
                        push!(vals, v)
                    end
                    length(vals) > 100_000 && break
                end
            catch
                continue
            end
            length(vals) >= min_spikes || continue
            issorted(vals) && all(>(0), diff(vals)) || continue
            # ia-1 stores times in ms in most files; normalise to seconds.
            maximum(vals) > 1000 && (vals = vals ./ 1000)
            push!(units, UnitTrain(relpath(p, dir), "crcns:10.6080/K0BG2KWB", vals))
        end
    end
    return units
end

# ---------------------------------------------------- synthetic fallback -----
"""
    write_synthetic_units(path; n_units=48, dur_s=60.0, seed=42) -> DataFrame

Generate and persist a **clearly labelled synthetic** unit set so the whole
pipeline runs with no network access. Parameters are drawn from ranges that
match published insect antennal-lobe statistics (mean rates 2-25 Hz, ISI CV
0.5-1.6, refractory 1-6 ms) but no real recording is involved -- every
consumer of this file reports `source = "synthetic"`.
"""
function write_synthetic_units(path::AbstractString; n_units::Integer = 48,
                               dur_s::Real = 60.0, seed::Integer = 42)
    rng = MersenneTwister(seed)
    rows = DataFrame(unit_id = String[], t_s = Float64[])
    for u in 1:n_units
        shape = 0.6 + 2.4 * rand(rng)                 # CV = 1/sqrt(shape)
        target_rate = 2 + 23 * rand(rng)              # Hz
        refr = (1 + 5 * rand(rng)) / 1000             # s
        theta = max((1 / target_rate - refr) / shape, 1e-4)
        t = 0.0
        uid = "synthetic_unit_$(lpad(u, 3, '0'))"
        while true
            t += refr + rand(rng, Gamma(shape, theta))
            t > dur_s && break
            push!(rows, (uid, t))
        end
    end
    mkpath(dirname(path))
    CSV.write(path, rows)
    return rows
end

"""
    load_synthetic_units(path) -> Vector{UnitTrain}
"""
function load_synthetic_units(path::AbstractString)
    isfile(path) || return UnitTrain[]
    df = CSV.read(path, DataFrame)
    units = UnitTrain[]
    for uid in unique(df.unit_id)
        st = Float64.(df.t_s[df.unit_id .== uid])
        push!(units, UnitTrain(String(uid), "synthetic", sort(st)))
    end
    return units
end

# ------------------------------------------------------------------ facade ---
"""
    load_units(repo_root, detect; allow_synthetic=true) -> (units, source, is_real)

Load every real dataset that is present on disk; if none is, generate (and
cache) the synthetic fallback. `source` is the provenance string written into
`twin_params.json`, and `is_real` says plainly whether any real recording
contributed.
"""
function load_units(repo_root::AbstractString, detect::Function; allow_synthetic::Bool = true)
    raw = joinpath(repo_root, "data", "raw")
    units = UnitTrain[]
    srcs = String[]

    ck = load_cockroach_h5(joinpath(raw, COCKROACH_H5))
    isempty(ck) || (append!(units, ck); push!(srcs, "zenodo:10.5281/zenodo.14281 (cockroach AL, n=$(length(ck)))"))

    lc = load_locust_h5(joinpath(raw, LOCUST_H5), detect)
    isempty(lc) || (append!(units, lc); push!(srcs, "zenodo:10.5281/zenodo.14607 (locust AL, n=$(length(lc)))"))

    ia = load_crcns_ia1(joinpath(raw, "crcns-ia1"))
    isempty(ia) || (append!(units, ia); push!(srcs, "crcns:10.6080/K0BG2KWB (grasshopper ia-1, n=$(length(ia)))"))

    if !isempty(units)
        return (units, join(srcs, " + "), true)
    end

    allow_synthetic || return (units, "none", false)
    cache = joinpath(repo_root, "data", "cache", "synthetic_units.csv")
    isfile(cache) || write_synthetic_units(cache)
    syn = load_synthetic_units(cache)
    return (syn, "SYNTHETIC (no real dataset on disk; run data/fetch.sh) n=$(length(syn))", false)
end

end # module DataIO
