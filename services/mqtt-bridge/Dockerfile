# mqtt-bridge: multi-stage build. librdkafka is compiled from source by
# rdkafka-sys (cmake-build feature) -> needs cmake + ninja + a C compiler.
FROM rust:1.98-bookworm AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        cmake ninja-build build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# .cargo/config.toml sets CMAKE_GENERATOR=Ninja (also needed on Windows hosts)
COPY Cargo.toml Cargo.lock* ./
COPY .cargo ./.cargo
COPY src ./src
COPY tests ./tests
RUN cargo build --release --locked 2>/dev/null || cargo build --release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/target/release/mqtt-bridge /usr/local/bin/mqtt-bridge
ENV MQTT_URL=mqtt://mosquitto:1883 \
    KAFKA_BROKERS=redpanda:9092 \
    METRICS_PORT=9102 \
    RUST_LOG=info
EXPOSE 9102
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD curl -fs http://localhost:9102/health || exit 1
ENTRYPOINT ["/usr/local/bin/mqtt-bridge"]
