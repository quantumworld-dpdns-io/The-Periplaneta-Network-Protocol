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

import json
import math
import threading
import time
from dataclasses import dataclass

import numpy as np

# #region agent log
_HUBBARD_CALLS = 0
_DEBUG_LOG = "/Users/dennis_leedennis_lee/Documents/GitHub/The-Periplaneta-Network-Protocol/.cursor/debug-64cc09.log"

def _agent_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    payload = {
        "sessionId": "64cc09",
        "runId": "post-fix",
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    line = json.dumps(payload)
    try:
        with open(_DEBUG_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://127.0.0.1:7616/ingest/291dd374-303e-433f-9ed0-c941bdf4665c",
            data=line.encode(),
            headers={"Content-Type": "application/json", "X-Debug-Session-Id": "64cc09"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=0.5).read()
    except Exception:
        pass
# #endregion


def _pauli_op(terms: list[tuple[str, float]]):
    """Build a SparsePauliOp without Qiskit's Rust `simplify`.

    `SparsePauliOp.simplify` calls `unordered_unique` in the native extension
    and segfaults on later invocations from Starlette's TestClient worker
    thread (GitHub Actions and local `pytest blattella/tests`). Combining
    duplicate labels in Python is enough for this 4-qubit model.
    """
    from qiskit.quantum_info import SparsePauliOp

    acc: dict[str, complex] = {}
    for label, coeff in terms:
        acc[label] = acc.get(label, 0.0) + complex(coeff)
    cleaned = [(label, coeff) for label, coeff in acc.items() if abs(coeff) > 1e-12]
    if not cleaned:
        cleaned = [("IIII", 0.0)]
    return SparsePauliOp.from_list(cleaned)

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

_PAULI = {
    "I": np.array([[1, 0], [0, 1]], dtype=complex),
    "X": np.array([[0, 1], [1, 0]], dtype=complex),
    "Y": np.array([[0, -1j], [1j, 0]], dtype=complex),
    "Z": np.array([[1, 0], [0, -1]], dtype=complex),
}


def _pauli_string_matrix(label: str) -> np.ndarray:
    """Kronecker product for a Qiskit Pauli label (leftmost = high qubit)."""
    mat = np.array([[1.0]], dtype=complex)
    for ch in label:
        mat = np.kron(mat, _PAULI[ch])
    return mat


def dense_matrix(op) -> np.ndarray:
    """Dense Hamiltonian without Qiskit's threaded Rust `to_matrix`.

    `to_matrix` (and `simplify`) call into `qiskit._accelerate` and segfault
    on later Starlette TestClient worker-thread invocations. 4 qubits is 16x16,
    so expanding Pauli terms in NumPy is cheap and stays in Python.
    """
    acc = None
    for label, coeff in op.to_list():
        term = complex(coeff) * _pauli_string_matrix(str(label))
        acc = term if acc is None else acc + term
    if acc is None:
        dim = 1 << op.num_qubits
        return np.zeros((dim, dim), dtype=complex)
    return acc


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
    # #region agent log
    global _HUBBARD_CALLS
    _HUBBARD_CALLS += 1
    qiskit_ver = "unknown"
    try:
        import qiskit
        qiskit_ver = getattr(qiskit, "__version__", "unknown")
    except Exception as exc:
        qiskit_ver = f"import-failed:{type(exc).__name__}"
    _agent_log("B1-B3", "blattella/chem/quantum.py:hubbard_entry", "hubbard_hamiltonian entry", {
        "call": _HUBBARD_CALLS,
        "thread": threading.current_thread().name,
        "ident": threading.get_ident(),
        "qiskit": qiskit_ver,
        "penalty": penalty,
        "n_electrons": n_electrons,
    })
    # #endregion

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

    # #region agent log
    labels = [lbl for lbl, _ in terms]
    _agent_log("B1", "blattella/chem/quantum.py:before_pauli_op", "terms ready for SparsePauliOp", {
        "call": _HUBBARD_CALLS,
        "n_terms": len(terms),
        "n_unique_labels": len(set(labels)),
        "n_iiii": sum(1 for lbl in labels if lbl == "IIII"),
        "thread": threading.current_thread().name,
        "used_simplify": False,
    })
    # #endregion
    h = _pauli_op(terms)
    # #region agent log
    _agent_log("B1-B3", "blattella/chem/quantum.py:after_pauli_op", "python-coalesced operator returned", {
        "call": _HUBBARD_CALLS,
        "n_ops": len(h),
        "thread": threading.current_thread().name,
    })
    # #endregion

    if penalty > 0:
        num = _pauli_op(
            [("IIII", 2.0)] +
            [("".join(reversed(["Z" if k == i else "I" for k in range(4)])), -0.5) for i in range(4)]
        )
        # #region agent log
        _agent_log("B4", "blattella/chem/quantum.py:after_num_op", "number operator without simplify", {
            "call": _HUBBARD_CALLS,
            "n_ops": len(num),
            "thread": threading.current_thread().name,
        })
        # #endregion
        shifted = num - n_electrons * _pauli_op([("IIII", 1.0)])
        h = h + penalty * (shifted @ shifted)
        # #region agent log
        _agent_log("B4", "blattella/chem/quantum.py:after_penalty", "penalty applied without simplify", {
            "call": _HUBBARD_CALLS,
            "n_ops": len(h),
            "thread": threading.current_thread().name,
        })
        # #endregion
    return h


def exact_ground_energy(op) -> float:
    """Exact diagonalisation of the qubit Hamiltonian, in eV."""
    # #region agent log
    _agent_log("B5", "blattella/chem/quantum.py:exact_ground_energy", "numpy diagonalisation", {
        "n_ops": len(op),
        "n_qubits": op.num_qubits,
        "thread": threading.current_thread().name,
    })
    # #endregion
    return float(np.linalg.eigvalsh(dense_matrix(op))[0])


def vqe_ground_energy(op, seed: int = 7, maxiter: int = 4000, restarts: int = 3) -> tuple[float, int]:
    """
    Variational ground-state energy in eV, on the statevector simulator.

    A hardware-efficient EfficientSU2 ansatz with COBYLA. Returns the energy and
    the number of objective evaluations, so a run that failed to converge is
    visible rather than silently reported as a result.

    Expectation values are computed in NumPy. Qiskit's StatevectorEstimator
    submits work to a thread pool, and those workers segfault in circuit.copy
    after other native extensions have been loaded (full `pytest blattella/tests`).
    """
    from qiskit.circuit.library import EfficientSU2
    from qiskit.quantum_info import Statevector
    from scipy.optimize import minimize

    ham = dense_matrix(op)
    ansatz = EfficientSU2(op.num_qubits, reps=3, entanglement="full")
    rng = np.random.default_rng(seed)
    calls = 0

    def energy(x: np.ndarray) -> float:
        nonlocal calls
        calls += 1
        psi = Statevector.from_instruction(ansatz.assign_parameters(x, inplace=False)).data
        return float(np.real(np.vdot(psi, ham @ psi)))

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
