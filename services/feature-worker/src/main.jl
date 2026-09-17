#!/usr/bin/env julia

include("wire.jl")
include("features.jl")
include(get(ENV, "MODELS_JL",
            joinpath(@__DIR__, "..", "..", "..", "ml", "src", "models.jl")))
include("worker.jl")
include("kafka.jl")

using .Wire
using .Worker
using .KafkaAdapter
using Sockets

const BROKERS = get(ENV, "KAFKA_BROKERS", "localhost:19092")
const METRICS_PORT = parse(Int, get(ENV, "METRICS_PORT", "9101"))
const counters = Dict(
    :messages => Ref(0),
    :feature_frames => Ref(0),
    :alerts => Ref(0),
    :decode_errors => Ref(0),
)
const detector_info = Dict(
    "requested" => "zscore",
    "active" => "zscore",
    "fallback" => "none",
)

function metrics_text()
    fallback = isempty(detector_info["fallback"]) ? "none" : detector_info["fallback"]
    gru_loaded = detector_info["active"] == "gru" ? 1 : 0
    tx_loaded = detector_info["active"] == "transformer" ? 1 : 0
    return """
# HELP feature_worker_messages_total Kafka input messages consumed
# TYPE feature_worker_messages_total counter
feature_worker_messages_total $(counters[:messages][])
# HELP feature_worker_feature_frames_total FeatureFrames produced
# TYPE feature_worker_feature_frames_total counter
feature_worker_feature_frames_total $(counters[:feature_frames][])
# HELP feature_worker_alerts_total state-transition alerts produced
# TYPE feature_worker_alerts_total counter
feature_worker_alerts_total $(counters[:alerts][])
# HELP feature_worker_decode_errors_total rejected input frames
# TYPE feature_worker_decode_errors_total counter
feature_worker_decode_errors_total $(counters[:decode_errors][])
# HELP feature_worker_detector Active vs requested detector (1 = this combination)
# TYPE feature_worker_detector gauge
feature_worker_detector{requested="$(detector_info["requested"])",active="$(detector_info["active"])",fallback="$fallback"} 1
# HELP feature_worker_gru_loaded 1 if gru.bson was loaded for live inference
# TYPE feature_worker_gru_loaded gauge
feature_worker_gru_loaded $gru_loaded
# HELP feature_worker_transformer_loaded 1 if transformer.bson was loaded for live inference
# TYPE feature_worker_transformer_loaded gauge
feature_worker_transformer_loaded $tx_loaded
"""
end

function serve_metrics(port::Int)
    server = listen(ip"0.0.0.0", port)
    @info "metrics listening" port
    while true
        socket = accept(server)
        try
            request_line = readline(socket)
            parts = split(request_line, ' ')
            path = length(parts) >= 2 ? parts[2] : "/"
            body = path == "/health" ? "ok\n" : metrics_text()
            status = path in ("/health", "/metrics") ? "200 OK" : "404 Not Found"
            path in ("/health", "/metrics") || (body = "not found\n")
            write(socket, "HTTP/1.1 $status\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: $(ncodeunits(body))\r\nConnection: close\r\n\r\n$body")
        catch err
            @debug "metrics connection failed" exception = (err, catch_backtrace())
        finally
            close(socket)
        end
    end
end

function publish_result!(producer, engine, result)
    result === nothing && return
    frame, transitions = result
    produce_message(producer, "neuro.features", string(frame.shard), encode(frame))
    counters[:feature_frames][] += 1
    for (node_id, previous, state, stress) in transitions
        payload = Vector{UInt8}(codeunits(alert_payload(frame.t_ms, node_id, previous, state, stress, engine.detector)))
        produce_message(producer, "neuro.alerts", node_hex(node_id), payload)
        counters[:alerts][] += 1
    end
end

function main()
    engine = resolve_engine()
    detector_info["requested"] = engine.requested
    detector_info["active"] = engine.detector
    detector_info["fallback"] = isempty(engine.fallback_reason) ? "none" : engine.fallback_reason

    @async serve_metrics(METRICS_PORT)
    consumer = make_consumer(BROKERS, "feature-worker", ["swarm.bins", "hil.raw"])
    producer = make_producer(BROKERS)
    transport_available() || error("RDKafka.jl is required; refusing to run without Kafka")
    @info "feature-worker started" brokers = BROKERS transport = transport_name(consumer) detector = engine.detector requested = engine.requested fallback = engine.fallback_reason

    try
        while true
            message = poll_message(consumer, 200)
            if message === nothing
                yield()
                continue
            end
            topic, _, payload = message
            counters[:messages][] += 1
            try
                if topic == "swarm.bins"
                    publish_result!(producer, engine, accept_swarm!(engine, decode_swarm_bin(payload)))
                elseif topic == "hil.raw"
                    publish_result!(producer, engine, accept_hil!(engine, decode_hil_raw(payload)))
                end
            catch err
                counters[:decode_errors][] += 1
                counters[:decode_errors][] % 100 == 1 &&
                    @warn "input frame rejected" topic exception = (err, catch_backtrace())
            end
            yield()
        end
    finally
        flush_producer(producer)
        close_transport(consumer)
        close_transport(producer)
    end
end

main()
