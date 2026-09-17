"""
Toxicology: uptake, internal burden, mortality, and horizontal transfer.

This replaces the old prototype's entire model of harm, which was
`exp(-dt / 6.0)` with a hand-set time constant and no notion of a dose.

Three insecticides with different modes of action, chosen so that rotation and
mixture strategies have something to rotate and mix, and so that target-site and
metabolic resistance both matter:

| active       | class          | target                         | resistance route in the field |
|--------------|----------------|--------------------------------|-------------------------------|
| deltamethrin | pyrethroid     | voltage-gated sodium channel   | kdr (Vssc L993F) + P450       |
| imidacloprid | neonicotinoid  | nicotinic acetylcholine receptor| mainly metabolic (P450)       |
| fipronil     | phenylpyrazole | GABA-gated chloride channel    | target-site + metabolic       |

Dose-response is a probit model on the internal burden. Mortality is applied as
a hazard rather than a one-shot 24 h outcome, because an agent-based model needs
to know *when* an animal dies: a corpse is a concentrated source of insecticide
for whoever scavenges it. The hazard is the exact inversion of the probit rather
than a fitted constant (see the note above `_LD50_WINDOW_S`).

Horizontal transfer is the mechanism that makes gel baits work and the reason
the contact network matters. Three routes:

* **contact** -- residue carried on the cuticle rubs off during contact
* **coprophagy** -- faeces of a dosed animal carry residue and are eaten by
  others in the harborage; modelled as a deposited field, like the pheromone
* **necrophagy** -- a corpse retains its burden and is scavenged

Doses are micrograms per insect throughout.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree
from scipy.stats import norm

from .behaviour import FORAGING, RESTING, Colony
from .params import Param, Source, named

# The mortality hazard is the exact inversion of the 24 h probit, not a scaling of
# it. Holding a burden B constant for the assay window T must reproduce the probit
# outcome, P(dead by T) = Phi(z):
#
#     h(B) = -ln(1 - Phi(z)) / T
#
# At B = LD50 this is ln2 / T, and it diverges as Phi -> 1 so a large overdose kills
# quickly. An earlier version used h = h_max * Phi(z) with h_max fixed at the LD50
# point; that capped mortality at 75 % in 24 h no matter how large the dose, which
# is wrong and mattered here because gel baits deliver enormous multiples of an LD50.
_LD50_WINDOW_S = 24 * 3600.0
_MAX_PHI = 1.0 - 1e-9

P = named(
    probit_slope=Param(
        3.0, "probit/log10(dose)", Source.ASSUMPTION, sweep=(1.5, 6.0),
        note="slope of the log-dose probit line; bioassay slopes for this species "
             "typically fall in this range, the exact value varies by active and strain",
    ),
    clearance_half_life_h=Param(
        24.0, "h", Source.ASSUMPTION, sweep=(4.0, 96.0),
        note="half-life of the internal burden in a susceptible insect through "
             "metabolism and excretion; strongly active-dependent and not pinned",
    ),
    bait_concentration=Param(
        0.0215, "mass fraction", Source.LITERATURE,
        cite="commercial gel bait formulated at 2.15 % imidacloprid (Maxforce Fusion), "
             "used as a representative field concentration",
    ),
    gel_ingestion_rate=Param(
        0.03, "mg/s", Source.ASSUMPTION, sweep=(0.003, 0.3),
        note="gel ingested per second while feeding; a bout of roughly a minute "
             "takes up a few milligrams",
    ),
    cuticular_transfer_rate=Param(
        0.002, "1/s", Source.ASSUMPTION, sweep=(0.0, 0.02),
        note="fraction of a donor's burden passed to a recipient per second of "
             "contact; horizontal transfer of gel baits is well documented, the "
             "per-second efficiency is not",
    ),
    faecal_shed_fraction=Param(
        1.0 / 3600.0, "1/s", Source.ASSUMPTION, sweep=(1 / 86400, 1 / 600),
        note="fraction of burden excreted per second, available for coprophagy",
    ),
    faecal_uptake_rate=Param(
        0.05, "1/s", Source.ASSUMPTION, sweep=(0.0, 0.5),
        note="fraction of the local faecal residue ingested per second while resting",
    ),
    residue_decay=Param(
        1.0 / (7 * 86400.0), "1/s", Source.ASSUMPTION, sweep=(1 / (30 * 86400), 1 / 86400),
        note="environmental degradation of deposited residue",
    ),
    corpse_scavenge_rate=Param(
        0.01, "1/s", Source.ASSUMPTION, sweep=(0.0, 0.1),
        note="fraction of a corpse's remaining burden acquired per second by a "
             "scavenger in contact with it; necrophagy is a documented route",
    ),
    corpse_persistence_h=Param(
        48.0, "h", Source.ASSUMPTION, sweep=(6.0, 168.0),
        note="how long a corpse remains available to scavengers",
    ),
    min_time_to_death_h=Param(
        8.0, "h", Source.ASSUMPTION, sweep=(1.0, 48.0),
        note="floor on time to death however large the dose. Gel baits are "
             "deliberately formulated for delayed action so that a poisoned "
             "individual returns to the harborage before dying, where its corpse "
             "and faeces reach the rest of the colony. Without this floor an "
             "overdosed animal dies where it fed and horizontal transfer largely "
             "fails -- the toxicodynamic cascade takes time regardless of dose",
    ),
)


@dataclass(frozen=True)
class Insecticide:
    """One active ingredient. `ld50_ug` is for a susceptible strain, topical."""

    name: str
    iclass: str
    target: str
    ld50_ug: Param
    resistance_locus: str

    @property
    def ld50(self) -> float:
        return float(self.ld50_ug)


DELTAMETHRIN = Insecticide(
    name="deltamethrin",
    iclass="pyrethroid",
    target="Vssc",              # voltage-gated sodium channel
    resistance_locus="kdr",
    ld50_ug=Param(
        0.004, "ug/insect", Source.LITERATURE,
        cite="topical LD50 for a susceptible Blattella germanica strain, ~0.004 ug/insect",
        name="ld50_deltamethrin",
    ),
)

FIPRONIL = Insecticide(
    name="fipronil",
    iclass="phenylpyrazole",
    target="GABA-Cl",           # GABA-gated chloride channel (Rdl)
    resistance_locus="rdl",
    ld50_ug=Param(
        0.00133, "ug/insect", Source.LITERATURE,
        cite="topical LD50 for a susceptible Blattella germanica strain, 1.33 ng/insect",
        name="ld50_fipronil",
    ),
)

IMIDACLOPRID = Insecticide(
    name="imidacloprid",
    iclass="neonicotinoid",
    target="nAChR",             # nicotinic acetylcholine receptor
    # "p450" named no locus: default_loci() has kdr, rdl, cyp6, est and gst. The
    # field was never read, so the mismatch was invisible until the ligand table
    # tried to join on it. cyp6 is the P450 cluster, and it lists imidacloprid.
    resistance_locus="cyp6",
    ld50_ug=Param(
        0.01, "ug/insect", Source.ASSUMPTION, sweep=(0.001, 0.1),
        note="no susceptible-strain topical LD50 located for this species; field "
             "resistance to imidacloprid baits is documented but the baseline is "
             "not pinned. Placeholder of the same order as the other actives, "
             "swept until a published value is substituted",
        name="ld50_imidacloprid",
    ),
)

ACTIVES = {a.name: a for a in (DELTAMETHRIN, IMIDACLOPRID, FIPRONIL)}


@dataclass
class Toxicology:
    """
    Observer that carries the insecticide state of a colony.

    Attach it to `blattella.behaviour.simulate` alongside a ContactRecorder; it
    is stepped with the same `dt` and updates burden, residue and mortality.
    """

    colony: Colony
    actives: tuple[Insecticide, ...]
    seed: int = 0
    treated_resources: dict[str, set[int]] = field(default_factory=dict)
    rng: np.random.Generator = field(init=False)
    burden: np.ndarray = field(init=False)     # (n_actives, n) ug per insect
    residue: np.ndarray = field(init=False)    # (n_actives, gh, gw) ug per grid cell
    corpse_age: np.ndarray = field(init=False)
    deaths: np.ndarray = field(init=False)     # time of death, nan while alive
    burden_at_death: np.ndarray = field(init=False)  # (n_actives, n) ug, frozen when it died
    acquired_from: np.ndarray = field(init=False)  # 0 bait, 1 contact, 2 faeces, 3 corpse

    def __post_init__(self) -> None:
        # a private stream: drawing mortality from the colony's generator would
        # make the animals' movement depend on whether toxicology is attached,
        # so the same seed would not reproduce the same behaviour
        self.rng = np.random.default_rng(self.seed)
        n = self.colony.n
        k = len(self.actives)
        self.burden = np.zeros((k, n))
        self.residue = np.zeros((k, *self.colony.pheromone.shape))
        self.corpse_age = np.full(n, np.nan)
        self.deaths = np.full(n, np.nan)
        # A corpse loses its burden to scavengers, so the dose an animal is
        # carrying at the end of a run is not the dose that killed it. Recording
        # only the latter would make any survival table cut from this misleading.
        self.burden_at_death = np.full((k, n), np.nan)
        self.acquired_from = np.zeros((4, n))

    # ------------------------------------------------------------------ helpers
    def resize(self) -> None:
        """Grow the per-individual arrays after the colony has taken immigrants."""
        n = self.colony.n
        grown = n - self.burden.shape[1]
        if grown <= 0:
            return
        k = len(self.actives)
        self.burden = np.hstack([self.burden, np.zeros((k, grown))])
        self.corpse_age = np.concatenate([self.corpse_age, np.full(grown, np.nan)])
        self.deaths = np.concatenate([self.deaths, np.full(grown, np.nan)])
        self.burden_at_death = np.hstack([self.burden_at_death, np.full((k, grown), np.nan)])
        self.acquired_from = np.hstack([self.acquired_from, np.zeros((4, grown))])

    def index(self, name: str) -> int:
        for i, a in enumerate(self.actives):
            if a.name == name:
                return i
        raise KeyError(name)

    def treat_resource(self, active: str, resource_indices: list[int] | None = None) -> None:
        """Place a bait of `active` at the given resource sites (default: all)."""
        if resource_indices is None:
            resource_indices = list(range(len(self.colony.arena.resources)))
        self.treated_resources.setdefault(active, set()).update(resource_indices)

    def clear_treatments(self) -> None:
        self.treated_resources.clear()

    def lethal_fraction(self) -> np.ndarray:
        """Burden as a multiple of each active's LD50, summed over actives.

        Actives with different targets are taken as additive in LD50 units. That
        is the standard no-interaction assumption for a mixture; synergism and
        antagonism are out of scope here and would need their own evidence.
        """
        out = np.zeros(self.colony.n)
        for i, a in enumerate(self.actives):
            out += self.burden[i] / a.ld50
        return out

    def mortality_hazard(self) -> np.ndarray:
        """Hazard whose 24 h survival exactly reproduces the probit dose-response."""
        frac = self.lethal_fraction()
        with np.errstate(divide="ignore"):
            z = float(P["probit_slope"]) * np.log10(np.maximum(frac, 1e-12))
        phi = np.minimum(norm.cdf(z), _MAX_PHI)
        h = -np.log1p(-phi) / _LD50_WINDOW_S
        # however large the dose, death takes time: cap the hazard
        return np.minimum(h, 1.0 / (float(P["min_time_to_death_h"]) * 3600.0))

    # --------------------------------------------------------------------- step
    def observe(self, colony: Colony, dt: float) -> None:
        assert colony is self.colony
        if colony.n != self.burden.shape[1]:
            self.resize()
        a = colony.arena
        live = colony.alive
        k = len(self.actives)

        # 1. ingestion at treated resources while foraging
        for name, sites in self.treated_resources.items():
            ai = self.index(name)
            at_bait = np.zeros(colony.n, dtype=bool)
            for si in sites:
                at_bait |= a.resources[si].contains(colony.xy)
            feeding = at_bait & live & (colony.state == FORAGING)
            if feeding.any():
                # ingested gel mass x active concentration, in micrograms
                gain = (float(P["gel_ingestion_rate"]) * 1000.0
                        * float(P["bait_concentration"]) * dt)
                self.burden[ai, feeding] += gain
                self.acquired_from[0, feeding] += gain

        # 2. horizontal transfer by contact between live individuals.
        #    Applied simultaneously from the current burdens, so the result does
        #    not depend on the order pairs happen to come back in.
        rate = float(P["cuticular_transfer_rate"]) * dt
        if rate > 0 and live.sum() > 1:
            idx = np.flatnonzero(live)
            tree = cKDTree(colony.xy[idx])
            pairs = tree.query_pairs(2.0, output_type="ndarray")
            if len(pairs):
                I, J = idx[pairs[:, 0]], idx[pairs[:, 1]]
                give = self.burden[:, I] * rate        # (k, m) i -> j
                take = self.burden[:, J] * rate        # (k, m) j -> i
                for ai in range(k):
                    np.add.at(self.burden[ai], I, take[ai] - give[ai])
                    np.add.at(self.burden[ai], J, give[ai] - take[ai])
                # credit only the *net* gain. Counting both directions treats the
                # churn between two equally dosed animals as acquisition and makes
                # the transfer share look far larger than the real flow.
                net = give.sum(axis=0) - take.sum(axis=0)
                np.add.at(self.acquired_from[1], J, np.maximum(net, 0.0))
                np.add.at(self.acquired_from[1], I, np.maximum(-net, 0.0))

        # 3. faeces: dosed animals shed residue where they rest; others eat it
        resting = (colony.state == RESTING) & live
        if resting.any():
            cy, cx = colony._cells(colony.xy[resting])
            shed = self.burden[:, resting] * float(P["faecal_shed_fraction"]) * dt
            self.burden[:, resting] -= shed
            for ai in range(k):
                np.add.at(self.residue[ai], (cy, cx), shed[ai])
            grab = float(P["faecal_uptake_rate"]) * dt
            for ai in range(k):
                available = self.residue[ai][cy, cx] * grab
                self.burden[ai, resting] += available
                self.acquired_from[2, resting] += available
                np.add.at(self.residue[ai], (cy, cx), -available)
        self.residue *= math.exp(-float(P["residue_decay"]) * dt)

        # 4. necrophagy: corpses are concentrated sources until they are gone
        corpses = np.flatnonzero(~live & np.isfinite(self.corpse_age))
        fresh = corpses[self.corpse_age[corpses] < float(P["corpse_persistence_h"]) * 3600.0]
        if fresh.size and live.any():
            live_idx = np.flatnonzero(live)
            tree = cKDTree(colony.xy[live_idx])
            grab = float(P["corpse_scavenge_rate"]) * dt
            for c in fresh:
                for p in tree.query_ball_point(colony.xy[c], 2.0):
                    j = int(live_idx[p])
                    moved = self.burden[:, c] * grab
                    self.burden[:, j] += moved
                    self.burden[:, c] -= moved
                    self.acquired_from[3, j] += moved.sum()
        self.corpse_age[~live & np.isfinite(self.corpse_age)] += dt

        # 5. metabolic clearance
        lam = math.log(2.0) / (float(P["clearance_half_life_h"]) * 3600.0)
        self.burden[:, live] *= math.exp(-lam * dt)

        # 6. mortality
        h = self.mortality_hazard()
        p_die = 1.0 - np.exp(-h * dt)
        dying = live & (self.rng.random(colony.n) < p_die)
        if dying.any():
            colony.alive[dying] = False
            self.deaths[dying] = colony.t
            self.burden_at_death[:, dying] = self.burden[:, dying]
            self.corpse_age[dying] = 0.0

    # ------------------------------------------------------------------ reports
    def summary(self) -> dict:
        n = self.colony.n
        dead = ~self.colony.alive
        routes = self.acquired_from.sum(axis=1)
        total = routes.sum()
        return {
            "n": n,
            "dead": int(dead.sum()),
            "mortality": round(float(dead.mean()), 4),
            "mean_burden_ug": {a.name: round(float(self.burden[i].mean()), 8)
                               for i, a in enumerate(self.actives)},
            "mean_lethal_fraction": round(float(self.lethal_fraction().mean()), 4),
            "ever_exposed": int((self.acquired_from.sum(axis=0) > 0).sum()),
            "fed_at_bait": int((self.acquired_from[0] > 0).sum()),
            "acquisition_routes": {
                "bait": round(float(routes[0] / total), 4) if total else 0.0,
                "contact": round(float(routes[1] / total), 4) if total else 0.0,
                "faeces": round(float(routes[2] / total), 4) if total else 0.0,
                "corpse": round(float(routes[3] / total), 4) if total else 0.0,
            },
            "residue_ug": {a.name: round(float(self.residue[i].sum()), 6)
                           for i, a in enumerate(self.actives)},
        }


def probit_mortality(dose_ug: np.ndarray | float, ld50: float,
                     slope: float | None = None) -> np.ndarray | float:
    """Classic 24 h probit dose-response, for calibration and tests."""
    s = float(P["probit_slope"]) if slope is None else slope
    with np.errstate(divide="ignore"):
        z = s * np.log10(np.maximum(np.asarray(dose_ug, dtype=float), 1e-12) / ld50)
    return norm.cdf(z)
