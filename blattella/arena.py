"""
Arena geometry: harborages and resources.

German cockroaches are thigmotactic and nocturnal: they spend the photophase
pressed inside narrow refuges ("harborages") and emerge to forage for food and
water. The arena is therefore not an empty box but a small number of refuge
sites plus resource sites, because that structure is what concentrates
individuals and creates the contact network we care about.

Distances are centimetres throughout; an adult is roughly 1.5 cm long.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .params import Param, Source, named

P = named(
    harborage_radius=Param(
        3.0, "cm", Source.ASSUMPTION, sweep=(1.0, 8.0),
        note="effective radius of a crevice refuge; real harborages are slits, "
             "modelled here as a disc of equivalent occupancy area",
    ),
    resource_radius=Param(
        2.0, "cm", Source.ASSUMPTION, sweep=(1.0, 5.0),
        note="radius within which an individual counts as feeding at a resource",
    ),
)


@dataclass(frozen=True)
class Site:
    x: float
    y: float
    r: float

    def contains(self, xy: np.ndarray) -> np.ndarray:
        """Boolean mask over an (n, 2) array of positions."""
        return (xy[:, 0] - self.x) ** 2 + (xy[:, 1] - self.y) ** 2 <= self.r * self.r


@dataclass(frozen=True)
class Arena:
    width: float = 100.0
    height: float = 100.0
    harborages: tuple[Site, ...] = ()
    resources: tuple[Site, ...] = ()

    @property
    def size(self) -> tuple[float, float]:
        return (self.width, self.height)

    def clip(self, xy: np.ndarray) -> np.ndarray:
        np.clip(xy[:, 0], 0.0, self.width, out=xy[:, 0])
        np.clip(xy[:, 1], 0.0, self.height, out=xy[:, 1])
        return xy

    def harborage_centres(self) -> np.ndarray:
        return np.array([[s.x, s.y] for s in self.harborages], dtype=float) if self.harborages else np.zeros((0, 2))

    def resource_centres(self) -> np.ndarray:
        return np.array([[s.x, s.y] for s in self.resources], dtype=float) if self.resources else np.zeros((0, 2))

    def in_any_harborage(self, xy: np.ndarray) -> np.ndarray:
        out = np.zeros(len(xy), dtype=bool)
        for s in self.harborages:
            out |= s.contains(xy)
        return out


def default_arena(n_harborages: int = 6, n_resources: int = 3, width: float = 300.0,
                  height: float = 300.0, seed: int = 0) -> Arena:
    """
    A kitchen-scale arena (3 m x 3 m): refuges along the walls, since cockroaches
    favour wall contact, and resource sites placed away from them so that
    foraging means leaving the refuge and crossing open floor.

    The defaults matter for the science. A small arena with few harborages packs
    the whole colony within contact range of itself, the contact graph comes out
    essentially complete, and network structure stops mattering -- which would
    quietly defeat the point of modelling a network at all.
    """
    rng = np.random.default_rng(seed)
    margin = 8.0
    edges = []
    for i in range(n_harborages):
        side = i % 4
        t = rng.uniform(0.2, 0.8)
        if side == 0:
            edges.append((t * width, margin))
        elif side == 1:
            edges.append((width - margin, t * height))
        elif side == 2:
            edges.append((t * width, height - margin))
        else:
            edges.append((margin, t * height))
    harborages = tuple(Site(x, y, float(P["harborage_radius"])) for x, y in edges)
    res = []
    for _ in range(n_resources):
        for _attempt in range(100):
            x, y = rng.uniform(0.25 * width, 0.75 * width), rng.uniform(0.25 * height, 0.75 * height)
            if all((x - h.x) ** 2 + (y - h.y) ** 2 > (4 * h.r) ** 2 for h in harborages):
                res.append((x, y))
                break
        else:
            res.append((width / 2, height / 2))
    resources = tuple(Site(x, y, float(P["resource_radius"])) for x, y in res)
    return Arena(width=width, height=height, harborages=harborages, resources=resources)
