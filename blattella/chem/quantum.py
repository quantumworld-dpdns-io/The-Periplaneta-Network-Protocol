"""
Quantum ΔΔG backend: VQE on a model Hamiltonian of the residue-ligand contact.

**Scope, stated before anything else.** This does not compute the binding free
energy of deltamethrin in the sodium channel. Nothing available today does, and
claiming otherwise would be the kind of overreach this project exists to avoid.
What it does is take the one part of the interaction a classical force field
cannot represent -- charge transfer and dispersion between an aromatic residue
and a polarisable ligand -- write it as a small fermionic Hamiltonian, and solve
that Hamiltonian variationally on a quantum simulator.

**Why this particular piece.** Phase 3 established that the classical backend
gets the *sign* of the kdr effect wrong. Its failure is not numerical: a model
built from residue volume and hydropathy predicts that phenylalanine, being
larger and aromatic, binds a lipophilic pyrethroid better. The physics it is
missing is electronic. So the quantum backend is pointed at exactly that gap
rather than at the whole problem.

**The model.** Two sites, one for the residue's frontier orbital and one for the
ligand's, in an extended Hubbard form:

    H = sum_s eps_s n_s  -  t sum_sigma (c†_0sigma c_1sigma + h.c.)
        + sum_s U_s n_s,up n_s,down  +  V n_0 n_1

The site energies come from the residues' and ligand's ionisation character, the
hopping `t` from orbital overlap at the contact distance, `U` from on-site
repulsion and `V` from intersite Coulomb. Aromatic residues have a lower-lying,
more diffuse frontier orbital than aliphatic ones, so phenylalanine and leucine
differ in `eps` and `t`. The interaction energy is the ground state minus the two
isolated fragments, and ΔΔG is the difference of those between wild type and
mutant.

**Validation.** Every VQE energy is checked against exact diagonalisation of the
same Hamiltonian. That separates "the quantum algorithm converged" from "the
model is right", which are different claims and are reported separately.

Maturity is `demonstration`. The comparison against the measured reference in
`compare.py` is the honest deliverable, not the number itself.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..params import Param, Source, named
from .interface import DDGResult, Mutation, Target

# Frontier-orbital descriptors for the residue side chains that matter here.
# `eps` is a site energy in eV, more negative for a more tightly bound orbital;
# `spread` scales the orbital's reach, which sets the hopping at contact.
RESIDUE_ORBITALS: dict[str, dict[str, float]] = {
    "L": {"eps": -9.0, "spread": 1.00, "u": 6.0, "aromatic": 0.0},
    "F": {"eps": -8.3, "spread": 1.35, "u": 4.6, "aromatic": 1.0},
    "W": {"eps": -8.0, "spread": 1.50, "u": 4.2, "aromatic": 1.0},
    "Y": {"eps": -8.4, "spread": 1.35, "u": 4.7, "aromatic": 1.0},
    "I": {"eps": -9.0, "spread": 1.00, "u": 6.0, "aromatic": 0.0},
    "V": {"eps": -9.1, "spread": 0.95, "u": 6.2, "aromatic": 0.0},
    "A": {"eps": -9.4, "spread": 0.80, "u": 6.8, "aromatic": 0.0},
}

LIGAND_ORBITALS: dict[str, dict[str, float]] = {
    "deltamethrin": {"eps": -8.6, "spread": 1.30, "u": 4.8},
    "imidacloprid": {"eps": -9.6, "spread": 0.90, "u": 6.4},
    "fipronil": {"eps": -9.2, "spread": 1.05, "u": 5.6},
}

P = named(
    hopping_scale=Param(
        0.55, "eV", Source.ASSUMPTION, sweep=(0.1, 1.5),
        note="hopping integral between residue and ligand frontier orbitals at "
             "van der Waals contact, before the orbital-spread factor. Sets how "
             "much charge transfer the model allows",
    ),
    intersite_coulomb=Param(
        2.2, "eV", Source.ASSUMPTION, sweep=(0.5, 5.0),
        note="nearest-neighbour Coulomb repulsion V between the two sites",
    ),
    ddg_scale=Param(
        0.15, "dimensionless", Source.ASSUMPTION, sweep=(0.02, 0.5),
        note="fraction of the model's electronic interaction energy that survives "
             "into a binding free energy, standing in for solvation, entropy and "
             "the rest of the pocket. Unpinnable without a structure, and the "
             "single largest reason this backend is a demonstration",
    ),
)

EV_TO_KCAL = 23.060548


def hubbard_hamiltonian(eps0: float, eps1: float, t: float, u0: float, u1: float, v: float,
                        n_electrons: int = 2, penalty: float = 20.0):
    """
    Two-site extended Hubbard Hamiltonian as a qubit operator.

    Jordan-Wigner on four spin orbitals ordered (0up, 0down, 1up, 1down), so
    n_i = (I - Z_i)/2 and the hop between like spins carries the usual string.

    A penalty `penalty * (N - n_electrons)^2` fixes the electron count. Without
    it the ground state simply fills every orbital, since all site energies are
    negative; the sector is then Pauli-blocked and no charge transfer is
    possible, which is the entire physics this model exists to capture.
    """
    from qiskit.quantum_info import SparsePauliOp

    terms: list[tuple[str, float]] = []

    def n(i: int) -> list[tuple[str, float]]:
        z = ["I"] * 4
        z[i] = "Z"
        return [("IIII", 0.5), ("".join(reversed(z)), -0.5)]

    def add(ops: list[tuple[str, float]], scale: float) -> None:
        for label, c in ops:
            terms.append((label, c * scale))

    # site energies
    add(n(0), eps0)
    add(n(1), eps0)
    add(n(2), eps1)
    add(n(3), eps1)

    # on-site repulsion U n_up n_down, expanded from the number operators
    for (a, b), u in (((0, 1), u0), ((2, 3), u1)):
        za, zb = ["I"] * 4, ["I"] * 4
        za[a], zb[b] = "Z", "Z"
        zz = ["I"] * 4
        zz[a] = zz[b] = "Z"
        terms += [("IIII", 0.25 * u), ("".join(reversed(za)), -0.25 * u),
                  ("".join(reversed(zb)), -0.25 * u), ("".join(reversed(zz)), 0.25 * u)]

    # intersite Coulomb V n_0 n_1 over both spins
    for a in (0, 1):
        for b in (2, 3):
            za, zb = ["I"] * 4, ["I"] * 4
            za[a], zb[b] = "Z", "Z"
            zz = ["I"] * 4
            zz[a] = zz[b] = "Z"
            terms += [("IIII", 0.25 * v), ("".join(reversed(za)), -0.25 * v),
                      ("".join(reversed(zb)), -0.25 * v), ("".join(reversed(zz)), 0.25 * v)]

    # hopping, same spin only; JW string is a single Z between the two orbitals
    for a, b, mid in ((0, 2, 1), (1, 3, 2)):
        xx, yy = ["I"] * 4, ["I"] * 4
        xx[a], xx[b], xx[mid] = "X", "X", "Z"
        yy[a], yy[b], yy[mid] = "Y", "Y", "Z"
        terms += [("".join(reversed(xx)), -0.5 * t), ("".join(reversed(yy)), -0.5 * t)]

    h = SparsePauliOp.from_list(terms).simplify()

    if penalty > 0:
        num = SparsePauliOp.from_list(
            [("IIII", 2.0)] +
            [("".join(reversed(["Z" if k == i else "I" for k in range(4)])), -0.5) for i in range(4)]
        ).simplify()
        shifted = (num - n_electrons * SparsePauliOp.from_list([("IIII", 1.0)])).simplify()
        h = (h + penalty * (shifted @ shifted)).simplify()
    return h


def exact_ground_energy(op) -> float:
    """Exact diagonalisation of the qubit Hamiltonian, in eV."""
    return float(np.linalg.eigvalsh(op.to_matrix())[0])


def vqe_ground_energy(op, seed: int = 7, maxiter: int = 4000, restarts: int = 3) -> tuple[float, int]:
    """
    Variational ground-state energy in eV, on the statevector simulator.

    A hardware-efficient EfficientSU2 ansatz with COBYLA. Returns the energy and
    the number of objective evaluations, so a run that failed to converge is
    visible rather than silently reported as a result.
    """
    from qiskit.circuit.library import EfficientSU2
    from qiskit.primitives import StatevectorEstimator
    from scipy.optimize import minimize

    ansatz = EfficientSU2(op.num_qubits, reps=3, entanglement="full")
    estimator = StatevectorEstimator(seed=seed)
    rng = np.random.default_rng(seed)
    calls = 0

    def energy(x: np.ndarray) -> float:
        nonlocal calls
        calls += 1
        result = estimator.run([(ansatz, op, [x])]).result()
        return float(np.real(result[0].data.evs[0]))

    best = np.inf
    for r in range(restarts):
        x0 = rng.uniform(-np.pi, np.pi, ansatz.num_parameters) if r else \
            rng.uniform(-0.1, 0.1, ansatz.num_parameters)
        res = minimize(energy, x0, method="COBYLA",
                       options={"maxiter": maxiter, "rhobeg": 0.5, "tol": 1e-9})
        best = min(best, float(res.fun))
    return best, calls


def _fragment_energies(residue: str, ligand: str, gap: float = 0.0) -> dict[str, float]:
    """Complex and isolated-fragment energies, in eV, by exact diagonalisation."""
    r = RESIDUE_ORBITALS[residue]
    l = LIGAND_ORBITALS[ligand]
    t = float(P["hopping_scale"]) * r["spread"] * l["spread"] * math.exp(-gap)
    v = float(P["intersite_coulomb"])
    complex_h = hubbard_hamiltonian(r["eps"], l["eps"], t, r["u"], l["u"], v)
    apart_h = hubbard_hamiltonian(r["eps"], l["eps"], 0.0, r["u"], l["u"], v)
    return {"complex": exact_ground_energy(complex_h),
            "apart": exact_ground_energy(apart_h),
            "t": t}


def interaction_energy_ev(residue: str, ligand: str, use_vqe: bool = False,
                          seed: int = 7) -> dict[str, float]:
    """
    Electronic interaction energy of the contact, in eV.

    With `use_vqe`, the complex is solved variationally and the exact value is
    returned alongside so the two claims -- the algorithm converged, and the model
    is right -- stay separable.
    """
    r = RESIDUE_ORBITALS[residue]
    l = LIGAND_ORBITALS[ligand]
    t = float(P["hopping_scale"]) * r["spread"] * l["spread"]
    v = float(P["intersite_coulomb"])
    complex_h = hubbard_hamiltonian(r["eps"], l["eps"], t, r["u"], l["u"], v)
    # The reference keeps V and drops only the hopping, so the classical
    # electrostatics cancel and what is left is the charge-transfer and
    # correlation stabilisation -- precisely the term a force field omits, and
    # the reason for pointing a quantum method at this piece rather than at the
    # whole binding problem.
    apart_h = hubbard_hamiltonian(r["eps"], l["eps"], 0.0, r["u"], l["u"], v)
    e_apart = exact_ground_energy(apart_h)
    e_exact = exact_ground_energy(complex_h)
    out = {"exact_complex": e_exact, "apart": e_apart, "exact_interaction": e_exact - e_apart}
    if use_vqe:
        e_vqe, calls = vqe_ground_energy(complex_h, seed=seed)
        out.update({"vqe_complex": e_vqe, "vqe_interaction": e_vqe - e_apart,
                    "vqe_error": e_vqe - e_exact, "objective_calls": float(calls)})
    return out


@dataclass(frozen=True)
class QuantumBackend:
    """
    VQE on a two-site model of the residue-ligand frontier orbitals.

    `demonstration` maturity, and it will stay there until it is solving a
    Hamiltonian derived from a real structure rather than from tabulated orbital
    descriptors.
    """

    name: str = "quantum-vqe"
    maturity: str = "demonstration"
    use_vqe: bool = True
    seed: int = 7

    def ddg(self, target: Target, mutation: Mutation, ligand: str) -> DDGResult:
        if mutation.target != target.name:
            raise ValueError(f"{mutation.name} is not a mutation of {target.name}")
        if mutation.wild not in RESIDUE_ORBITALS or mutation.mutant not in RESIDUE_ORBITALS:
            raise KeyError(f"no orbital descriptors for {mutation.wild}->{mutation.mutant}")
        key = "vqe_interaction" if self.use_vqe else "exact_interaction"
        wild = interaction_energy_ev(mutation.wild, ligand, self.use_vqe, self.seed)
        mutant = interaction_energy_ev(mutation.mutant, ligand, self.use_vqe, self.seed)
        # ddG = dG_bind(mutant) - dG_bind(wild); interaction energies are negative
        # for favourable binding
        d_ev = mutant[key] - wild[key]
        value = d_ev * EV_TO_KCAL * float(P["ddg_scale"])

        # uncertainty from the scale factor, which is the least defensible input
        lo, hi = P["ddg_scale"].sweep
        unc = abs(d_ev) * EV_TO_KCAL * (hi - lo) / 2.0

        note = ("VQE on a two-site extended Hubbard model of the residue and ligand "
                "frontier orbitals; not an ab initio calculation of the binding site, "
                "and scaled to a free energy by an unpinnable factor")
        if self.use_vqe:
            err = max(abs(wild.get("vqe_error", 0.0)), abs(mutant.get("vqe_error", 0.0)))
            note += f". VQE reproduced exact diagonalisation to {err:.2e} eV"
        return DDGResult(value=value, uncertainty=unc, backend=self.name,
                         maturity=self.maturity, note=note)
