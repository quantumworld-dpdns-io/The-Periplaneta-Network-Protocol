# wire.jl -- binary frame codecs for Cockroach Internet, per proto/WIRE.md section 4.
# Pure Julia, no deps. All multi-byte integers little-endian.
# Included by services/feature-worker/src/main.jl and by ml/test/runtests.jl.

module Wire

export HilRawFrame, SwarmBinFrame, FeatureFrame, NodeRecord,
       encode, decode_hil_raw, decode_swarm_bin, decode_feature, alert_json,
       node_hex, shard_of, MAGIC_HILR, MAGIC_SWBN, MAGIC_FEAT,
       MODE_BASELINE, MODE_TOXIN, MODE_DRUG, MODE_RADIATION,
       MODE_OFFLINE, MODE_COLLAPSED, HIL_BASE, HIL_SHARD, N_FEAT

const MAGIC_HILR = UInt8['H', 'I', 'L', 'R']
const MAGIC_SWBN = UInt8['S', 'W', 'B', 'N']
const MAGIC_FEAT = UInt8['F', 'E', 'A', 'T']

const MODE_BASELINE  = UInt8(0)
const MODE_TOXIN     = UInt8(1)
const MODE_DRUG      = UInt8(2)
const MODE_RADIATION = UInt8(3)
const MODE_OFFLINE   = UInt8(4)
const MODE_COLLAPSED = UInt8(5)

const HIL_BASE  = UInt32(0xFFFF0000)   # HIL node ids are HIL_BASE + hil_index
const HIL_SHARD = UInt16(0xFFFF)
const N_FEAT    = 8

"node_hex per WIRE section 1: 8 lowercase hex chars."
node_hex(node_id::Integer) = string(UInt32(node_id); base = 16, pad = 8)

"shard per WIRE section 1: node_id / 1000 for twins, 0xFFFF for HIL nodes."
shard_of(node_id::Integer) =
    UInt32(node_id) >= HIL_BASE ? HIL_SHARD : UInt16(UInt32(node_id) / 1000 |> floor)

# --------------------------------------------------------------- helpers ----
@inline function wr!(buf::Vector{UInt8}, off::Int, x::T) where {T}
    r = Ref(htol(x))
    GC.@preserve r begin
        p = Ptr{UInt8}(Base.unsafe_convert(Ptr{T}, r))
        unsafe_copyto!(pointer(buf, off + 1), p, sizeof(T))
    end
    return off + sizeof(T)
end

@inline function rd(::Type{T}, buf::AbstractVector{UInt8}, off::Int) where {T}
    sizeof(T) + off <= length(buf) || throw(ArgumentError("frame truncated reading $T at offset $off"))
    r = Ref{T}()
    src = buf isa Vector{UInt8} ? buf : Vector{UInt8}(buf)
    GC.@preserve r src begin
        p = Ptr{UInt8}(Base.unsafe_convert(Ptr{T}, r))
        unsafe_copyto!(p, pointer(src, off + 1), sizeof(T))
    end
    return ltoh(r[])
end

@inline function check_magic(buf::AbstractVector{UInt8}, magic::Vector{UInt8}, name::String)
    length(buf) >= 32 || throw(ArgumentError("$name frame shorter than 32-byte header"))
    for i in 1:4
        buf[i] == magic[i] || throw(ArgumentError("bad magic for $name"))
    end
    buf[5] == 0x01 || throw(ArgumentError("unsupported $name version $(buf[5])"))
    return nothing
end

# ------------------------------------------------------- 4.1 HilRawFrame ----
"""
    HilRawFrame(mode, node_id, seq, ts_us, sample_rate_hz, samples)

WIRE section 4.1. 32-byte header + n_samples x Int16 in units of 0.1 uV.
"""
struct HilRawFrame
    mode::UInt8
    node_id::UInt32
    seq::UInt32
    ts_us::UInt64
    sample_rate_hz::UInt32
    samples::Vector{Int16}
end

function encode(f::HilRawFrame)
    n = length(f.samples)
    n <= typemax(UInt16) || throw(ArgumentError("n_samples $n exceeds u16"))
    buf = zeros(UInt8, 32 + 2n)
    buf[1:4] = MAGIC_HILR
    buf[5] = 0x01
    buf[6] = f.mode
    wr!(buf, 8,  f.node_id)
    wr!(buf, 12, f.seq)
    wr!(buf, 16, f.ts_us)
    wr!(buf, 24, f.sample_rate_hz)
    wr!(buf, 28, UInt16(n))
    o = 32
    @inbounds for s in f.samples
        o = wr!(buf, o, s)
    end
    return buf
end

function decode_hil_raw(buf::AbstractVector{UInt8})
    check_magic(buf, MAGIC_HILR, "HILR")
    mode    = buf[6]
    node_id = rd(UInt32, buf, 8)
    seq     = rd(UInt32, buf, 12)
    ts_us   = rd(UInt64, buf, 16)
    sr      = rd(UInt32, buf, 24)
    n       = Int(rd(UInt16, buf, 28))
    length(buf) >= 32 + 2n || throw(ArgumentError("HILR truncated: need $(32 + 2n), got $(length(buf))"))
    samples = Vector{Int16}(undef, n)
    @inbounds for i in 1:n
        samples[i] = rd(Int16, buf, 32 + 2 * (i - 1))
    end
    return HilRawFrame(mode, node_id, seq, ts_us, sr, samples)
end

# ------------------------------------------------------ 4.2 SwarmBinFrame ----
"""
    SwarmBinFrame(shard, first_node_id, n_nodes, bin_ms, t_ms, counts)

WIRE section 4.2. One frame per shard per bin; counts[i] is the spike count
for node first_node_id + i - 1 within that bin.
"""
struct SwarmBinFrame
    shard::UInt16
    first_node_id::UInt32
    n_nodes::UInt32
    bin_ms::UInt16
    t_ms::UInt64
    counts::Vector{UInt16}
end

function encode(f::SwarmBinFrame)
    n = length(f.counts)
    Int(f.n_nodes) == n || throw(ArgumentError("n_nodes $(f.n_nodes) != length(counts) $n"))
    buf = zeros(UInt8, 32 + 2n)
    buf[1:4] = MAGIC_SWBN
    buf[5] = 0x01
    wr!(buf, 6,  f.shard)
    wr!(buf, 8,  f.first_node_id)
    wr!(buf, 12, f.n_nodes)
    wr!(buf, 16, f.bin_ms)
    wr!(buf, 24, f.t_ms)
    o = 32
    @inbounds for c in f.counts
        o = wr!(buf, o, c)
    end
    return buf
end

function decode_swarm_bin(buf::AbstractVector{UInt8})
    check_magic(buf, MAGIC_SWBN, "SWBN")
    shard  = rd(UInt16, buf, 6)
    first  = rd(UInt32, buf, 8)
    nn     = rd(UInt32, buf, 12)
    bin_ms = rd(UInt16, buf, 16)
    t_ms   = rd(UInt64, buf, 24)
    n = Int(nn)
    length(buf) >= 32 + 2n || throw(ArgumentError("SWBN truncated: need $(32 + 2n), got $(length(buf))"))
    counts = Vector{UInt16}(undef, n)
    @inbounds for i in 1:n
        counts[i] = rd(UInt16, buf, 32 + 2 * (i - 1))
    end
    return SwarmBinFrame(shard, first, nn, bin_ms, t_ms, counts)
end

# ------------------------------------------------------- 4.3 FeatureFrame ----
const NODE_RECORD_BYTES = 40

"""
    NodeRecord(stress, state, features)

40 bytes: f32 stress, u8 state, 3 reserved, 8 x f32 features. Feature index
order (WIRE section 4.3): rate_hz, isi_mean_ms, isi_cv, burst_index, fano,
rate_slope_hz_per_s, band_power, snr_db.
"""
struct NodeRecord
    stress::Float32
    state::UInt8
    features::NTuple{8,Float32}
end

function NodeRecord(stress::Real, state::Integer, f::AbstractVector{<:Real})
    length(f) == 8 || throw(ArgumentError("need 8 features, got $(length(f))"))
    return NodeRecord(Float32(stress), UInt8(state), NTuple{8,Float32}(Float32.(f)))
end

struct FeatureFrame
    shard::UInt16
    first_node_id::UInt32
    n_nodes::UInt32
    t_ms::UInt64
    window_ms::UInt32
    records::Vector{NodeRecord}
end

function encode(f::FeatureFrame)
    n = length(f.records)
    Int(f.n_nodes) == n || throw(ArgumentError("n_nodes $(f.n_nodes) != length(records) $n"))
    buf = zeros(UInt8, 32 + NODE_RECORD_BYTES * n)
    buf[1:4] = MAGIC_FEAT
    buf[5] = 0x01
    buf[6] = UInt8(N_FEAT)
    wr!(buf, 6,  f.shard)
    wr!(buf, 8,  f.first_node_id)
    wr!(buf, 12, f.n_nodes)
    wr!(buf, 16, f.t_ms)
    wr!(buf, 24, f.window_ms)
    @inbounds for (i, r) in enumerate(f.records)
        base = 32 + NODE_RECORD_BYTES * (i - 1)
        wr!(buf, base, r.stress)
        buf[base + 5] = r.state
        for k in 1:8
            wr!(buf, base + 8 + 4 * (k - 1), r.features[k])
        end
    end
    return buf
end

function decode_feature(buf::AbstractVector{UInt8})
    check_magic(buf, MAGIC_FEAT, "FEAT")
    buf[6] == UInt8(N_FEAT) || throw(ArgumentError("n_feat $(buf[6]) != $N_FEAT"))
    shard = rd(UInt16, buf, 6)
    first = rd(UInt32, buf, 8)
    nn    = rd(UInt32, buf, 12)
    t_ms  = rd(UInt64, buf, 16)
    win   = rd(UInt32, buf, 24)
    n = Int(nn)
    need = 32 + NODE_RECORD_BYTES * n
    length(buf) >= need || throw(ArgumentError("FEAT truncated: need $need, got $(length(buf))"))
    recs = Vector{NodeRecord}(undef, n)
    @inbounds for i in 1:n
        base = 32 + NODE_RECORD_BYTES * (i - 1)
        stress = rd(Float32, buf, base)
        state  = buf[base + 5]
        feats  = ntuple(k -> rd(Float32, buf, base + 8 + 4 * (k - 1)), 8)
        recs[i] = NodeRecord(stress, state, feats)
    end
    return FeatureFrame(shard, first, nn, t_ms, win, recs)
end

"""
    alert_json(t_ms, node_id, prev_state, state, stress, detector) -> Dict

WIRE section 7 alert payload for topic neuro.alerts.
"""
function alert_json(t_ms::Integer, node_id::Integer, prev_state::Integer,
                    state::Integer, stress::Real, detector::AbstractString)
    return Dict{String,Any}(
        "t_ms"       => Int64(t_ms),
        "node_id"    => Int64(UInt32(node_id)),
        "node_hex"   => node_hex(node_id),
        "prev_state" => Int(prev_state),
        "state"      => Int(state),
        "stress"     => round(Float64(stress); digits = 4),
        "detector"   => String(detector),
    )
end

end # module Wire
