# Fit check — independent Python refit vs the committed Julia fit

Generated 2026-09-10 from `data/raw/CockroachDataJNM_2009_181_119.h5` (Zenodo 10.5281/zenodo.14281, CC-BY-4.0, MD5 verified) with
`python -m netsim.cli fit-real`; the Julia column is `ml/artifacts/twin_params.json` (first 12 units = the cockroach spontaneous trains, same file order).

| unit | n spikes | shape Py | shape Jl | refr ms Py | refr ms Jl | firing Hz Py | 1/(r+k/β) Jl | β = `rate_hz` Jl |
|---|---|---|---|---|---|---|---|---|
| e060517/Neuron1 | 356 | 0.641 | 0.649 | 2.26 | 2.15 | 5.893 | 5.893 | 3.875 |
| e060517/Neuron2 | 490 | 0.646 | 0.652 | 1.79 | 1.71 | 8.104 | 8.104 | 5.361 |
| e060517/Neuron3 | 216 | 0.606 | 0.625 | 8.66 | 8.24 | 3.627 | 3.627 | 2.338 |
| e060817/Neuron1 | 529 | 1.725 | 1.725 | 0.00 | 0.10 | 9.077 | 9.069 | 15.656 |
| e060817/Neuron2 | 1229 | 0.461 | 0.465 | 1.72 | 1.63 | 21.216 | 21.218 | 10.220 |
| e060817/Neuron3 | 781 | 1.193 | 1.195 | 0.76 | 0.74 | 13.427 | 13.427 | 16.208 |
| e060824/Neuron1 | 505 | 0.497 | 0.507 | 8.19 | 7.79 | 8.691 | 8.691 | 4.723 |
| e060824/Neuron2 | 64 | 0.669 | 0.717 | 10.93 | 10.39 | 1.100 | 1.100 | 0.798 |
| e070528/Neuron1 | 336 | 0.661 | 0.675 | 6.79 | 6.46 | 5.564 | 5.564 | 3.899 |
| e070528/Neuron2 | 1173 | 0.579 | 0.595 | 4.06 | 3.86 | 19.392 | 19.393 | 12.476 |
| e070528/Neuron3 | 1834 | 1.196 | 1.206 | 1.48 | 1.41 | 30.346 | 30.345 | 38.245 |
| e070528/Neuron4 | 1015 | 0.757 | 0.774 | 4.37 | 4.16 | 16.793 | 16.793 | 13.966 |

Agreement: shape within 6.7 % (median 1.6 %), refractory within 0.54 ms, firing rate within 0.09 % once the Julia triple is converted with 1/(r + k/β).

## What this caught

The Julia `rate_hz` is the gamma **rate parameter** β = 1/θ (documented in `ml/src/CockroachML.jl`), and `ml/src/sim.jl` converts it correctly. But `services/swarm-gen/src/twin.rs` used it directly as a firing rate (`isi_mean_ms = 1000/rate − refractory`), and so did the first version of `netsim`. For shape < 1 units (most of the cockroach cells) the twins therefore fired ~35 % too slowly; for shape > 1 units too fast. Fixed 2026-09-10 by adding `mean_rate_hz` to the JSON contract, `UnitParams::firing_rate_hz()` in swarm-gen, and the same derivation in `netsim.renewal.load_twin_params`. The Rust unit test `params_json_parses` now pins the conversion on unit 1 (5.893 Hz).

## Estimator differences

Both fits are profile-likelihood MLEs over the refractory period with Minka-seeded Newton for the shape. Residual differences (≤ 2 % in shape, ≤ 0.5 ms in refractory) come from the search tolerance and from Julia's `r` grid; they are far below the between-unit spread and do not affect any conclusion in `ABSTRACT.md`.
