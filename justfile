# The Periplaneta Protocol — task runner. Install: https://github.com/casey/just
set shell := ["bash", "-cu"]
set dotenv-load := true

compose := "docker compose -f deploy/docker-compose.yml"
frozen := "docker compose -f deploy/archive/docker-compose.yml"
py := ".venv/bin/python"

default:
    @just --list

# --- the project core ------------------------------------------------------

venv:
    python3 -m venv .venv && .venv/bin/pip install -q -r netsim/requirements.txt

test:
    {{py}} -m pytest -q blattella/tests

# What is modelled, and what is still missing
describe:
    {{py}} -m blattella.cli describe

# The headline experiment: rotation vs mixture vs single product
compare *args:
    {{py}} -m blattella.cli compare {{args}}

# Refresh every generated document, including the one the dashboard renders
docs:
    {{py}} -m blattella.cli provenance --out docs/blattella/PARAMETERS.md
    {{py}} -m blattella.cli chem --out docs/blattella/DDG_COMPARISON.md
    {{py}} -m blattella.cli neural --out docs/blattella/NEURAL_PREDICTIONS.md
    {{py}} -m blattella.cli export

export:
    {{py}} -m blattella.cli export

# --- the dashboard ---------------------------------------------------------
# It renders the exported document and nothing else, so export first.

dash-dev: export
    cd dashboard && npm run dev

dash-build: export
    cd dashboard && npm run build

# Serve the dashboard at http://localhost:3000
up: export
    {{compose}} up -d --build
    @echo "dashboard: http://localhost:3000   api: http://localhost:8000/docs"

down:
    {{compose}} down -v

logs service="":
    {{compose}} logs -f {{service}}

# --- frozen: the previous direction ----------------------------------------
# Recipes for services/, deploy/archive and the streaming stack. Nothing in the
# current direction uses them; see services/README.md.

frozen-up:
    {{frozen}} up -d --build
frozen-down:
    {{frozen}} down -v


# WS-A
firmware-build:
    cd firmware/hil-node && pio run
firmware-flash:
    cd firmware/hil-node && pio run -t upload
bridge-run:
    cd services/mqtt-bridge && cargo run --release

# WS-B
swarm-run nodes="100000":
    cd services/swarm-gen && SWARM_NODES={{nodes}} cargo run --release
gateway-run:
    cd services/api-gateway && cargo run --release
# 60 s at 100k nodes; asserts bounded lag / zero drops
loadtest:
    bash services/swarm-gen/scripts/loadtest.sh

# WS-C
ml-test:
    julia --project=ml -e "using Pkg; Pkg.test()"
worker-run:
    julia --project=services/feature-worker services/feature-worker/src/main.jl
train:
    julia --project=ml ml/scripts/train.jl
eval:
    julia --project=ml ml/scripts/eval.jl

# WS-D
k8s-up:
    bash deploy/archive/k8s/up.sh
k8s-down:
    kind delete cluster --name cockroach

# Scripted demo: toxin wave + HIL mode switch
demo:
    bash docs/demo.sh
