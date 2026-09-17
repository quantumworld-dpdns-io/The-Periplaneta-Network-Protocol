# deploy/

Three ways to see the project, cheapest first.

## 1. From the command line, no containers

The core is a Python package. This is the fastest way to see the actual result.

```bash
just venv                                    # once
just compare                                 # the headline experiment
just describe                                # what is modelled, what is not
.venv/bin/python -m blattella.cli neural     # the falsifiable predictions
```

`just compare` writes `docs/blattella/STRATEGY_COMPARISON.md`, the per-run CSV
and a figure.

## 2. The interactive dashboard, in dev mode

Two terminals, because the pages call the model server:

```bash
just api                 # uvicorn on :8000, with reload
just dash-dev            # next dev on :3000
open http://localhost:3000
```

## 3. Everything in containers

```bash
just up                  # builds the API and the dashboard, waits for health
open http://localhost:3000
open http://localhost:8000/docs      # the API, with its schema
just down
```

`DASHBOARD_PORT` and `API_PORT` move the ports. `PUBLIC_API_URL` sets the
address the **browser** uses to reach the API; it is inlined into the client
bundle at build time, so change it before building, not after.

### How the interactivity works

Every control posts to the API, which runs the real simulation and returns the
numbers together with the command line that would reproduce them. Nothing is
precomputed, so a page can take a moment while a hundred generations actually
run. Runs are capped (`GET /api/health` lists the limits) so a slider cannot
hang the server.

### The live colony on the homepage

The front page runs a colony in real time. `POST /api/live` opens a session on
the server; the browser then holds an `EventSource` on
`GET /api/live/{id}/stream`, which advances the simulation and pushes a frame
several times a second. Each frame carries the heat grids (aggregation
pheromone, insecticide residue, and where the animals are, downsampled to 48
cells a side and quantised to a byte), every animal's position, state and
poison load, and the contact-network summary. A frame is about 15 KiB.

`POST /api/live/{id}/act` is the other half: pause, speed, hold the lights,
bait a station or clear it, let animals in, run a trap, and change how strongly
the animals attract one another or how concentrated the bait is. Sessions are
capped at eight, reaped after fifteen idle minutes, and the stream has a frame
ceiling, so a browser tab that vanishes cannot leave a colony running.

One thing the live view makes visible that a summary hides: animals walk to
their **nearest** food, so a station that is nearest to no harborage is never
visited and baiting it kills nobody. Each station is labelled with how many
animals have used it, unused ones are drawn dimmed, and the bait button treats
the busiest station rather than the first one. Clicking a station baits it
directly, because placement is a real control decision.

`python -m blattella.cli export` still writes a full snapshot to
`dashboard/public/blattella.json`. The pages no longer read it; it is kept as a
downloadable artifact and as a shape check in CI.

## archive/

Everything from the project's previous direction: the Redpanda and Mosquitto
streaming stack, the three Rust services, the Julia worker, the Kubernetes
manifests and the kind cluster. It is kept so the history stays legible.
Nothing in the current direction uses it. See [`archive/README.md`](archive/README.md).
