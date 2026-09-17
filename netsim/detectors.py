"""
Detection statistics on per-sensor 1 s spike counts, and network-level rules.

Single-node statistics (all positive when the rate *drops*, since the
perturbation silences units; computed only from the `baseline` window
onwards, zero before):

  zscore   (mu - x_t) / sd
  cusum    one-sided CUSUM of standardised drops, S_t = max(0, S_{t-1} + z_t - kappa)
  bocpd    Bayesian online changepoint detection (Adams & MacKay 2007) with a
           Normal observation model of known variance and Normal prior on the
           mean; statistic = posterior probability that the *current* segment
           has a mean at least `delta` baseline-sd below the baseline mean,
           i.e. P(we are in a depressed regime). Unlike P(recent change) this
           stays high while the depression persists, which the sustained
           alarm rule needs.

Network rules turn the (n_sensors, T) statistic into one series S_t:

  max      max over sensors (the naive rule; every sensor is its own alarm)
  pool     each sensor averages its k nearest neighbours (incl. itself),
           rescaled by sqrt(k); then max over sensors. Exploits spatial
           coherence of a spreading front.
  vote     number of sensors with statistic > vote_level (a per-detector level:
           2 baseline-sd for z-score and CUSUM, posterior probability 0.5 for BOCPD)

`alarm_time` applies a sustained-exceedance rule: h must be exceeded for
`hold` consecutive bins; the alarm is stamped at the end of the hold.
"""
from __future__ import annotations

import numpy as np

DETECTORS = ("zscore", "cusum", "bocpd")
RULES = ("max", "pool", "vote")
# per-detector "this sensor is individually suspicious" level used by the vote rule
VOTE_LEVEL = {"zscore": 2.0, "cusum": 2.0, "bocpd": 0.5}


def baseline_stats(X: np.ndarray, B: int) -> tuple[np.ndarray, np.ndarray]:
    base = X[:, :B]
    mu = base.mean(axis=1)
    sd = base.std(axis=1, ddof=1) if B > 1 else np.sqrt(np.maximum(mu, 1.0))
    sd = np.maximum.reduce([sd, np.sqrt(np.maximum(mu, 0.0)) * 0.5, np.full_like(mu, 0.5)])
    return mu, sd


def zscore_stat(X: np.ndarray, B: int) -> np.ndarray:
    mu, sd = baseline_stats(X, B)
    z = (mu[:, None] - X) / sd[:, None]
    z[:, :B] = 0.0
    return z


def cusum_stat(X: np.ndarray, B: int, kappa: float = 0.5) -> np.ndarray:
    z = zscore_stat(X, B)
    n, T = z.shape
    S = np.zeros_like(z)
    acc = np.zeros(n)
    for t in range(B, T):
        acc = np.maximum(0.0, acc + z[:, t] - kappa)
        S[:, t] = acc
    return S


def bocpd_stat(X: np.ndarray, B: int, hazard: float = 1.0 / 200.0, kappa0: float = 0.1,
               delta: float = 0.5) -> np.ndarray:
    """
    Posterior probability that the current segment's mean is below
    mu - delta*sd, summed over run lengths.
    Observation x ~ N(m, sd^2) with sd from the baseline (known); prior
    m ~ N(mu, sd^2 / kappa0) (kappa0 small = a new segment may sit anywhere).
    Vectorised over sensors, loop over time.
    The recursion is warmed up over the baseline bins (so the run-length
    posterior has settled by the time monitoring starts); the statistic is
    reported only for t >= B.
    """
    mu, sd = baseline_stats(X, B)
    n, T = X.shape
    out = np.zeros((n, T))
    var = sd ** 2
    R = np.zeros((n, T + 1))
    R[:, 0] = 1.0
    kap = np.full((n, T + 1), kappa0)
    mean = np.tile(mu[:, None], (1, T + 1))
    for t in range(T):
        x = X[:, t][:, None]
        L = t + 1  # live run lengths 0..L-1
        pred_var = var[:, None] * (1.0 + 1.0 / kap[:, :L])
        logp = -0.5 * np.log(2 * np.pi * pred_var) - 0.5 * (x - mean[:, :L]) ** 2 / pred_var
        p = np.exp(logp - logp.max(axis=1, keepdims=True))
        growth = R[:, :L] * p * (1.0 - hazard)
        cp = (R[:, :L] * p).sum(axis=1) * hazard
        newR = np.zeros((n, L + 1))
        newR[:, 1:] = growth
        newR[:, 0] = cp
        newR /= newR.sum(axis=1, keepdims=True) + 1e-300
        new_mean = np.empty((n, L + 1))
        new_kap = np.empty((n, L + 1))
        new_mean[:, 1:] = (kap[:, :L] * mean[:, :L] + x) / (kap[:, :L] + 1.0)
        new_kap[:, 1:] = kap[:, :L] + 1.0
        new_mean[:, 0] = mu
        new_kap[:, 0] = kappa0
        R[:, :L + 1] = newR
        mean[:, :L + 1] = new_mean
        kap[:, :L + 1] = new_kap
        if t >= B:
            depressed = new_mean < (mu - delta * sd)[:, None]
            out[:, t] = (newR * depressed).sum(axis=1)
    return out


def node_statistic(name: str, X: np.ndarray, B: int) -> np.ndarray:
    if name == "zscore":
        return zscore_stat(X, B)
    if name == "cusum":
        return cusum_stat(X, B)
    if name == "bocpd":
        return bocpd_stat(X, B)
    raise ValueError(f"unknown detector {name!r}; choose from {DETECTORS}")


# ------------------------------------------------------------- network rules --
def network_series(stat: np.ndarray, rule: str, nbrs: np.ndarray | None = None,
                   vote_level: float = 2.0) -> np.ndarray:
    """Collapse an (n_sensors, T) statistic to one network series; see module docstring for the rules."""
    if stat.shape[0] == 0:
        return np.zeros(stat.shape[1])
    if rule == "max":
        return stat.max(axis=0)
    if rule == "pool":
        if nbrs is None:
            raise ValueError("pool rule needs neighbour indices")
        k = nbrs.shape[1]
        pooled = stat[nbrs].mean(axis=1) * np.sqrt(k)  # (n, T)
        return pooled.max(axis=0)
    if rule == "vote":
        return (stat > vote_level).sum(axis=0).astype(float)
    raise ValueError(f"unknown rule {rule!r}; choose from {RULES}")


def sustained_max(S: np.ndarray, B: int, hold: int) -> float:
    """Largest value h such that S exceeds h for `hold` consecutive bins after B (rolling min, then max)."""
    s = S[B:]
    if s.size < hold:
        return float("-inf")
    win = np.lib.stride_tricks.sliding_window_view(s, hold)
    return float(win.min(axis=1).max())


def alarm_time(S: np.ndarray, h: float, B: int, hold: int, dt: float = 1.0) -> float | None:
    """Time (s) at which S has exceeded h for `hold` consecutive bins, or None."""
    above = S > h
    above[:B] = False
    run = 0
    for t in range(above.size):
        run = run + 1 if above[t] else 0
        if run >= hold:
            return (t + 1) * dt
    return None


def localise(stat_at_alarm: np.ndarray, xy: np.ndarray, level: float) -> np.ndarray:
    """Weighted centroid of sensors whose statistic exceeds `level` (squared weights); fallback argmax."""
    w = np.where(stat_at_alarm > level, np.maximum(stat_at_alarm, 0.0) ** 2, 0.0)
    if w.sum() <= 0:
        return xy[int(np.argmax(stat_at_alarm))]
    return (w[:, None] * xy).sum(axis=0) / w.sum()
