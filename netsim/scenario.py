"""
One simulated run of a sensor network under (or not under) a spreading perturbation.

Pipeline per run
  1. lay out n sensors (sensors.layout)
  2. assign each sensor a real-fitted renewal unit (ml/artifacts/twin_params.json)
  3. build the rate modulation from the perturbation field (field.Perturbation)
  4. generate spikes by time-rescaled gamma renewal (renewal.spike_train)
  5. bin to 1 s counts, apply measurement noise (multiplicative log-normal)

The returned Run carries what the detectors need plus the ground truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .field import Perturbation, random_perturbation
from .renewal import RenewalParams, bin_counts, load_twin_params, spike_train
from .sensors import layout, neighbours


@dataclass(frozen=True)
class Config:
    n_sensors: int = 36
    topology: str = "grid"
    noise: float = 0.15            # sd of log-normal multiplicative measurement noise on counts
    domain: tuple[float, float] = (100.0, 100.0)
    duration_s: float = 150.0
    baseline_s: float = 30.0
    onset_s: float = 40.0
    speed: float = 1.5             # domain units / s
    tau_s: float = 8.0
    dt: float = 1.0
    knn: int = 4
    hold: int = 3
    min_rate_hz: float = 2.0       # units below this are too sparse for second-scale detection
    rate_jitter: float = 0.10      # log-normal sd applied to each sensor's rate

    @property
    def n_bins(self) -> int:
        return int(round(self.duration_s / self.dt))

    @property
    def baseline_bins(self) -> int:
        return int(round(self.baseline_s / self.dt))


@dataclass
class Run:
    cfg: Config
    seed: int
    xy: np.ndarray                    # (n, 2)
    params: list[RenewalParams]
    counts: np.ndarray                # (n, T) noisy 1 s counts
    clean_counts: np.ndarray          # (n, T) before measurement noise
    nbrs: np.ndarray                  # (n, k)
    perturbation: Perturbation | None
    t_symptom: float | None           # conventional endpoint (first collapse at the origin), s; None for null runs
    stats: dict = field(default_factory=dict)   # detector name -> (n, T) statistic (filled lazily)


_UNIT_CACHE: dict[str, tuple[list[RenewalParams], dict]] = {}


def unit_pool(min_rate_hz: float) -> tuple[list[RenewalParams], dict]:
    key = f"{min_rate_hz}"
    if key not in _UNIT_CACHE:
        units, meta = load_twin_params()
        kept = [u for u in units if u.rate_hz >= min_rate_hz]
        _UNIT_CACHE[key] = (kept or units, meta)
    return _UNIT_CACHE[key]


def simulate(cfg: Config, seed: int, perturbed: bool = True) -> Run:
    rng = np.random.default_rng(seed)
    xy = layout(cfg.topology, cfg.n_sensors, cfg.domain, rng)
    n = xy.shape[0]
    units, _ = unit_pool(cfg.min_rate_hz)
    idx = rng.integers(0, len(units), n)
    params = []
    for i in idx:
        u = units[i]
        r = u.rate_hz * float(np.exp(rng.normal(0, cfg.rate_jitter)))
        params.append(RenewalParams(u.shape, r, u.refractory_ms))

    T = cfg.n_bins
    pert = random_perturbation(rng, cfg.domain, cfg.onset_s, cfg.speed, cfg.tau_s) if perturbed else None
    mod = pert.modulation(xy, T, cfg.dt) if pert else np.ones((n, T))

    clean = np.empty((n, T))
    for i in range(n):
        sp = spike_train(params[i], cfg.duration_s, rng, modulation=mod[i], dt=cfg.dt)
        clean[i] = bin_counts(sp, cfg.duration_s, cfg.dt)
    noisy = clean * np.exp(rng.normal(0, cfg.noise, size=clean.shape)) if cfg.noise > 0 else clean.copy()

    return Run(cfg=cfg, seed=seed, xy=xy, params=params, counts=noisy, clean_counts=clean,
               nbrs=neighbours(xy, cfg.knn), perturbation=pert,
               t_symptom=pert.symptom_onset() if pert else None)
