"""
Population genetics: generations, selection, and allele-frequency trajectories.

This is the layer that makes the project's third verb, *evolve*, mean something.

**Two timescales, coupled in two stages.** Behaviour runs at dt = 1 s; a
generation of *Blattella germanica* is of the order of 100 days. Running one
loop across six orders of magnitude is not viable, so:

1. the agent-based layer is run once for a representative activity period and
   summarised as an `ExposureProfile`: what fraction of the colony is exposed to
   a treated station, and how large a dose they take;
2. this layer then steps generation by generation, drawing exposures from that
   profile rather than re-running the agent model.

The profile is recomputed when the deployment changes. The cost is that the
contact network is held static within a generation, which is stated rather than
hidden.

**Selection.** An exposed individual's survival is the probit dose-response from
`toxicology`, with its LD50 raised by target-site resistance and its effective
dose lowered by metabolic clearance, both read from its genotype. Survivors
reproduce with a fitness weight that carries the cost of resistance, which is
what lets an allele decline when the matching active is withdrawn.

Life history is taken from the literature: about 30 embryos per ootheca, an
ootheca every 20-25 days, 4-8 in a lifetime, and roughly 100 days egg to adult.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.stats import norm

from .genome import Genome
from .params import Param, Source, named
from .rnai import RnaiTreatment
from .toxicology import ACTIVES, Insecticide

P = named(
    eggs_per_ootheca=Param(
        30.0, "eggs", Source.LITERATURE,
        cite="an ootheca of Blattella germanica carries about 30 embryos, range 25-43",
    ),
    oothecae_per_female=Param(
        6.0, "oothecae", Source.LITERATURE,
        cite="a female produces 4-8 oothecae in her lifetime; midpoint used",
    ),
    generation_days=Param(
        100.0, "days", Source.LITERATURE,
        cite="egg to adult in about 100 days for Blattella germanica",
    ),
    juvenile_survival=Param(
        0.25, "fraction", Source.ASSUMPTION, sweep=(0.05, 0.6),
        note="fraction of eggs reaching adulthood in the absence of insecticide, "
             "absorbing density-independent mortality. Chosen with the carrying "
             "capacity so an untreated population is stable rather than exploding",
    ),
    carrying_capacity=Param(
        400.0, "adults", Source.ASSUMPTION, sweep=(100.0, 5000.0),
        note="adults the harborage network supports; density regulation acts here",
    ),
    sex_ratio=Param(
        0.5, "fraction female", Source.ASSUMPTION, sweep=(0.4, 0.6),
        note="assumed even",
    ),
    probit_slope=Param(
        3.0, "probit/log10(dose)", Source.ASSUMPTION, sweep=(1.5, 6.0),
        note="as in blattella.toxicology; repeated here so the generational "
             "survival step does not silently depend on the fine-scale module",
    ),
)


@dataclass(frozen=True)
class ExposureProfile:
    """
    What an agent-based run says about who meets the insecticide.

    `exposed_fraction` is the share of the colony that acquires any dose, and
    `dose_ld50_median` / `dose_ld50_log_sd` describe the dose they receive, in
    multiples of the susceptible LD50, as a log-normal.
    """

    exposed_fraction: float
    dose_ld50_median: float
    dose_ld50_log_sd: float = 1.0
    source: str = "assumed"

    @staticmethod
    def from_toxicology(tox, active: str, colony) -> "ExposureProfile":
        """Summarise a finished `Toxicology` run for one active."""
        ai = tox.index(active)
        ld50 = tox.actives[ai].ld50
        got = tox.acquired_from.sum(axis=0) > 0
        frac = float(got.mean())
        if not got.any():
            return ExposureProfile(0.0, 0.0, 1.0, source="agent-based run (nobody exposed)")
        doses = np.maximum(tox.burden[ai][got], 1e-12) / ld50
        logs = np.log(doses)
        return ExposureProfile(
            exposed_fraction=frac,
            dose_ld50_median=float(np.exp(np.median(logs))),
            dose_ld50_log_sd=float(np.std(logs)) or 1.0,
            source="agent-based run",
        )

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        """Dose in multiples of the susceptible LD50, zero for the unexposed."""
        hit = rng.random(n) < self.exposed_fraction
        d = np.zeros(n)
        if hit.any() and self.dose_ld50_median > 0:
            d[hit] = rng.lognormal(np.log(self.dose_ld50_median), self.dose_ld50_log_sd, hit.sum())
        return d


@dataclass
class Deployment:
    """
    Which actives are applied in a given generation.

    Entries may be a single active name, `None` for an untreated generation, or a
    tuple of names for a mixture. `dose_share` scales every dose, so a mixture of
    k actives at 1/k share applies the same total quantity as a single-product
    arm and the comparison is fair.
    """

    schedule: list[str | None | tuple[str, ...]]
    dose_share: float = 1.0

    def actives_for(self, generation: int) -> tuple[str, ...]:
        if not self.schedule:
            return ()
        entry = self.schedule[generation % len(self.schedule)]
        if entry is None:
            return ()
        return (entry,) if isinstance(entry, str) else tuple(entry)

    def active_for(self, generation: int) -> str | None:
        """First active of the generation; kept for single-product callers."""
        a = self.actives_for(generation)
        return a[0] if a else None

    @staticmethod
    def from_strategy(strategy) -> "Deployment":
        return Deployment(schedule=list(strategy.schedule), dose_share=strategy.dose_share)


@dataclass
class Population:
    """A diploid population evolving under insecticide selection."""

    genome: Genome
    n: int
    rng: np.random.Generator
    founder_frequency: float = 0.05
    # An optional non-heritable treatment. Passed in per population rather than
    # set on a module global, so two arms of the same comparison cannot contaminate
    # each other: experiment.compare_strategies shares one Genome across all arms.
    rnai: "RnaiTreatment | None" = None
    hap: np.ndarray = field(init=False)
    generation: int = 0
    history: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.hap = self.genome.founder_haplotypes(self.n, self._founder_freqs(), self.rng)
        self._record(survival=float("nan"), active=None)

    def _founder_freqs(self) -> dict[str, float]:
        """
        Standing variation before any treatment. The default is rare resistance
        alleles; raising it models inheriting an infestation that is already
        resistant, which is the situation most control programmes are actually in.
        """
        return {l.name: self.founder_frequency for l in self.genome.loci}

    # -------------------------------------------------------------- selection
    def survival_probability(self, actives, profiles, dose_share: float = 1.0) -> np.ndarray:
        """
        Probability of surviving one generation, per individual.

        Actives in a mixture act independently: an animal must survive each one,
        so the probabilities multiply. Independent action is the standard
        no-interaction assumption; synergism between actives would need its own
        evidence and is not claimed here.
        """
        n = self.hap.shape[0]
        if isinstance(actives, str) or actives is None:
            actives = () if actives is None else (actives,)
        surv = np.ones(n)
        for active in actives:
            ins: Insecticide = ACTIVES[active]
            if hasattr(profiles, "get"):
                if active not in profiles:
                    # silently treating this as "no exposure" turns a typo into a
                    # false result, which is the worst possible failure here
                    raise KeyError(
                        f"no exposure profile for {active!r}; have "
                        f"{sorted(k for k in profiles if k)}")
                profile = profiles[active]
            else:
                profile = profiles
            if profile is None:
                continue
            dose = profile.sample(n, self.rng) * dose_share     # susceptible LD50 units
            rr = self.genome.resistance_ratio(self.hap, active, ins.target)
            clear = self.genome.clearance_multiplier(self.hap, active)
            if self.rnai is not None:
                # a phenotype imposed on the individuals actually reached, for this
                # generation only. Nothing about it is inherited, and it touches one
                # locus: the esterase and the GST are left working.
                share = self.genome.clearance_multiplier(self.hap, active,
                                                         only=self.rnai.locus)
                reached = self.rnai.treated(n, self.rng, self.generation)
                clear = self.rnai.apply(clear, share, reached)
            effective = dose / np.maximum(rr * clear, 1e-9)
            with np.errstate(divide="ignore"):
                z = float(P["probit_slope"]) * np.log10(np.maximum(effective, 1e-12))
            surv *= 1.0 - norm.cdf(z)
        return surv

    def step(self, actives, profiles, dose_share: float = 1.0) -> dict:
        """One generation: selection, then reproduction under density regulation."""
        if isinstance(actives, str) or actives is None:
            actives = () if actives is None else (actives,)
        active = "+".join(actives) if actives else None
        surv_p = self.survival_probability(actives, profiles, dose_share)
        survived = self.rng.random(self.hap.shape[0]) < surv_p
        self.hap = self.hap[survived]
        n_surv = self.hap.shape[0]
        if n_surv < 2:
            self.generation += 1
            return self._record(survival=float(survived.mean()), active=active, extinct=True)

        # reproduction, weighted by the fitness cost of carrying resistance
        w = self.genome.fitness_cost(self.hap)
        females = self.rng.random(n_surv) < float(P["sex_ratio"])
        if females.sum() < 1 or (~females).sum() < 1:
            self.generation += 1
            return self._record(survival=float(survived.mean()), active=active, extinct=True)

        fecundity = float(P["eggs_per_ootheca"]) * float(P["oothecae_per_female"])
        n_eggs = int(females.sum() * fecundity * float(P["juvenile_survival"]))
        k = float(P["carrying_capacity"])
        n_off = int(min(n_eggs, k))
        if n_off < 2:
            self.generation += 1
            return self._record(survival=float(survived.mean()), active=active, extinct=True)

        f_idx = np.flatnonzero(females)
        m_idx = np.flatnonzero(~females)
        pf = w[f_idx] / w[f_idx].sum() if w[f_idx].sum() > 0 else None
        pm = w[m_idx] / w[m_idx].sum() if w[m_idx].sum() > 0 else None
        mothers = self.rng.choice(f_idx, n_off, replace=True, p=pf)
        fathers = self.rng.choice(m_idx, n_off, replace=True, p=pm)
        self.hap = self.genome.mate(self.hap, mothers, fathers, self.rng)

        self.generation += 1
        return self._record(survival=float(survived.mean()), active=active)

    def run(self, deployment: Deployment, generations: int,
            profiles: dict[str | None, ExposureProfile]) -> list[dict]:
        for _ in range(generations):
            actives = deployment.actives_for(self.generation)
            self.step(actives, profiles, deployment.dose_share)
            if self.hap.shape[0] < 2:
                break
        return self.history

    # ---------------------------------------------------------------- reports
    def _record(self, survival: float, active: str | None, extinct: bool = False) -> dict:
        row = {
            "generation": self.generation,
            "n": int(self.hap.shape[0]),
            "active": active,
            "survival": round(survival, 4) if survival == survival else None,
            "extinct": extinct,
            **{f"f_{k}": round(v, 4) for k, v in
               self.genome.allele_frequencies(self.hap).items()},
        }
        self.history.append(row)
        return row

    def allele_frequencies(self) -> dict[str, float]:
        return self.genome.allele_frequencies(self.hap)


def make_population(n: int = 400, seed: int = 0, genome: Genome | None = None,
                    founder_frequency: float = 0.05) -> Population:
    return Population(genome=genome or Genome(), n=n, rng=np.random.default_rng(seed),
                      founder_frequency=founder_frequency)
