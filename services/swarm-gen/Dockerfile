# syntax=docker/dockerfile:1
FROM rust:1-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends cmake build-essential pkg-config zlib1g-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
# cache dependency build
RUN mkdir -p src && echo 'fn main(){}' > src/main.rs && cargo build --release && rm -rf src
COPY src ./src
RUN touch src/main.rs && cargo build --release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/target/release/swarm-gen /usr/local/bin/swarm-gen
ENV KAFKA_BROKERS=redpanda:9092 SWARM_NODES=100000 SWARM_SHARDS=100 BIN_MS=100 SEED=42 METRICS_PORT=9100 \
    GRID_W=400 GRID_H=250 TWIN_PARAMS=/app/ml/artifacts/twin_params.json RUST_LOG=info
EXPOSE 9100
ENTRYPOINT ["swarm-gen"]
