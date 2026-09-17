"""
Sensor layouts over a rectangular domain and their neighbour structure.

  grid       near-square lattice, jittered by `jitter` * spacing
  random     uniform random positions
  clustered  a few Gaussian clusters (what you get when sensors are placed
             where it is convenient rather than where coverage demands)

`neighbours(xy, k)` returns the k nearest neighbours of each sensor including
itself, which the pooled network rule averages over.
"""
from __future__ import annotations

import math

import numpy as np

TOPOLOGIES = ("grid", "random", "clustered")


def layout(kind: str, n: int, domain: tuple[float, float], rng: np.random.Generator,
           jitter: float = 0.1, n_clusters: int = 3) -> np.ndarray:
    w, h = domain
    if n <= 0:
        return np.zeros((0, 2))
    if kind == "grid":
        cols = max(1, int(round(math.sqrt(n * w / h))))
        rows = max(1, int(math.ceil(n / cols)))
        xs = (np.arange(cols) + 0.5) * (w / cols)
        ys = (np.arange(rows) + 0.5) * (h / rows)
        pts = np.array([(x, y) for y in ys for x in xs])[:n]
        pts = pts + rng.normal(0, jitter, size=pts.shape) * np.array([w / cols, h / rows])
        return np.clip(pts, [0, 0], [w, h])
    if kind == "random":
        return np.column_stack([rng.uniform(0, w, n), rng.uniform(0, h, n)])
    if kind == "clustered":
        centres = np.column_stack([rng.uniform(0.2 * w, 0.8 * w, n_clusters), rng.uniform(0.2 * h, 0.8 * h, n_clusters)])
        sd = 0.12 * min(w, h)
        which = rng.integers(0, n_clusters, n)
        pts = centres[which] + rng.normal(0, sd, size=(n, 2))
        return np.clip(pts, [0, 0], [w, h])
    raise ValueError(f"unknown topology {kind!r}; choose from {TOPOLOGIES}")


def pairwise_dist(xy: np.ndarray) -> np.ndarray:
    d = xy[:, None, :] - xy[None, :, :]
    return np.sqrt(np.sum(d * d, axis=-1))


def neighbours(xy: np.ndarray, k: int) -> np.ndarray:
    """(n, k) indices of the k nearest sensors to each sensor, self first."""
    n = xy.shape[0]
    k = max(1, min(k, n))
    d = pairwise_dist(xy)
    return np.argsort(d, axis=1)[:, :k]


def coverage_radius(xy: np.ndarray, domain: tuple[float, float], grid: int = 40) -> float:
    """Largest distance from any point of the domain to its nearest sensor (a layout quality number)."""
    if xy.shape[0] == 0:
        return float("inf")
    w, h = domain
    gx, gy = np.meshgrid((np.arange(grid) + 0.5) * w / grid, (np.arange(grid) + 0.5) * h / grid)
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    d = np.sqrt(((pts[:, None, :] - xy[None, :, :]) ** 2).sum(-1)).min(axis=1)
    return float(d.max())
