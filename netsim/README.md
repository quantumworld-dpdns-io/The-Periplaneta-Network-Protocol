# netsim — bio-sensor network design study

**Question.** Given single-node spike statistics fitted to real insect
recordings, how many sensors, in what layout, and with what collective
inference rule are needed to detect a spatially spreading neurotoxic
perturbation before the conventional endpoint (first loss of function at the source, which does not depend on where the sensors are), at a
matched false-alarm rate — and how well can the network locate the source?

This is the *networking* face of The Periplaneta Protocol. The platform's
streaming stack (`services/`, `dashboard/`) moves the data; `netsim` answers
the design question of how much sensing is enough.

## What is empirical and what is simulated

| Quantity | Source |
|---|---|
| Per-unit ISI statistics: gamma shape, rate, absolute refractory period | **Empirical.** `ml/artifacts/twin_params.json`, MLE fits to Zenodo 14281 (cockroach antennal lobe, CC-BY-4.0) and Zenodo 14607 (locust, CC0). `netsim.renewal.fit_gamma_renewal` re-implements the estimator and is tested for parameter recovery; `python -m netsim.cli fit-real` refits the cockroach file when it is on disk. |
| Perturbation geometry: radial front, speed, exponential rate decay behind it, collapse endpoint | **Simulated** (phenomenological; mirrors `services/swarm-gen/src/twin.rs`). |
| Measurement noise | **Simulated**: multiplicative log-normal on 1 s counts. |
| Sensor layouts | grid / random / clustered. |

Ground truth is therefore simulated by construction. That is the normal way a
sensor network is designed, but it means the lead times below are design
quantities, not biological ones.

## Pipeline

```
twin_params.json ─┐
                  ├─▶ scenario.simulate ──▶ 1 s counts (n_sensors × T) ──▶ detectors.node_statistic ──▶ detectors.network_series ──▶ alarm
field.Perturbation┘        │                                              zscore | cusum | bocpd         max | pool(k-NN) | vote
                           └─▶ ground truth: t_symptom (first collapse at the origin), origin
```

Thresholds are calibrated per pipeline on null runs to the same per-run
false-alarm probability (`alpha`, default 0.05), realised FAR is reported on
held-out null runs, and perturbed runs give detection probability, lead time
`t_symptom − t_alarm`, and localisation error (estimated vs true origin, as a
fraction of the domain diagonal).

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r netsim/requirements.txt
.venv/bin/python -m pytest -q netsim/tests
.venv/bin/python -m netsim.cli describe
.venv/bin/python -m netsim.cli run --n 36 --topology grid --noise 0.15 --detector cusum --rule pool --seed 1
.venv/bin/python -m netsim.cli sweep --quick        # ~1 min smoke sweep
bash netsim/reproduce.sh                            # full sweep: tests + sweep + RESULTS.md + PNGs
```

Outputs land in `netsim/out/` (gitignored): `summary.csv` (one row per cell ×
pipeline), `runs.csv` (one row per perturbed run), `RESULTS.md`, and
`design_curves_noise*.png`.

## Submission artefacts

`docs/netsim/` holds the committed copies of the study outputs and the
write-up: `RESULTS.md` and `design_curves_noise0.15.png` (from the sweep),
`ABSTRACT.md` → `ABSTRACT.pdf` (one A4 page, nothing below 12 pt, built by
`python -m netsim.make_abstract_pdf`), and `VIDEO_SCRIPT.md` →
`VIDEO_SCRIPT.srt` (timed subtitles, `python -m netsim.make_subtitles`).
`python -m netsim.verify_abstract` recomputes every number quoted in the
abstract from `netsim/out/summary.csv` and fails on any mismatch; the
reproduction script runs all three after the sweep.

## MCP server

`python -m netsim.mcp_server` starts a stdio Model Context Protocol server
with tools `describe_model`, `run_scenario`, `design_sweep`, `fit_renewal`, so
an AI research agent can drive parameter sweeps and read back the same numbers
the CLI produces. Client config example (Claude Desktop / any MCP host):

```json
{ "mcpServers": { "periplaneta-netsim": { "command": "/abs/path/.venv/bin/python", "args": ["-m", "netsim.mcp_server"], "cwd": "/abs/path/The-Periplaneta-Protocol" } } }
```

## Limitations

- Rate is the only per-node feature; ISI-shape features and the learned GRU detector live in `ml/` and are not used here.
- The front is isotropic and the decay purely exponential; no recovery, no drug or radiation dynamics.
- Detection uses 1 s bins and a 3-bin sustain rule; sub-second latency is out of scope.
- `clustered` layouts are random Gaussian clusters, a stand-in for convenience placement, not a model of any specific site.
