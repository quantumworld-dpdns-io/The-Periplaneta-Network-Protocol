"""
Spatial perturbation field.

A neurotoxic front starts at `origin` at time `onset_s` and expands radially at
`speed` (domain units per second). Once the front reaches a sensor at
t_arr = onset + dist/speed, that unit's firing rate decays exponentially,
rate(t) = baseline * exp(-(t - t_arr)/tau) -- the same phenomenology as
services/swarm-gen/src/twin.rs and ml/src/sim.jl (sodium-channel blockade /
excitotoxic silencing). The conventional endpoint ("symptom onset") is the
first loss of function anywhere in the population: the units at the origin
fall below `collapse_frac` of baseline and stay there for `collapse_hold_s`.
It does not depend on where the sensors are -- that is the point: a sparse
network learns about the front late, but the animals at the origin die on the
same schedule regardless.

Everything is deterministic given the parameters; randomness lives in the
spike generation and measurement noise (scenario.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Perturbation:
    origin: tuple[float, float]
    onset_s: float
    speed: float          # domain units / s
    tau_s: float = 8.0    # rate decay time constant behind the front
    collapse_frac: float = 0.05
    collapse_hold_s: float = 2.0

    def arrival_times(self, xy: np.ndarray) -> np.ndarray:
        d = np.hypot(xy[:, 0] - self.origin[0], xy[:, 1] - self.origin[1])
        return self.onset_s + d / self.speed

    def modulation(self, xy: np.ndarray, n_bins: int, dt: float = 1.0) -> np.ndarray:
        """(n_sensors, n_bins) multiplicative rate factor per bin (bin-centre time)."""
        t = (np.arange(n_bins) + 0.5) * dt
        t_arr = self.arrival_times(xy)[:, None]
        el = t[None, :] - t_arr
        m = np.where(el > 0, np.exp(-np.clip(el, 0, None) / self.tau_s), 1.0)
        return m

    def collapse_times(self, xy: np.ndarray) -> np.ndarray:
        """Per-sensor time at which rate has been < collapse_frac*baseline for collapse_hold_s."""
        t_cross = self.arrival_times(xy) + self.tau_s * np.log(1.0 / self.collapse_frac)
        return t_cross + self.collapse_hold_s

    def symptom_onset(self) -> float:
        """Conventional endpoint: first collapse in the population (at the origin), independent of sensors."""
        return self.onset_s + self.tau_s * math.log(1.0 / self.collapse_frac) + self.collapse_hold_s


def random_perturbation(rng: np.random.Generator, domain: tuple[float, float], onset_s: float,
                        speed: float, tau_s: float = 8.0, margin: float = 0.1) -> Perturbation:
    """Origin uniform inside the domain, keeping `margin` (fraction) off the edges."""
    w, h = domain
    ox = rng.uniform(margin * w, (1 - margin) * w)
    oy = rng.uniform(margin * h, (1 - margin) * h)
    return Perturbation(origin=(float(ox), float(oy)), onset_s=onset_s, speed=speed, tau_s=tau_s)
