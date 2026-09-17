# WS-C — Julia ML: data fitting, features, detectors, feature worker

Dirs: `ml/`, `services/feature-worker/`, `data/`.
Contract: `proto/WIRE.md` §4 (SwarmBinFrame in, FeatureFrame out), §7 (alerts); twin parameters via `ml/artifacts/twin_params.json`.

```
data/fetch.sh ──▶ data/raw/*.h5 ──▶ ml/scripts/prepare.jl ──▶ ml/artifacts/twin_params.json ──▶ swarm-gen
                                    ml/scripts/train.jl   ──▶ ml/artifacts/gru.bson (gitignored) ──▶ feature-worker
                                    ml/scripts/eval.jl    ──▶ docs/EVAL.md
                                    ml/scripts/make_template.jl ──▶ firmware/hil-node/include/template.h
swarm.bins ──▶ feature-worker (1 s windows, 8 features, stress index) ──▶ neuro.features, neuro.alerts
```

## Components

| Path | What | Status |
|---|---|---|
| `ml/src/CockroachML.jl` | gamma-renewal + absolute-refractory MLE (Minka seed, Newton on shape, golden-section profile on refractory) | tested (`ml/test/runtests.jl`, parameter recovery) |
| `ml/src/dataio.jl` | HDF5 loaders for Zenodo 14281 / 14607, CRCNS ia-1 reader, labelled synthetic fallback | tested |
| `ml/src/features.jl` | 8 features per 1 s window: rate, ISI mean, ISI CV, burst index, Fano, rate slope, band power, SNR | tested against analytic expectations |
| `ml/src/models.jl` | `ZScoreDetector` (diagonal Hotelling T²), `GRUDetector` (Flux `GRU(8=>32)`, 30-window sequences, 4-class softmax) | tested (warm-up, silence) |
| `ml/src/sim.jl` | seeded twin simulator mirroring `services/swarm-gen/src/twin.rs`; produces training labels | tested (determinism) |
| `ml/scripts/prepare.jl` | fit units → `twin_params.json` | committed fit from 16 real units |
| `ml/scripts/train.jl` | train GRU → `gru.bson` | runs; artifact **not shipped** (gitignored) |
| `ml/scripts/eval.jl` | matched-false-alarm-rate evaluation, 5 seeds → `docs/EVAL.md` | generated report committed |
| `ml/scripts/make_template.jl` | emit the firmware `TPL_*` ingredient header from a real unit | rewritten to the firmware format; not yet run on a machine with Julia + data |
| `services/feature-worker/` | Kafka consumer/producer, windowing, stress index, alert transitions | 7 testsets; Dockerfile runs the suite at build |

## Run

```bash
./data/fetch.sh                      # ~3.5 MB from Zenodo
julia --project=ml -e 'using Pkg; Pkg.instantiate()'
julia --project=ml ml/scripts/prepare.jl        # twin_params.json
just train                                      # gru.bson (several minutes on CPU)
just eval                                       # docs/EVAL.md
just template                                   # firmware header from a real unit
just ml-test
just worker-run                                 # DETECTOR=gru|zscore, GRU_MODEL=path
```

## Detector selection in the worker

`DETECTOR` defaults to `gru`. If `ml/artifacts/gru.bson` (or `GRU_MODEL`) is
missing or fails to load, `resolve_engine` in `services/feature-worker/src/worker.jl`
fail-closes to the 1-D firing-rate z-score and records `fallback_reason`
(`missing_artifact` or `load_error`) in the status it publishes. **A fresh
clone therefore runs the z-score path until `just train` has been executed.**
Dashboards and docs must say which detector produced a number.

## Results (from `docs/EVAL.md`, seeded, matched false-alarm rate)

| Detector | Lead time vs simulated collapse | Notes |
|---|---|---|
| GRU | 12.41 ± 1.84 s | toxin class only is reliably separated |
| z-score | 8.18 ± 3.78 s | fallback path |

Ground truth is the simulator's own collapse endpoint; this is a
pipeline-validation number, not a biological lead time. See `docs/METHODS.md` §7.

## Known gaps

- `twin_params.json` field `rate_hz` is the gamma rate parameter, not the firing rate; `mean_rate_hz` was added on 2026-09-10 after the Python refit exposed the mismatch in swarm-gen (see `docs/netsim/FIT_CHECK.md`). `docs/EVAL.md` is unaffected (`ml/src/sim.jl` always used the correct conversion) but predates the field.

- `drug` and `radiation` recall are ≈ 0 for both detectors; the 4-class head
  is effectively binary (baseline vs toxin).
- `gru.bson` is not distributed; either ship it as a release asset or accept
  the documented z-score fallback on fresh clones.
- `make_template.jl` has not been executed since the rewrite to the `TPL_*`
  format; `firmware/hil-node/include/template.h` is still the placeholder.
- `RDKafka.jl` is lightly maintained; `services/feature-worker/src/kafka.jl`
  guards it behind a runtime `Base.require` with a `NullTransport` fallback.
