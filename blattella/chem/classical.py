"""
Classical ΔΔG backend: a reduced molecular-mechanics estimate.

**What this is.** A coarse, explicit calculation. The binding site is modelled as
a small set of residue pseudo-atoms around a ligand pseudo-atom; the interaction
is Lennard-Jones plus a Coulomb term plus a hydrophobic-burial term; a mutation
swaps one residue's parameters and ΔΔG is the difference in interaction energy.
Every input is a tabulated amino-acid property, so the calculation is inspectable
and reproducible.

**What this is not.** It is not free-energy perturbation, not docking against a
real structure, and not a claim about absolute affinity. There is no protein
structure in this repository. A production backend would dock the ligand into a
resolved or modelled channel structure and run FEP or MM-PBSA; this stands in
for it so that the layers above can be built and tested, and so that the quantum
backend in phase 5 has something to be compared against.

Its accuracy is therefore reported against the literature-derived reference in
`interface.reference_ddg`, not asserted. `compare.py` does that comparison.

**The outcome of that comparison, stated up front.** For the case that matters,
kdr L993F against deltamethrin, this backend gets the **sign right and the
magnitude badly wrong**: about +0.2 kcal/mol against a measured +3.1. It agrees
that the mutation confers resistance, and underestimates it roughly fifteenfold,
which in resistance-ratio terms is 1.4-fold against 202-fold.

That is not a usable selection coefficient, so the backend stays
`demonstration` and the population-genetics layer uses the literature-derived
reference. The architecture is working as intended: the interface lets the
science proceed on a measured number while a backend that is not ready says so.

*(An earlier revision reported this backend as getting the sign wrong. That was
a sign-convention bug in the backend, not a property of the model: ΔΔG is
ΔG_bind(mutant) − ΔG_bind(wild), and the subtraction was the other way round.
The bug was found while building the quantum backend and is recorded in the
phase log.)*
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..params import Param, Source, named
from .interface import DDGResult, Mutation, Target

# Tabulated amino-acid properties. Volumes are residue volumes in cubic angstroms;
# hydropathy is the Kyte-Doolittle scale; charge is the formal charge at pH 7.
RESIDUES: dict[str, dict[str, float]] = {
    "A": {"volume": 88.6, "hydropathy": 1.8, "charge": 0.0, "aromatic": 0.0},
    "R": {"volume": 173.4, "hydropathy": -4.5, "charge": 1.0, "aromatic": 0.0},
    "N": {"volume": 114.1, "hydropathy": -3.5, "charge": 0.0, "aromatic": 0.0},
    "D": {"volume": 111.1, "hydropathy": -3.5, "charge": -1.0, "aromatic": 0.0},
    "C": {"volume": 108.5, "hydropathy": 2.5, "charge": 0.0, "aromatic": 0.0},
    "Q": {"volume": 143.8, "hydropathy": -3.5, "charge": 0.0, "aromatic": 0.0},
    "E": {"volume": 138.4, "hydropathy": -3.5, "charge": -1.0, "aromatic": 0.0},
    "G": {"volume": 60.1, "hydropathy": -0.4, "charge": 0.0, "aromatic": 0.0},
    "H": {"volume": 153.2, "hydropathy": -3.2, "charge": 0.1, "aromatic": 1.0},
    "I": {"volume": 166.7, "hydropathy": 4.5, "charge": 0.0, "aromatic": 0.0},
    "L": {"volume": 166.7, "hydropathy": 3.8, "charge": 0.0, "aromatic": 0.0},
    "K": {"volume": 168.6, "hydropathy": -3.9, "charge": 1.0, "aromatic": 0.0},
    "M": {"volume": 162.9, "hydropathy": 1.9, "charge": 0.0, "aromatic": 0.0},
    "F": {"volume": 189.9, "hydropathy": 2.8, "charge": 0.0, "aromatic": 1.0},
    "P": {"volume": 112.7, "hydropathy": -1.6, "charge": 0.0, "aromatic": 0.0},
    "S": {"volume": 89.0, "hydropathy": -0.8, "charge": 0.0, "aromatic": 0.0},
    "T": {"volume": 116.1, "hydropathy": -0.7, "charge": 0.0, "aromatic": 0.0},
    "W": {"volume": 227.8, "hydropathy": -0.9, "charge": 0.0, "aromatic": 1.0},
    "Y": {"volume": 193.6, "hydropathy": -1.3, "charge": 0.0, "aromatic": 1.0},
    "V": {"volume": 140.0, "hydropathy": 4.2, "charge": 0.0, "aromatic": 0.0},
}

# Ligand descriptors. Pyrethroids are large, lipophilic and uncharged; the
# neonicotinoids carry a polarised nitro/cyano pharmacophore; fipronil is smaller
# and more polar than a pyrethroid.
LIGANDS: dict[str, dict[str, float]] = {
    "deltamethrin": {"radius": 4.6, "charge": 0.0, "logp": 6.2, "polarisable": 1.0},
    "imidacloprid": {"radius": 3.4, "charge": -0.3, "logp": 0.6, "polarisable": 0.4},
    "fipronil": {"radius": 3.8, "charge": -0.2, "logp": 4.0, "polarisable": 0.6},
}

P = named(
    lj_well_depth=Param(
        0.15, "kcal/mol", Source.ASSUMPTION, sweep=(0.05, 0.5),
        note="Lennard-Jones well depth for a residue-ligand contact in the reduced "
             "model; of the order of a united-atom carbon parameter",
    ),
    contact_gap=Param(
        0.3, "angstrom", Source.ASSUMPTION, sweep=(-0.3, 2.0),
        note="separation of the mutated residue and the ligand beyond van der "
             "Waals contact in the bound pose. Without a structure this cannot be "
             "measured, and the result is sensitive to it",
    ),
    dielectric=Param(
        20.0, "dimensionless", Source.ASSUMPTION, sweep=(4.0, 80.0),
        note="effective dielectric inside the binding pocket; between the protein "
             "interior and bulk water",
    ),
    hydrophobic_coefficient=Param(
        0.025, "kcal/(mol A^2)", Source.ASSUMPTION, sweep=(0.005, 0.05),
        note="free energy per unit buried non-polar surface; the usual range for "
             "surface-area models of the hydrophobic effect",
    ),
    aromatic_stacking=Param(
        -0.8, "kcal/mol", Source.ASSUMPTION, sweep=(-2.0, 0.0),
        note="stabilisation when an aromatic residue faces a polarisable ligand",
    ),
)

COULOMB_KCAL = 332.06371          # kcal A / (mol e^2)


def _radius(volume_a3: float) -> float:
    """Effective spherical radius of a residue from its volume."""
    return (3.0 * volume_a3 / (4.0 * math.pi)) ** (1.0 / 3.0)


def interaction_energy(residue: str, ligand: str, distance: float | None = None) -> float:
    """
    Interaction energy in kcal/mol between one residue and the ligand.

    Three terms, all explicit:
      * Lennard-Jones 12-6, with sigma from the residue and ligand radii
      * a screened Coulomb term between the residue formal charge and the ligand
        partial charge
      * a hydrophobic-burial term proportional to contact area, plus an aromatic
        stacking bonus when both partners can support it
    """
    if residue not in RESIDUES:
        raise KeyError(f"unknown residue {residue!r}")
    if ligand not in LIGANDS:
        raise KeyError(f"unknown ligand {ligand!r}")
    res = RESIDUES[residue]
    lig = LIGANDS[ligand]
    r_contact = _radius(res["volume"]) + lig["radius"]
    r = r_contact + float(P["contact_gap"]) if distance is None else distance

    # sigma is where the 12-6 potential crosses zero, not the contact distance:
    # the minimum sits at 2^(1/6) sigma, so sigma = r_contact / 2^(1/6). Setting
    # sigma to the sum of radii puts the pose deep on the repulsive wall and
    # produced energies of order -150 kcal/mol.
    sigma = r_contact / 2.0 ** (1.0 / 6.0)
    eps = float(P["lj_well_depth"])
    sr6 = (sigma / r) ** 6
    lj = 4.0 * eps * (sr6 * sr6 - sr6)

    coulomb = (COULOMB_KCAL * res["charge"] * lig["charge"]) / (float(P["dielectric"]) * r)

    # buried non-polar area grows with both partners' size; hydropathy scales how
    # much of it is actually non-polar, and logp how much the ligand wants burial
    area = math.pi * (_radius(res["volume"]) + lig["radius"]) ** 2
    nonpolar = max(res["hydropathy"], 0.0) / 4.5 * min(lig["logp"] / 6.2, 1.0)
    hydrophobic = -float(P["hydrophobic_coefficient"]) * area * nonpolar

    stacking = float(P["aromatic_stacking"]) * res["aromatic"] * lig["polarisable"]

    return lj + coulomb + hydrophobic + stacking


@dataclass(frozen=True)
class ClassicalBackend:
    """
    Reduced molecular-mechanics ΔΔG.

    Marked `demonstration` because it gets the sign of the kdr effect wrong for
    want of a structure; see the module docstring. A docking or FEP backend
    against a modelled channel structure would take this slot and be marked
    `production`.
    """

    name: str = "classical-mm"
    maturity: str = "demonstration"

    def ddg(self, target: Target, mutation: Mutation, ligand: str) -> DDGResult:
        if mutation.target != target.name:
            raise ValueError(f"{mutation.name} is not a mutation of {target.name}")
        wild = interaction_energy(mutation.wild, ligand)
        mutant = interaction_energy(mutation.mutant, ligand)
        # ddG = dG_bind(mutant) - dG_bind(wild). Interaction energies are negative
        # for favourable binding, so a mutant that binds more weakly has the less
        # negative energy and the difference comes out positive, i.e. resistance.
        value = mutant - wild

        # uncertainty from the parameter the result is most sensitive to: without a
        # structure the contact distance is unknown, so sweep it and take the spread
        lo, hi = P["contact_gap"].sweep
        r_w = _radius(RESIDUES[mutation.wild]["volume"]) + LIGANDS[ligand]["radius"]
        r_m = _radius(RESIDUES[mutation.mutant]["volume"]) + LIGANDS[ligand]["radius"]
        spread = [
            (interaction_energy(mutation.mutant, ligand, r_m + g)
             - interaction_energy(mutation.wild, ligand, r_w + g))
            for g in np.linspace(lo, hi, 9)
        ]
        unc = float(np.std(spread))

        return DDGResult(
            value=value, uncertainty=unc, backend=self.name, maturity=self.maturity,
            note=("reduced molecular mechanics on tabulated residue properties; no "
                  "protein structure is used, so this is a stand-in for docking or "
                  "FEP rather than a substitute for it"),
        )
