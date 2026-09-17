"""
One simulated colony, held so several tables can be cut from it.

`/api/contact` and `cli bait` each build a colony this way. Keeping the objects
rather than a summary is the point: the survival table and the contact edge list
both need the raw arrays, and both were previously impossible to obtain because
every caller threw them away and kept only `network.summary()`.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..arena import default_arena
from ..behaviour import Colony, make_colony, simulate
from ..contact import ContactNetwork, ContactRecorder
from ..toxicology import ACTIVES, Toxicology


@dataclass(frozen=True)
class ColonyRun:
    colony: Colony
    network: ContactNetwork
    tox: Toxicology | None
    hours: float
    seed: int
    bait: str | None
    stations: int

    @property
    def config(self) -> dict:
        return {"colony": self.colony.n, "hours": self.hours, "seed": self.seed,
                "bait": self.bait or "", "stations": self.stations if self.bait else 0,
                "arena_cm": self.colony.arena.width,
                "harborages": len(self.colony.arena.harborages),
                "resources": len(self.colony.arena.resources)}

    @property
    def reproduce(self) -> str:
        base = (f"python -m blattella.cli bait --n {self.colony.n} "
                f"--hours {self.hours:g} --seed {self.seed}")
        return base + (f" --bait {self.bait} --stations {self.stations}" if self.bait else "")


def colony_run(*, colony: int = 150, hours: float = 4.0, harborages: int = 6,
               resources: int = 3, arena_cm: float = 300.0, bait: str | None = None,
               stations: int = 1, seed: int = 1) -> ColonyRun:
    if bait is not None and bait not in ACTIVES:
        raise KeyError(f"unknown active {bait!r}; have {sorted(ACTIVES)}")

    arena = default_arena(n_harborages=harborages, n_resources=resources,
                          width=arena_cm, height=arena_cm, seed=seed)
    c = make_colony(n=colony, seed=seed, arena=arena)
    c.t = 12 * 3600.0                       # start at dusk, when they come out
    rec = ContactRecorder()
    tox = None
    observers: list = []
    if bait and stations:
        tox = Toxicology(colony=c, actives=tuple(ACTIVES.values()), seed=seed)
        tox.treat_resource(bait, list(range(min(stations, resources))))
        observers.append(tox)
    simulate(c, hours * 3600.0, 1.0, recorder=rec, observers=observers)
    return ColonyRun(colony=c, network=rec.network(), tox=tox, hours=hours,
                     seed=seed, bait=bait, stations=stations)
