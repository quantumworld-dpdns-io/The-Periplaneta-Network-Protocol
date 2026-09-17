# syntax=docker/dockerfile:1
FROM rust:1-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends cmake build-essential pkg-config zlib1g-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
RUN mkdir -p src && echo 'fn main(){}' > src/main.rs && cargo build --release && rm -rf src
COPY src ./src
RUN touch src/main.rs && cargo build --release && cargo test --release --offline -- --test-threads=1

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/target/release/api-gateway /usr/local/bin/api-gateway
ENV KAFKA_BROKERS=redpanda:9092 API_PORT=8080 GRID_W=400 GRID_H=250 SWARM_NODES=100000 \
    CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000 RUST_LOG=info
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD curl -fsS http://localhost:8080/health || exit 1
ENTRYPOINT ["api-gateway"]
