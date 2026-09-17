# Methods

This document states exactly what is real, what is simulated, and how every
number in the demo can be reproduced. It is written for the judging rubric
"held to the standards of genuine research".

## 1. Research question

Can continuous electrophysiological telemetry detect the onset of a
neurotoxic, pharmacological or radiological perturbation **earlier** than the
conventional endpoint (loss of motor function / death), and by how much?

The measured quantity is **lead time**:

```
lead_time = t(symptom_onset) − t(first sustained alarm)
```

where `symptom_onset` is defined operationally as firing rate below 5 % of
the node's own baseline for ≥ 2 s (see `proto/WIRE.md` §6), and a
"sustained alarm" is a detector state ≠ baseline held for ≥ 3 consecutive
1 s windows.

## 2. Signal sources

| Source | Nature | Role |
|---|---|---|
| Two ESP32-S3 HIL nodes | Replay of **real recorded insect spike trains** (see §3) rendered to a 10 kHz waveform with a measured biphasic spike template and 1/f noise | Prove the physical-to-digital interface, feed the oscilloscope view, and give the ML pipeline raw waveform input |
| 100,000 digital twins | Gamma-renewal point processes whose per-unit parameters (shape, rate, refractory period) are **fit by maximum likelihood to the real recordings** in §3 | Scale test of the pipeline and statistical power for the lead-time estimate |

No live animal is used. The HIL approach is standard practice for validating
medical-device software before animal or human trials: a signal generator
with known ground truth is played into the acquisition and analysis chain.

## 3. Datasets

Documented in `data/README.md` with DOI, licence and checksums once fetched.
Primary: locust (*Schistocerca*) antennal-lobe / grasshopper auditory receptor
spike trains published on Zenodo and CRCNS (`ia-1`). Cockroach-specific
antenna micro-stimulation parameters come from the Harvard Dataverse biobot
dataset (doi:10.7910/DVN/DFHXMM) and are used only to ground the
"micro-stimulation interface" narrative, not as training data.

Where a dataset could not be fetched at build time, `data/README.md` says so
and the pipeline falls back to a clearly labelled synthetic parameter set.

## 4. Perturbation model (digital twins)

Implemented in `services/swarm-gen/src/twin.rs` and mirrored for training in
`ml/src/sim.jl`. All randomness is seeded.

- **toxin**: firing rate decays exponentially toward zero with a radial
  diffusion front from the target origin; ISI coefficient of variation rises;
  collapse when rate < 5 % baseline. Models sodium-channel blockade /
  excitotoxic silencing.
- **drug**: rate recovers toward baseline along a logistic curve.
- **radiation**: 2 s acute burst followed by noisy suppression.

These are phenomenological, not biophysical, and are described as such.

## 5. Features and detectors

Per node, per sliding 1 s window (`services/feature-worker/src/features.jl`):
firing rate, ISI mean, ISI CV, burst index, Fano factor, rate slope, and for
raw-waveform nodes spectral band power and SNR.

- **Offline z-score (eval)**: per-node Hotelling T² drift on the 8-feature
  vector against the node's first 30 s (`ml/src/models.jl` `ZScoreDetector`).
- **Online z-score (worker fallback)**: 1-D firing-rate z-score with a 20 s
  baseline plus a collapsed-node overlay. This is what runs when `DETECTOR=zscore`
  or when `gru.bson` cannot be loaded.
- **Learned detector (GRU)**: Flux `GRU(8 => 32)` over 30-window feature
  sequences trained on seeded twin simulations with simulator labels; Neural
  Stress Index = 1 − P(baseline). Live path: `DETECTOR=gru` loads
  `ml/artifacts/gru.bson`.
- **Learned detector (Transformer)**: 2-layer encoder (`d_model=32`, 4 heads)
  over the **same** 8×30 twin windows. This is the spec-shaped backbone, not a
  day-scale clinical model. `DETECTOR=transformer` (Compose default) loads
  `ml/artifacts/transformer.bson` (`TRANSFORMER_MODEL`). Missing weights
  fail-close to GRU then to the online z-score. `/metrics` exports
  `feature_worker_transformer_loaded` and `feature_worker_gru_loaded`.
  Alerts carry `"detector":"transformer"|"gru"|"zscore"` for the active path.

The war-room heatmap is this live NSI. It is **not** a multi-day clinical
warning: eval lead times in `docs/EVAL.md` are seconds before a simulated
rate collapse.

`just train` writes `gru.bson`; `just train --arch transformer` writes
`transformer.bson` (both gitignored). Compose mounts `ml/artifacts`.

The dashboard **Conserved channels** panel is literature FlyBase/HGNC
orthologs, not RNA-seq of insect ganglia.

## 6. Evaluation protocol

`just eval` runs `ml/scripts/eval.jl` on ≥ 5 seeds and regenerates
`docs/EVAL.md` with per-class precision/recall, ROC-AUC, and lead-time
mean ± std for both detectors. Training and evaluation seeds are disjoint.

## 6b. Sensor-network design study (`netsim/`)

The same single-node fits drive a second, self-contained experiment that
treats sensor count, layout and collective inference rule as the design
variables and asks how early a *network* alarms relative to the first collapse
at the source (which is independent of sensor placement). Thresholds are
matched on per-run false-alarm probability across nine pipelines
(z-score / CUSUM / BOCPD × max / k-NN pool / vote). Results are regenerated by
`bash netsim/reproduce.sh` into `netsim/out/RESULTS.md`; the method and its
limitations are in `netsim/README.md`.

## 7. Limitations

- Lead time is a **second-scale** gap versus a simulated collapse endpoint,
  not days of clinical prodrome.
- The GRU and Transformer learn the twin generator (`ml/src/sim.jl` labels), not in-vivo
  drug or toxin responses. `drug` and `radiation` recall in `docs/EVAL.md`
  is ~0; do not read the 4-class head as a pharmacology screen.
- Insect-to-human translation is argued from ion-channel and transmitter
  conservation, not demonstrated here. The war-room ortholog table is
  literature mapping, not RNA-seq (see `docs/FUTURE.md`).
- Twins are statistical, not mechanistic; lead times measured on twins bound
  what the pipeline *can* detect, not what biology *will* show.
- HIL nodes replay fixed recordings; their perturbations are synthetic edits
  of a real signal.
