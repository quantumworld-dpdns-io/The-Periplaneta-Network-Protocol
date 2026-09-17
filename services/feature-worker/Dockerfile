FROM julia:1.10-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /repo

COPY services/feature-worker/Project.toml /repo/services/feature-worker/Project.toml
WORKDIR /repo/services/feature-worker
RUN JULIA_PKG_PRECOMPILE_AUTO=0 julia --project=. -e 'using Pkg; Pkg.instantiate(); using RDKafka, Flux, BSON, NNlib'

WORKDIR /repo
COPY services/feature-worker/src /repo/services/feature-worker/src
COPY services/feature-worker/test /repo/services/feature-worker/test
COPY ml/src/models.jl /repo/ml/src/models.jl
COPY ml/artifacts /repo/ml/artifacts

WORKDIR /repo/services/feature-worker
RUN julia --project=. test/runtests.jl

ENV JULIA_PROJECT=/repo/services/feature-worker \
    JULIA_NUM_THREADS=2 \
    DETECTOR=transformer \
    GRU_MODEL=/repo/ml/artifacts/gru.bson \
    TRANSFORMER_MODEL=/repo/ml/artifacts/transformer.bson

EXPOSE 9101

CMD ["julia", "--project=/repo/services/feature-worker", "/repo/services/feature-worker/src/main.jl"]
