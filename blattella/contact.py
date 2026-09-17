"""
Contact events and the contact network.

Contacts are **not prescribed**: they are observed from the behaviour layer. Two
individuals are in contact when they are within `contact_radius` of each other,
which happens because they seek the same harborages, follow the same pheromone
marks and feed at the same resources. The resulting weighted graph is what
insecticide, pathogens and aggregation pheromone actually travel along, and it
is the object later layers act on.

`ContactRecorder` accumulates contact-seconds per pair over a simulation, then
`ContactNetwork` exposes the summary an epidemiologist would ask for: degree
distribution, weighted degree, components, and who the super-spreaders are.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from .params import Param, Source, named

P = named(
    contact_radius=Param(
        2.0, "cm", Source.ASSUMPTION, sweep=(0.5, 5.0),
        note="separation below which two adults count as in contact; of the order "
             "of one body length (an adult Blattella germanica is ~1.5 cm)",
    ),
)


@dataclass
class ContactRecorder:
    """Accumulates pairwise contact duration while a colony is simulated."""

    radius: float = float(P["contact_radius"])
    weights: dict[tuple[int, int], float] = field(default_factory=lambda: defaultdict(float))
    n: int = 0
    total_time: float = 0.0

    def observe(self, colony, dt: float) -> None:
        self.n = colony.n
        self.total_time += dt
        live_idx = np.flatnonzero(colony.alive)
        if live_idx.size < 2:
            return
        tree = cKDTree(colony.xy[live_idx])
        for a, b in tree.query_pairs(self.radius):
            i, j = int(live_idx[a]), int(live_idx[b])
            self.weights[(i, j) if i < j else (j, i)] += dt

    def network(self) -> "ContactNetwork":
        return ContactNetwork(n=self.n, weights=dict(self.weights), total_time=self.total_time)


@dataclass(frozen=True)
class ContactNetwork:
    n: int
    weights: dict[tuple[int, int], float]
    total_time: float

    # ------------------------------------------------------------------ basics
    @property
    def n_edges(self) -> int:
        return len(self.weights)

    def adjacency(self) -> np.ndarray:
        a = np.zeros((self.n, self.n))
        for (i, j), w in self.weights.items():
            a[i, j] = a[j, i] = w
        return a

    def degree(self) -> np.ndarray:
        """Number of distinct partners per individual."""
        d = np.zeros(self.n, dtype=int)
        for (i, j) in self.weights:
            d[i] += 1
            d[j] += 1
        return d

    def strength(self) -> np.ndarray:
        """Total contact-seconds per individual."""
        s = np.zeros(self.n)
        for (i, j), w in self.weights.items():
            s[i] += w
            s[j] += w
        return s

    def density(self) -> float:
        possible = self.n * (self.n - 1) / 2
        return self.n_edges / possible if possible else 0.0

    def components(self) -> list[list[int]]:
        """Connected components of the unweighted contact graph."""
        adj: dict[int, set[int]] = defaultdict(set)
        for (i, j) in self.weights:
            adj[i].add(j)
            adj[j].add(i)
        seen: set[int] = set()
        out: list[list[int]] = []
        for start in range(self.n):
            if start in seen:
                continue
            stack, comp = [start], []
            seen.add(start)
            while stack:
                v = stack.pop()
                comp.append(v)
                for w in adj[v]:
                    if w not in seen:
                        seen.add(w)
                        stack.append(w)
            out.append(sorted(comp))
        return sorted(out, key=len, reverse=True)

    def largest_component_fraction(self) -> float:
        comps = self.components()
        return len(comps[0]) / self.n if comps and self.n else 0.0

    def super_spreaders(self, k: int = 5) -> list[int]:
        """Individuals with the highest weighted degree: the ones worth baiting."""
        return list(np.argsort(self.strength())[::-1][:k])

    def summary(self) -> dict:
        d, s = self.degree(), self.strength()
        return {
            "n": self.n,
            "edges": self.n_edges,
            "density": round(self.density(), 5),
            "mean_degree": round(float(d.mean()), 3) if self.n else 0.0,
            "max_degree": int(d.max()) if self.n else 0,
            "isolated": int((d == 0).sum()),
            "mean_contact_seconds": round(float(s.mean()), 2) if self.n else 0.0,
            "largest_component_fraction": round(self.largest_component_fraction(), 4),
            "observed_seconds": self.total_time,
        }
