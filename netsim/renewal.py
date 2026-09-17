"""
Gamma-renewal spike trains with an absolute refractory period, and their MLE.

Model: ISI = r + G,  G ~ Gamma(shape k, scale theta),  r >= 0 refractory.
Mean rate = 1 / (r + k*theta).  CV of the ISI = sqrt(k)*theta / (r + k*theta).

Fitting (same estimator family as ml/src/CockroachML.jl): for a fixed r the
shape/scale MLE of the shifted ISIs is the standard gamma MLE (Minka's
closed-form seed refined by Newton on the digamma equation); r is chosen by
maximising the profile log-likelihood over [0, min(ISI)) with golden-section
search. Parameters loaded from ml/artifacts/twin_params.json use the field
names shape / rate_hz / refractory_ms.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.special import digamma, gammaln, polygamma

REPO_ROOT = Path(__file__).resolve().parents[1]
TWIN_PARAMS = REPO_ROOT / "ml" / "artifacts" / "twin_params.json"


@dataclass(frozen=True)
class RenewalParams:
    shape: float
    rate_hz: float
    refractory_ms: float

    @property
    def refractory_s(self) -> float:
        return self.refractory_ms * 1e-3

    @property
    def scale(self) -> float:
        """Gamma scale theta such that mean ISI = refractory + shape*theta = 1/rate."""
        mean_isi = 1.0 / self.rate_hz
        return max(mean_isi - self.refractory_s, 1e-6) / self.shape

    @property
    def isi_cv(self) -> float:
        m = 1.0 / self.rate_hz
        return math.sqrt(self.shape) * self.scale / m


def load_twin_params(path: Path = TWIN_PARAMS) -> tuple[list[RenewalParams], dict]:
    """
    Return the fitted units and the file's provenance metadata.

    In twin_params.json `rate_hz` is the gamma *rate parameter* (1/theta), not
    the firing rate; the firing rate is `mean_rate_hz`, or, for older files,
    1 / (refractory_s + shape / rate_hz). RenewalParams.rate_hz is always the
    firing rate.
    """
    doc = json.loads(Path(path).read_text())
    units = []
    for u in doc["units"]:
        fr = u.get("mean_rate_hz") or 1.0 / (u["refractory_ms"] / 1000.0 + u["shape"] / u["rate_hz"])
        units.append(RenewalParams(u["shape"], fr, u["refractory_ms"]))
    meta = {k: v for k, v in doc.items() if k != "units"}
    return units, meta


# ----------------------------------------------------------------- sampling --
def sample_isi(p: RenewalParams, n: int, rng: np.random.Generator) -> np.ndarray:
    return p.refractory_s + rng.gamma(p.shape, p.scale, size=n)


def spike_train(p: RenewalParams, duration_s: float, rng: np.random.Generator,
                modulation: np.ndarray | None = None, dt: float = 1.0) -> np.ndarray:
    """
    Spike times in [0, duration_s).

    With `modulation` (per-`dt` multiplicative rate factor, >= 0) the train is
    generated in operational time and warped through the cumulative intensity
    Lambda(t) = int rate*m(u) du, so ISI *shape* (the CV) is preserved while
    the local rate follows m(t). m == 0 silences the unit.
    """
    if modulation is None:
        n_guess = int(duration_s * p.rate_hz * 1.5) + 50
        t = np.cumsum(sample_isi(p, n_guess, rng)) + rng.uniform(0, 1.0 / p.rate_hz)
        while t[-1] < duration_s:
            t = np.concatenate([t, t[-1] + np.cumsum(sample_isi(p, n_guess, rng))])
        return t[t < duration_s]

    m = np.clip(np.asarray(modulation, dtype=float), 0.0, None)
    n_bins = m.size
    # cumulative operational time at bin edges, in units of "expected spikes"
    lam = np.concatenate([[0.0], np.cumsum(m) * dt * p.rate_hz])
    total_ops = lam[-1]
    if total_ops <= 0:
        return np.zeros(0)
    # unit-rate renewal in operational time: rescale ISIs to mean 1
    mean_isi = 1.0 / p.rate_hz
    n_guess = int(total_ops * 1.5) + 50
    s = np.cumsum(sample_isi(p, n_guess, rng) / mean_isi) + rng.uniform(0, 1.0)
    while s[-1] < total_ops:
        s = np.concatenate([s, s[-1] + np.cumsum(sample_isi(p, n_guess, rng) / mean_isi)])
    s = s[s < total_ops]
    # invert Lambda piecewise-linearly (zero-rate bins are flat: no spikes land there)
    edges = np.arange(n_bins + 1) * dt
    t = np.interp(s, lam, edges)
    return t[t < min(duration_s, n_bins * dt)]


def bin_counts(spikes: np.ndarray, duration_s: float, dt: float = 1.0) -> np.ndarray:
    n = int(round(duration_s / dt))
    return np.histogram(spikes, bins=n, range=(0.0, n * dt))[0].astype(float)


# ---------------------------------------------------------------------- MLE --
def _gamma_mle(x: np.ndarray, iters: int = 50) -> tuple[float, float]:
    """Shape and scale MLE for positive samples (Minka 2002 seed + Newton)."""
    x = x[x > 0]
    if x.size < 2:
        return 1.0, float(np.mean(x)) if x.size else 1.0
    m = float(np.mean(x))
    s = math.log(m) - float(np.mean(np.log(x)))
    if s <= 0:
        return 1e3, m / 1e3
    k = (3.0 - s + math.sqrt((s - 3.0) ** 2 + 24.0 * s)) / (12.0 * s)
    for _ in range(iters):
        f = math.log(k) - digamma(k) - s
        fp = 1.0 / k - polygamma(1, k)
        step = f / fp
        k_new = k - step
        if k_new <= 0:
            k_new = k / 2
        if abs(k_new - k) < 1e-10 * max(1.0, k):
            k = k_new
            break
        k = k_new
    return float(k), m / float(k)


def _loglik(x: np.ndarray, k: float, theta: float) -> float:
    return float(np.sum((k - 1) * np.log(x) - x / theta) - x.size * (gammaln(k) + k * math.log(theta)))


def fit_gamma_renewal(isi: np.ndarray, refractory_max_ms: float | None = None) -> RenewalParams:
    """
    MLE of (shape, rate, refractory) from a vector of inter-spike intervals (s).
    Golden-section search on the profile likelihood over the refractory period.
    """
    isi = np.asarray(isi, dtype=float)
    isi = isi[np.isfinite(isi) & (isi > 0)]
    if isi.size < 5:
        raise ValueError("need at least 5 ISIs")
    hi = float(np.min(isi)) * 0.999
    if refractory_max_ms is not None:
        hi = min(hi, refractory_max_ms * 1e-3)
    lo = 0.0

    def profile(r: float) -> float:
        k, th = _gamma_mle(isi - r)
        return _loglik(isi - r, k, th)

    gr = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - gr * (b - a), a + gr * (b - a)
    fc, fd = profile(c), profile(d)
    for _ in range(60):
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - gr * (b - a)
            fc = profile(c)
        else:
            a, c, fc = c, d, fd
            d = a + gr * (b - a)
            fd = profile(d)
        if b - a < 1e-6:
            break
    r = (a + b) / 2
    # refractory at the boundary is indistinguishable from none; snap to 0 if the gain is tiny
    if profile(r) - profile(0.0) < 1e-3:
        r = 0.0
    k, th = _gamma_mle(isi - r)
    mean_isi = r + k * th
    return RenewalParams(shape=k, rate_hz=1.0 / mean_isi, refractory_ms=r * 1e3)


def fit_spike_train(spikes: np.ndarray) -> RenewalParams:
    return fit_gamma_renewal(np.diff(np.sort(np.asarray(spikes, dtype=float))))
