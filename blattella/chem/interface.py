"""
The chemistry boundary: what a resistance mutation does to drug binding.

This is the hinge of the whole project. A resistance mutation changes the
binding site of the insecticide's molecular target; that shifts the binding free
energy by ΔΔG; that shifts the dose-response curve; that changes who survives;
that is the selection pressure driving allele frequencies. The chain the project
set out to model -- DNA to drug to harm to evolution -- passes through this one
number.

Everything above this layer sees only `ddg(target, mutation, ligand)`. Backends
plug in underneath:

* `classical` -- a reduced molecular-mechanics estimate. Production path today.
* `quantum`   -- a Qiskit VQE calculation (phase 5). Demonstration path.

Both are judged against the same reference, which is derived rather than
modelled: a published resistance ratio *is* a measurement of ΔΔG, because a
strain needing `RR` times the dose has, under a competitive-occupancy model,
a binding free energy shifted by

    ΔΔG = R T ln(RR)

`ddg_from_resistance_ratio` implements exactly that, and it is the value the
population-genetics layer uses unless a backend is explicitly selected. A
backend that cannot reproduce it is a backend that is not ready to drive the
science, and saying so is the point of having the comparison.

Sign convention: **positive ΔΔG means weaker binding**, i.e. resistance.
Energies are kcal/mol.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from ..params import Param, Source, named

GAS_CONSTANT_KCAL = 0.0019872041      # kcal / (mol K)
TEMPERATURE_K = 298.15
RT_KCAL = GAS_CONSTANT_KCAL * TEMPERATURE_K     # ~0.5925 kcal/mol


@dataclass(frozen=True)
class Target:
    """A molecular target of an insecticide."""

    name: str
    gene: str
    description: str


@dataclass(frozen=True)
class Mutation:
    """A single amino-acid substitution in a target."""

    name: str
    target: str
    wild: str
    mutant: str
    position: int
    note: str = ""


# --------------------------------------------------------------------- targets --
VSSC = Target("Vssc", "para", "voltage-gated sodium channel, target of pyrethroids")
NACHR = Target("nAChR", "nAChR", "nicotinic acetylcholine receptor, target of neonicotinoids")
GABA_CL = Target("GABA-Cl", "Rdl", "GABA-gated chloride channel, target of phenylpyrazoles")

TARGETS = {t.name: t for t in (VSSC, NACHR, GABA_CL)}

# The canonical knockdown-resistance substitution in Blattella germanica. Note the
# numbering: L993F is the Blattella position, in the S6 helix of domain II. The
# L1014F seen throughout the literature is the equivalent site under the Musca
# domestica numbering, and the two are easy to confuse.
KDR_L993F = Mutation(
    name="L993F", target="Vssc", wild="L", mutant="F", position=993,
    note="knockdown resistance; domain II S6 helix of the para-type sodium channel",
)

MUTATIONS = {m.name: m for m in (KDR_L993F,)}


# ------------------------------------------------------- the reference values --
P = named(
    pyrethroid_resistance_ratio=Param(
        202.0, "fold", Source.LITERATURE,
        cite="topical LD50 ratio of a field-collected Blattella germanica strain "
             "to a susceptible laboratory population for cypermethrin, 202 +/- 33",
        note="this ratio is whole-organism, so it carries metabolic resistance "
             "(P450, esterase, GST) as well as the target-site change. The "
             "target-site-only shift is therefore smaller, and the ΔΔG derived "
             "from it is an upper bound",
    ),
    resistance_ratio_sd=Param(
        33.0, "fold", Source.LITERATURE,
        cite="standard deviation reported alongside the cypermethrin resistance ratio",
    ),
)


def ddg_from_resistance_ratio(rr: float) -> float:
    """
    ΔΔG in kcal/mol implied by a resistance ratio.

    Under a competitive-occupancy model the dose required to reach a fixed
    receptor occupancy scales with the dissociation constant, so RR = Kd'/Kd and
    ΔΔG = RT ln(RR). Positive means weaker binding.
    """
    if rr <= 0:
        raise ValueError("resistance ratio must be positive")
    return RT_KCAL * math.log(rr)


def resistance_ratio_from_ddg(ddg: float) -> float:
    """Inverse of `ddg_from_resistance_ratio`; what the selection layer consumes."""
    return math.exp(ddg / RT_KCAL)


def reference_ddg(mutation: Mutation = KDR_L993F) -> "DDGResult":
    """The literature-derived reference every backend is measured against."""
    if mutation is not KDR_L993F:
        raise KeyError(f"no published resistance ratio wired up for {mutation.name}")
    rr = float(P["pyrethroid_resistance_ratio"])
    sd = float(P["resistance_ratio_sd"])
    val = ddg_from_resistance_ratio(rr)
    # propagate the reported spread through the logarithm
    unc = RT_KCAL * sd / rr
    return DDGResult(
        value=val, uncertainty=unc, backend="literature", maturity="reference",
        note=f"RT ln({rr:g}) at {TEMPERATURE_K:g} K; whole-organism ratio, so this "
             f"is an upper bound on the target-site contribution",
    )


# ----------------------------------------------------------------- the contract --
@dataclass(frozen=True)
class DDGResult:
    """
    A ΔΔG with the provenance needed to report it honestly.

    `maturity` is not decoration. The project's rule is that no quantum number is
    presented without a classical comparison and a statement of what it is, and
    this field is how that rule is enforced mechanically.
    """

    value: float                 # kcal/mol, positive = weaker binding = resistance
    uncertainty: float
    backend: str
    maturity: str                # "reference" | "production" | "demonstration"
    note: str = ""

    def resistance_ratio(self) -> float:
        return resistance_ratio_from_ddg(self.value)

    def as_dict(self) -> dict:
        return {
            "ddg_kcal_per_mol": round(self.value, 4),
            "uncertainty": round(self.uncertainty, 4),
            "implied_resistance_ratio": round(self.resistance_ratio(), 2),
            "backend": self.backend,
            "maturity": self.maturity,
            "note": self.note,
        }


class ChemBackend(Protocol):
    """Anything that can estimate a ΔΔG for a mutation."""

    name: str
    maturity: str

    def ddg(self, target: Target, mutation: Mutation, ligand: str) -> DDGResult:
        ...


def binding_affinity(target: Target, mutation: Mutation, ligand: str,
                     backend: ChemBackend | None = None) -> DDGResult:
    """
    ΔΔG for `mutation` in `target` against `ligand`.

    With no backend the literature-derived reference is returned, which is what
    the population-genetics layer uses by default: it is measured, and no backend
    currently beats a measurement.
    """
    if backend is None:
        return reference_ddg(mutation)
    return backend.ddg(target, mutation, ligand)
