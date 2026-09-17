"""
dsRNA against a metabolic-resistance gene, as a treatment the model can apply.

RNA interference is a real and current line of work against this species:
published studies report dsRNA targeting a CYP6 P450 cutting transcript levels
by roughly nine tenths and restoring susceptibility to a pyrethroid. This module
asks what that would do to the *evolution* of resistance, which is the question
the rest of the package is built for and the one a knockdown experiment on its
own cannot answer.

Four decisions here are load-bearing, and each is a place where a careless
implementation would say something false.

**It acts on clearance, not on the resistance ratio.** `cyp6` is a metabolic
locus. `Genome.resistance_ratio` sums target-site loci only, so scaling "the
resistance ratio contributed by cyp6" would have scaled a quantity that is
identically one -- a silent no-op. The channel a P450 acts through is
`clearance_multiplier`, and `genome.py` is explicit that the two must not be
conflated.

**It is not heritable.** A dsRNA effect is a phenotype imposed on an individual
for as long as the molecule persists. `Population` carries only genotypes, so
implementing this as a genome-level parameter would have made the knockdown
inherited by the offspring of treated animals, which is wrong. It is applied
per generation, to the individuals actually reached, and nothing about it passes
to the next generation.

**It knocks down one locus, not metabolism.** The model also carries an esterase
and a glutathione S-transferase acting on the same actives. Silencing a P450
leaves those intact, and that compensation is an argument against a large effect
rather than a detail to gloss over.

**The expected result at bait doses is nothing, and that is the point.** Phase 4
recorded that metabolic resistance is irrelevant against a median dose of fifty
LD50s: a twofold clearance boost buys no survival, so removing it costs none.
The prediction that followed -- that metabolic resistance should matter under
residual sprays, which deliver a few LD50s -- was never tested. This treatment is
the test. Reporting a null at bait doses confirms a recorded finding; reporting
an effect under sprays confirms the prediction. Either way the result is the
allele frequency of `cyp6`, not the kill: relaxing selection on a phenotype is
how an intervention changes which genes spread.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .params import Param, Source, named

P = named(
    rnai_knockdown_efficiency=Param(
        0.92, "fraction", Source.ASSUMPTION, sweep=(0.0, 0.95),
        note="fraction of the targeted locus's clearance contribution removed. "
             "Anchored to published dsRNA experiments in Blattella germanica "
             "reporting roughly 91-94 % reduction in CYP6 TRANSCRIPT levels, but "
             "transcript is not protein and protein is not whole-organism "
             "clearance. The transfer function between them is unmeasured, and "
             "this sweep is what covers that ignorance rather than hiding it",
    ),
    rnai_coverage=Param(
        0.8, "fraction", Source.ASSUMPTION, sweep=(0.0, 1.0),
        note="fraction of the colony receiving an effective dose of dsRNA. "
             "Laboratory work injects or feeds individuals; there is no "
             "field-deployable dsRNA bait for this species, so no delivery route "
             "is modelled and this number stands in for all of it",
    ),
    rnai_persistence=Param(
        0.5, "fraction of a generation", Source.ASSUMPTION, sweep=(0.0, 1.0),
        note="share of a generation over which knockdown holds. RNAi is transient "
             "and a generation here is about a hundred days, so treating it as "
             "lasting the whole interval would flatter the intervention",
    ),
    rnai_locus_share=Param(
        0.5, "fraction", Source.ASSUMPTION, sweep=(0.0, 1.0),
        note="share of the modelled cyp6 cluster attributable to the single gene a "
             "dsRNA targets. The locus stands for a P450 cluster; CYP6K1 is one "
             "gene in it, and silencing one gene does not silence the cluster",
    ),
)


@dataclass(frozen=True)
class RnaiTreatment:
    """
    A dsRNA treatment against one metabolic locus.

    The effective knockdown is the product of four fractions, each of which is a
    declared assumption: how well the molecule silences its target, how much of
    the colony it reaches, how much of the generation it lasts, and how much of
    the modelled locus the targeted gene accounts for. Multiplying them is the
    honest arithmetic -- each is a place the intervention can fail -- and it means
    the headline 92 % figure buys far less than 92 %.
    """

    locus: str = "cyp6"
    efficiency: float | None = None
    coverage: float | None = None
    persistence: float | None = None
    locus_share: float | None = None
    from_generation: int = 0

    def _v(self, given: float | None, key: str) -> float:
        return float(P[key]) if given is None else float(given)

    @property
    def effective_knockdown(self) -> float:
        """How much of the locus's clearance contribution is actually removed."""
        return (self._v(self.efficiency, "rnai_knockdown_efficiency")
                * self._v(self.persistence, "rnai_persistence")
                * self._v(self.locus_share, "rnai_locus_share"))

    def treated(self, n: int, rng: np.random.Generator, generation: int) -> np.ndarray:
        """Which individuals the dsRNA actually reached this generation."""
        if generation < self.from_generation:
            return np.zeros(n, dtype=bool)
        return rng.random(n) < self._v(self.coverage, "rnai_coverage")

    def apply(self, clearance: np.ndarray, contribution: np.ndarray,
              reached: np.ndarray) -> np.ndarray:
        """
        Remove part of one locus's contribution from the clearance multiplier.

        `contribution` is what that locus alone contributes, so dividing it out
        and multiplying a knocked-down version back in leaves every other
        metabolic locus untouched. An animal carrying no resistant allele at the
        locus contributes 1.0 and is unaffected, which is correct: there is
        nothing there to silence.
        """
        k = self.effective_knockdown
        knocked = 1.0 + (contribution - 1.0) * (1.0 - k)
        out = clearance.copy()
        out[reached] = clearance[reached] / contribution[reached] * knocked[reached]
        return out

    def describe(self) -> dict:
        return {
            "locus": self.locus,
            "knockdown_efficiency": self._v(self.efficiency, "rnai_knockdown_efficiency"),
            "coverage": self._v(self.coverage, "rnai_coverage"),
            "persistence": self._v(self.persistence, "rnai_persistence"),
            "locus_share": self._v(self.locus_share, "rnai_locus_share"),
            "effective_knockdown": round(self.effective_knockdown, 4),
            "from_generation": self.from_generation,
            "heritable": False,
            "acts_on": "metabolic clearance, not the target-site resistance ratio",
        }
