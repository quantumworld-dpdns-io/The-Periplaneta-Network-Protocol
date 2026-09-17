# The Periplaneta Protocol




A cockroach-based bio-industrial **sensing / networking / evolving simulation
platform**. This repository is the sensing-and-networking backbone: a
zero-in-vivo neurophysiology telemetry pipeline that asks how early
continuous spike-train telemetry can flag a neurotoxic, pharmacological or
radiological perturbation, and how a network of such sensors should be laid
out to do it.

What runs today, stated precisely:

- **Digital twins.** A Rust generator simulates 100,000 gamma-renewal spiking
  nodes whose per-unit parameters are fit by maximum likelihood to **real,
  openly licensed insect recordings** (Zenodo 14281 cockroach, Zenodo 14607
  locust; see `data/README.md`). Interventions spread radially across the grid
  and emit ground-truth events.
- **Streaming.** Everything flows through Redpanda (Kafka API) under a
  byte-level wire contract (`proto/WIRE.md`) that every service unit-tests.
- **Features and stress index.** A Julia worker computes eight spike-train
  features per node per second and scores a Neural Stress Index with a
  tiny Transformer (default), a Flux GRU, or a rate z-score. Weights live in
  `ml/artifacts/{transformer,gru}.bson` after `just train --arch …` and are
  gitignored; missing files fail-close down that chain and `/metrics` says so.
- **Dashboard.** A Next.js war-room: grid heatmap, per-node oscilloscope,
  feature table, alert timeline, ventral-nerve-cord map. Has a full in-browser
  mock mode (`NEXT_PUBLIC_MOCK=1`).
- **Hardware-in-the-loop path.** ESP32-S3 firmware that renders a spike
  template at 10 kHz and publishes raw frames over MQTT. It **compiles and
  passes host tests but has not been flashed to a board**, and the committed
  template is still the **synthetic placeholder**; `just template` regenerates
  it from the real recordings on a machine with Julia and the data. The
  scripted demo uses the Python emulator instead.
- **Sensor-network design study (`netsim/`).** A Python module that asks the
  networking question directly: given the real-fitted single-node statistics,
  how many sensors, in what layout, with which collective rule (z-score /
  CUSUM / Bayesian online changepoint × max / k-NN pooling / vote) detect a
  spreading perturbation before the first collapse at the source, at a matched
  false-alarm rate, and how well do they locate it. Reproducible with
  `bash netsim/reproduce.sh`; exposed to AI agents as an MCP server. See
  `netsim/README.md`.

No live animals are used anywhere in the pipeline.

Docs: `proto/WIRE.md` (wire contract) · `docs/METHODS.md` (what is real, what
is simulated, limitations) · `docs/EVAL.md` (generated evaluation) ·
`docs/DEMO.md` (demo runbook) · `docs/WS-A.md` … `docs/WS-D.md` (per-subsystem
run notes) · `docs/FUTURE.md`.

## Layout

| Path | What | Stack |
|---|---|---|
| `firmware/hil-node/` | ESP32-S3 spike-template player, MQTT | PlatformIO / Arduino C++ |
| `services/mqtt-bridge/` | MQTT ⇄ Redpanda bridge | Rust |
| `services/swarm-gen/` | 100k digital twins, intervention dynamics, ground truth | Rust |
| `services/api-gateway/` | WebSocket fan-out, REST interventions | Rust / axum |
| `services/feature-worker/` | spike features + stress index (z-score, GRU, Transformer) | Julia |
| `ml/` | data fitting, training, evaluation, firmware template | Julia / Flux |
| `dashboard/` | interactive, bilingual; drives the model through the API | Next.js |
| `blattella/` | **the project core**: colony, contact network, toxicology, chemistry, genetics, strategy comparison | Python |
| `netsim/` | frozen sensor-network study; `renewal.py` still serves the neural readout | Python |
| `data/` | dataset fetcher and provenance (`data/README.md`) | bash |
| `deploy/` | serves the dashboard; the old stack is in `deploy/archive/` | |

## Quick start

```bash
just venv           # python3 -m venv .venv, then the requirements
just test           # 108 tests
just compare        # the headline experiment
just docs           # regenerate every generated document, including the export
just api            # the model server on http://localhost:8000/docs
just up             # both, in containers, on http://localhost:3000
just down
```

The dashboard calls `blattella/api.py`, which runs the same code the CLI runs,
so any number on screen can be reproduced from a terminal. Every response says
how. `python -m blattella.cli export` still writes a full snapshot for anyone
who wants the whole thing as one file.

## Evaluation, honestly

`docs/EVAL.md` reports lead time against the **simulator's own collapse
endpoint**, at a matched false-alarm rate over five seeds. It validates the
pipeline; it is not a biological lead time, and only the toxin class is
reliably separated. `docs/METHODS.md` §7 lists the limitations.

## Tests

```bash
just ml-test                          # Julia: features, MLE recovery, wire offsets, detectors, sim
cargo test --manifest-path services/api-gateway/Cargo.toml   # and swarm-gen, mqtt-bridge
cd dashboard && npm test              # vitest: wire codec, catalog
just firmware-test                    # PlatformIO native host tests
just netsim-venv && just netsim-test   # Python study module (pytest)
```

## License

MIT — see `LICENSE`. Dataset licences are listed in `data/README.md`.
