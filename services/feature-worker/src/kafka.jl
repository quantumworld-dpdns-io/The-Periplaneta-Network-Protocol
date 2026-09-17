# kafka.jl -- a thin Kafka adapter.
#
# RDKafka.jl is an optional dependency: it needs librdkafka, which builds
# cleanly inside the julia:1.11 Docker image but not reliably on a Windows
# dev box. So the transport is isolated behind this one interface and the
# worker degrades to a no-op/loopback transport when the binding is missing.
# The codec and feature logic (wire.jl, features.jl) never depend on it and
# are covered by ml/test/runtests.jl either way.
#
#   Transport API
#     consumer(brokers, group, topics)  -> handle
#     poll(handle, timeout_ms)          -> (topic, key, payload) | nothing
#     producer(brokers)                 -> handle
#     produce(handle, topic, key, payload)
#     flush(handle)
#     close(handle)

module KafkaAdapter

export Transport, RDKafkaTransport, NullTransport, transport_available,
       make_consumer, make_producer, poll_message, produce_message,
       flush_producer, close_transport, transport_name

abstract type Transport end

"""
    RDKAFKA_AVAILABLE[]

Set at load time. When RDKafka.jl cannot be loaded the worker still starts,
serves /metrics and logs clearly that it is running without a broker --
which is what keeps `docker compose up` from failing on a dev laptop.
"""
const RDKAFKA_AVAILABLE = Ref(false)
const RDK = Ref{Module}()

function __init__()
    try
        RDK[] = Base.require(Base.PkgId(
            Base.UUID("43e2f499-f4c7-585f-8317-cbc2d9c3bf8f"), "RDKafka"))
        RDKAFKA_AVAILABLE[] = true
    catch
        RDKAFKA_AVAILABLE[] = false
    end
    return nothing
end

transport_available() = RDKAFKA_AVAILABLE[]

# ------------------------------------------------------------------ RDKafka --
struct RDKafkaTransport <: Transport
    handle::Any
    kind::Symbol          # :consumer | :producer
end

# --------------------------------------------------------------------- null --
"""
    NullTransport

Used when RDKafka.jl is unavailable. Consumers return no messages; producers
count what they were given so `/metrics` still reports real numbers and the
worker can be exercised end-to-end without a broker.
"""
mutable struct NullTransport <: Transport
    produced::Int
    kind::Symbol
end
NullTransport(kind::Symbol) = NullTransport(0, kind)

transport_name(::RDKafkaTransport) = "rdkafka"
transport_name(::NullTransport)    = "null (RDKafka.jl unavailable)"

# ------------------------------------------------------------------- consume --
function make_consumer(brokers::AbstractString, group::AbstractString,
                       topics::Vector{String})
    if !transport_available()
        @warn "RDKafka.jl unavailable -- consuming nothing" brokers group topics
        return NullTransport(:consumer)
    end
    M = RDK[]
    conf = Dict(
        "auto.offset.reset"  => "latest",
        "enable.auto.commit" => "true",
    )
    c = M.KafkaConsumer(String(brokers), String(group), conf)
    M.subscribe(c, [(topic, -1) for topic in topics])
    return RDKafkaTransport(c, :consumer)
end

"""
    poll_message(t, timeout_ms) -> (topic, key, payload) or nothing
"""
function poll_message(t::RDKafkaTransport, timeout_ms::Integer = 200)
    M = RDK[]
    msg = M.poll(t.handle, Int(timeout_ms))
    msg === nothing && return nothing
    msg.err == 0 || return nothing
    key = msg.key isa Vector{UInt8} ? msg.key : UInt8[]
    payload = msg.payload isa Vector{UInt8} ? msg.payload : UInt8[]
    return (String(msg.topic.topic), key, payload)
end
poll_message(::NullTransport, timeout_ms::Integer = 200) = nothing

# ------------------------------------------------------------------- produce --
function make_producer(brokers::AbstractString)
    if !transport_available()
        @warn "RDKafka.jl unavailable -- producing to /dev/null" brokers
        return NullTransport(:producer)
    end
    M = RDK[]
    return RDKafkaTransport(M.KafkaProducer(String(brokers), Dict(
        "linger.ms"         => "20",
        "compression.type"  => "lz4",
    )), :producer)
end

function produce_message(t::RDKafkaTransport, topic::AbstractString,
                         key::AbstractString, payload::Vector{UInt8})
    RDK[].produce(t.handle, String(topic), -1, Vector{UInt8}(codeunits(key)), payload)
    return true
end
function produce_message(t::NullTransport, topic::AbstractString,
                         key::AbstractString, payload::Vector{UInt8})
    t.produced += 1
    return false
end

flush_producer(::RDKafkaTransport) = nothing
flush_producer(::NullTransport) = nothing

close_transport(::RDKafkaTransport) = nothing
close_transport(::NullTransport) = nothing

end # module KafkaAdapter
