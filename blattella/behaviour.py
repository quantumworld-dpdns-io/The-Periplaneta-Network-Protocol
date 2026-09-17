"""
Individual behaviour: movement, harborage use, aggregation.

This is the layer the old prototype never had. Every individual's next position
depends on where the *other* individuals are and on the pheromone they have
deposited, so removing one animal changes the trajectories of the rest. That
property is the whole point: it is what makes "cockroach network" mean a network
of cockroaches rather than a Kafka topic, and it is asserted directly in
`tests/test_behaviour.py::test_removing_an_individual_changes_the_others`.

Three coupling channels, all of them documented behaviour:

* **Aggregation pheromone.** *Blattella germanica* faeces carry an aggregation
  pheromone; individuals deposit it where they rest and are attracted up its
  gradient. This is an indirect, memory-carrying coupling (stigmergy): an animal
  that has left still influences the others through the mark it left behind.
* **Conspecific attraction.** Short-range attraction to neighbours, on top of
  the pheromone field.
* **Volume exclusion.** Below a body width neighbours push apart, which gives
  the aggregation a finite density instead of collapsing to a point.

Individuals cycle resting -> foraging -> returning -> resting. Movement is a
correlated random walk biased by the social terms plus the current goal (a
resource when hungry, the home harborage when returning). Units are centimetres
and seconds throughout.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from .arena import Arena, default_arena
from .params import Param, Source, named

RESTING, FORAGING, RETURNING = 0, 1, 2
STATE_NAMES = {RESTING: "resting", FORAGING: "foraging", RETURNING: "returning"}

P = named(
    # --- locomotion -----------------------------------------------------------
    forage_speed=Param(
        2.5, "cm/s", Source.ASSUMPTION, sweep=(1.0, 6.0),
        note="mean walking speed of a foraging adult; within the range reported "
             "for unstartled Blattella germanica locomotion",
    ),
    heading_persistence=Param(
        0.85, "dimensionless", Source.ASSUMPTION, sweep=(0.5, 0.97),
        note="autocorrelation of heading between 1 s steps (correlated random walk)",
    ),
    # --- circadian ------------------------------------------------------------
    photophase_hours=Param(
        12.0, "h", Source.LITERATURE,
        cite="standard 12:12 LD regime used in Blattella germanica husbandry and assays",
    ),
    dark_emergence_rate=Param(
        0.85, "1/min", Source.ASSUMPTION, sweep=(0.1, 2.0),
        note="hazard rate at which a hungry individual leaves the harborage "
             "during scotophase; nocturnality is well documented, the rate is not",
    ),
    light_emergence_rate=Param(
        0.05, "1/min", Source.ASSUMPTION, sweep=(0.0, 0.3),
        note="residual daytime emergence hazard",
    ),
    # --- aggregation ----------------------------------------------------------
    pheromone_deposit=Param(
        1.0, "arb/s", Source.ASSUMPTION, sweep=(0.2, 5.0),
        note="faecal aggregation-pheromone deposition rate while resting; "
             "arbitrary units, only the ratio to decay matters",
    ),
    pheromone_decay=Param(
        1.0 / 3600.0, "1/s", Source.ASSUMPTION, sweep=(1 / 86400, 1 / 600),
        note="first-order loss of the deposited mark",
    ),
    pheromone_weight=Param(
        1.2, "dimensionless", Source.ASSUMPTION, sweep=(0.0, 4.0),
        note="strength of attraction up the pheromone gradient; 0 disables the "
             "stigmergic coupling and is used as a control in tests",
    ),
    conspecific_weight=Param(
        0.6, "dimensionless", Source.ASSUMPTION, sweep=(0.0, 3.0),
        note="strength of short-range attraction to neighbours",
    ),
    conspecific_radius=Param(
        6.0, "cm", Source.ASSUMPTION, sweep=(2.0, 15.0),
        note="range over which a neighbour is attractive",
    ),
    repulsion_radius=Param(
        1.0, "cm", Source.ASSUMPTION, sweep=(0.3, 2.0),
        note="volume exclusion: below this separation neighbours push apart. "
             "Roughly a body width; without it the colony collapses to a point "
             "and every pair is permanently in contact",
    ),
    repulsion_weight=Param(
        2.5, "dimensionless", Source.ASSUMPTION, sweep=(0.5, 6.0),
        note="strength of volume exclusion; must exceed attraction at short "
             "range or aggregation has no equilibrium spacing",
    ),
    # --- feeding --------------------------------------------------------------
    hunger_rate=Param(
        1.0 / 7200.0, "1/s", Source.ASSUMPTION, sweep=(1 / 86400, 1 / 1800),
        note="rate at which satiety decays; sets how often an animal must forage",
    ),
    feed_rate=Param(
        1.0 / 120.0, "1/s", Source.ASSUMPTION, sweep=(1 / 600, 1 / 30),
        note="rate at which satiety is restored while at a resource",
    ),
    goal_weight=Param(
        1.5, "dimensionless", Source.ASSUMPTION, sweep=(0.5, 4.0),
        note="strength of the bias toward the current goal (resource or harborage)",
    ),
    harborage_switch_prob=Param(
        0.15, "per trip", Source.ASSUMPTION, sweep=(0.0, 0.6),
        note="probability that a returning individual settles in the nearest "
             "harborage rather than the one it came from. Harborage fidelity is "
             "real but imperfect; with fidelity pinned at 1 the groups become "
             "disjoint components, insecticide equilibrates inside a group and "
             "never crosses to another, and bait coverage rather than horizontal "
             "transfer decides the outcome",
    ),
)

PHERO_CELL_CM = 2.0


@dataclass
class Colony:
    """A population of individuals moving in an arena, coupled through pheromone and proximity."""

    arena: Arena
    n: int
    rng: np.random.Generator
    xy: np.ndarray = field(init=False)
    heading: np.ndarray = field(init=False)
    state: np.ndarray = field(init=False)
    satiety: np.ndarray = field(init=False)
    home: np.ndarray = field(init=False)          # index of each individual's harborage
    pheromone: np.ndarray = field(init=False)
    alive: np.ndarray = field(init=False)
    emergences: np.ndarray = field(init=False)    # foraging trips taken, per individual
    t: float = 0.0

    def __post_init__(self) -> None:
        a = self.arena
        hc = a.harborage_centres()
        if len(hc) == 0:
            raise ValueError("arena needs at least one harborage")
        self.home = self.rng.integers(0, len(hc), self.n)
        jitter = self.rng.normal(0, float(a.harborages[0].r) / 2, size=(self.n, 2))
        self.xy = a.clip(hc[self.home] + jitter)
        self.heading = self.rng.uniform(0, 2 * np.pi, self.n)
        self.state = np.full(self.n, RESTING, dtype=np.uint8)
        self.satiety = self.rng.uniform(0.3, 1.0, self.n)
        gw = int(np.ceil(a.width / PHERO_CELL_CM))
        gh = int(np.ceil(a.height / PHERO_CELL_CM))
        self.pheromone = np.zeros((gh, gw))
        self.alive = np.ones(self.n, dtype=bool)
        self.emergences = np.zeros(self.n, dtype=int)

    # ------------------------------------------------------------------ helpers
    def _cells(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        gh, gw = self.pheromone.shape
        cx = np.clip((xy[:, 0] / PHERO_CELL_CM).astype(int), 0, gw - 1)
        cy = np.clip((xy[:, 1] / PHERO_CELL_CM).astype(int), 0, gh - 1)
        return cy, cx

    def _pheromone_gradient(self, xy: np.ndarray) -> np.ndarray:
        """Central-difference gradient of the pheromone field at each position."""
        gh, gw = self.pheromone.shape
        cy, cx = self._cells(xy)
        xp, xm = np.clip(cx + 1, 0, gw - 1), np.clip(cx - 1, 0, gw - 1)
        yp, ym = np.clip(cy + 1, 0, gh - 1), np.clip(cy - 1, 0, gh - 1)
        gx = (self.pheromone[cy, xp] - self.pheromone[cy, xm]) / (2 * PHERO_CELL_CM)
        gy = (self.pheromone[yp, cx] - self.pheromone[ym, cx]) / (2 * PHERO_CELL_CM)
        return np.column_stack([gx, gy])

    def _social(self, xy: np.ndarray, tree: cKDTree) -> tuple[np.ndarray, np.ndarray]:
        """
        Zonal social response: attraction toward the local centre of mass of
        neighbours, and volume exclusion pushing away from anyone too close.

        Attraction is a unit vector, but repulsion **carries its magnitude**: it
        scales with how far inside the exclusion radius the neighbours are, so it
        fades to zero once the animals are adequately spaced. A unit-magnitude
        repulsion never balances the attraction, and a resting aggregation then
        expands without limit.

        Vectorised over pairs rather than looping per individual, because the
        later phases need generation-scale runs.
        """
        r_att = float(P["conspecific_radius"])
        r_rep = float(P["repulsion_radius"])
        n = len(xy)
        attract = np.zeros_like(xy)
        repel = np.zeros_like(xy)
        pairs = tree.query_pairs(r_att, output_type="ndarray")
        if len(pairs) == 0:
            return attract, repel
        i, j = pairs[:, 0], pairs[:, 1]
        d = xy[j] - xy[i]                       # offset from i to j
        dist = np.hypot(d[:, 0], d[:, 1])

        sum_d = np.zeros_like(xy)
        cnt = np.zeros(n)
        np.add.at(sum_d, i, d)
        np.add.at(sum_d, j, -d)
        np.add.at(cnt, i, 1.0)
        np.add.at(cnt, j, 1.0)
        has = cnt > 0
        com = np.zeros_like(xy)
        com[has] = sum_d[has] / cnt[has, None]
        attract = self._unit(com)

        close = dist < r_rep
        if close.any():
            ic, jc = i[close], j[close]
            overlap = (r_rep - dist[close]) / r_rep
            away = -d[close] / np.maximum(dist[close], 1e-9)[:, None]   # i away from j
            np.add.at(repel, ic, away * overlap[:, None])
            np.add.at(repel, jc, -away * overlap[:, None])
        return attract, repel

    @staticmethod
    def _unit(v: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(v, axis=1, keepdims=True)
        return np.divide(v, n, out=np.zeros_like(v), where=n > 1e-9)

    def is_dark(self) -> bool:
        period = 24 * 3600.0
        return (self.t % period) >= float(P["photophase_hours"]) * 3600.0

    # --------------------------------------------------------------------- step
    def step(self, dt: float = 1.0) -> None:
        live = self.alive
        if not live.any():
            # the mark outlives the animals: decay still applies
            self.pheromone *= np.exp(-float(P["pheromone_decay"]) * dt)
            self.t += dt
            return
        a = self.arena
        xy = self.xy
        hc = a.harborage_centres()
        rc = a.resource_centres()

        # 1. satiety decays; feeding at a resource restores it
        self.satiety[live] -= float(P["hunger_rate"]) * dt
        if len(rc):
            at_food = np.zeros(self.n, dtype=bool)
            for s in a.resources:
                at_food |= s.contains(xy)
            feeding = at_food & live & (self.state == FORAGING)
            self.satiety[feeding] += float(P["feed_rate"]) * dt
        np.clip(self.satiety, 0.0, 1.0, out=self.satiety)

        # 2. the activity cycle:
        #    resting in a harborage -> foraging when hungry (mostly at night)
        #    -> returning once satiated -> resting again on arrival.
        #    Conflating "returning" with "resting" strands satiated animals on the
        #    open floor, since a resting animal barely moves.
        rate = float(P["dark_emergence_rate"] if self.is_dark() else P["light_emergence_rate"])
        p_out = 1.0 - np.exp(-rate * dt / 60.0)
        want_out = (self.satiety < 0.6) & (self.state == RESTING) & live
        leaving = want_out & (self.rng.random(self.n) < p_out)
        self.state[leaving] = FORAGING
        self.emergences += leaving      # cumulative activity, per individual
        turning_back = (self.state == FORAGING) & (self.satiety > 0.95) & live
        self.state[turning_back] = RETURNING
        # Imperfect harborage fidelity, decided at the resource rather than at the
        # refuge: from out in the room the nearest refuge is often not the one the
        # animal came from. Deciding this on emergence is a no-op, because an
        # animal sitting in its own harborage is always nearest to it.
        switching = turning_back & (self.rng.random(self.n) < float(P["harborage_switch_prob"]))
        if switching.any():
            d = np.linalg.norm(hc[None, :, :] - xy[switching][:, None, :], axis=2)
            self.home[switching] = np.argmin(d, axis=1)
        home_now = np.zeros(self.n, dtype=bool)
        for k, site in enumerate(a.harborages):
            home_now |= (self.home == k) & site.contains(xy)
        self.state[(self.state == RETURNING) & home_now & live] = RESTING

        # 3. goal: nearest resource while foraging, own harborage otherwise
        goal = hc[self.home].copy()
        if len(rc):
            forage = (self.state == FORAGING) & live
            if forage.any():
                d = np.linalg.norm(rc[None, :, :] - xy[forage][:, None, :], axis=2)
                goal[forage] = rc[np.argmin(d, axis=1)]
        goal_dir = self._unit(goal - xy)

        # 4. the coupling terms
        tree = cKDTree(xy[live])
        attract = np.zeros_like(xy)
        repel = np.zeros_like(xy)
        attract[live], repel[live] = self._social(xy[live], tree)
        grad = self._unit(self._pheromone_gradient(xy))

        # 5. correlated random walk biased by everything above
        noise = self.rng.normal(0, 1.0, self.n)
        prev = np.column_stack([np.cos(self.heading), np.sin(self.heading)])
        persist = float(P["heading_persistence"])
        direction = self._unit(
            persist * prev
            + (1 - persist) * np.column_stack([np.cos(noise * np.pi), np.sin(noise * np.pi)])
            + float(P["goal_weight"]) * goal_dir
            + float(P["pheromone_weight"]) * grad
            + float(P["conspecific_weight"]) * attract
            + float(P["repulsion_weight"]) * repel
        )
        self.heading = np.arctan2(direction[:, 1], direction[:, 0])

        moving = (self.state == FORAGING) | (self.state == RETURNING)
        speed = np.where(moving, float(P["forage_speed"]), 0.05)
        # a resting animal that has reached a harborage is wedged into the crevice
        # and stays put; without this it drifts out under the social terms
        settled = (self.state == RESTING) & a.in_any_harborage(xy)
        speed = np.where(settled, 0.0, speed)
        speed = np.where(live, speed, 0.0)
        self.xy = a.clip(xy + direction * (speed * dt)[:, None])

        # 6. deposit pheromone where individuals rest (faeces accumulate in harborages)
        resting = (self.state == RESTING) & live
        if resting.any():
            cy, cx = self._cells(self.xy[resting])
            np.add.at(self.pheromone, (cy, cx), float(P["pheromone_deposit"]) * dt)
        self.pheromone *= np.exp(-float(P["pheromone_decay"]) * dt)

        self.t += dt

    def positions(self) -> np.ndarray:
        return self.xy.copy()

    # ------------------------------------------------------- open population
    def add(self, n: int, rng: np.random.Generator | None = None) -> np.ndarray:
        """
        Immigrate `n` individuals, returning their indices.

        Real infestations are reinvaded from neighbouring units, and a closed
        population is one of this model's stated limitations. Arrivals start in a
        harborage like residents do, so they join the contact network rather than
        appearing in open floor.
        """
        if n <= 0:
            return np.zeros(0, dtype=int)
        rng = rng or self.rng
        a = self.arena
        hc = a.harborage_centres()
        home = rng.integers(0, len(hc), n)
        jitter = rng.normal(0, float(a.harborages[0].r) / 2, size=(n, 2))
        first = self.n
        self.home = np.concatenate([self.home, home])
        self.xy = np.vstack([self.xy, a.clip(hc[home] + jitter)])
        self.heading = np.concatenate([self.heading, rng.uniform(0, 2 * np.pi, n)])
        self.state = np.concatenate([self.state, np.full(n, RESTING, dtype=np.uint8)])
        self.satiety = np.concatenate([self.satiety, rng.uniform(0.3, 1.0, n)])
        self.alive = np.concatenate([self.alive, np.ones(n, dtype=bool)])
        self.emergences = np.concatenate([self.emergences, np.zeros(n, dtype=int)])
        self.n += n
        return np.arange(first, self.n)

    def remove(self, n: int, rng: np.random.Generator | None = None) -> np.ndarray:
        """
        Take `n` living individuals out, as a trap or a vacuum would, returning
        their indices. They are marked dead rather than deleted so that indices
        stay stable for anything watching.
        """
        rng = rng or self.rng
        live = np.flatnonzero(self.alive)
        if n <= 0 or live.size == 0:
            return np.zeros(0, dtype=int)
        taken = rng.choice(live, size=min(n, live.size), replace=False)
        self.alive[taken] = False
        return taken

    def state_counts(self) -> dict[str, int]:
        return {name: int((self.state == code).sum()) for code, name in STATE_NAMES.items()}


def make_colony(n: int = 200, seed: int = 0, arena: Arena | None = None) -> Colony:
    rng = np.random.default_rng(seed)
    return Colony(arena=arena or default_arena(seed=seed), n=n, rng=rng)


def simulate(colony: Colony, duration_s: float, dt: float = 1.0, recorder=None,
             observers=()) -> Colony:
    """
    Advance `colony` by `duration_s`.

    Each step is offered to `recorder.observe(colony, dt)` and to every entry of
    `observers`, in order. Order matters: a toxicology observer placed after a
    contact recorder sees the same positions the contacts were measured from.
    """
    obs = ([recorder] if recorder is not None else []) + list(observers)
    for _ in range(int(round(duration_s / dt))):
        colony.step(dt)
        for o in obs:
            o.observe(colony, dt)
    return colony
