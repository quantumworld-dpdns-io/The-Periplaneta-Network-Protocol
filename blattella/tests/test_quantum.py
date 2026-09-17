"""
Tests for the quantum ΔΔG backend.

Two claims are kept apart throughout, because conflating them is the standard
way quantum results get oversold:

  1. the variational algorithm found the ground state of the Hamiltonian it was
     given -- checked against exact diagonalisation;
  2. that Hamiltonian describes the real system -- checked against the measured
     reference, and it does not.
"""
import numpy as np
import pytest

from blattella.chem import KDR_L993F, VSSC, reference_ddg
from blattella.chem.classical import ClassicalBackend
from blattella.chem.compare import compare, report
from blattella.chem.quantum import (QuantumBackend, exact_ground_energy,
                                    hubbard_hamiltonian, interaction_energy_ev,
                                    vqe_ground_energy)


# --------------------------------------------------------------- the Hamiltonian --
def test_hamiltonian_is_four_qubits_and_hermitian():
    h = hubbard_hamiltonian(-9.0, -8.6, 0.5, 6.0, 4.8, 2.2)
    assert h.num_qubits == 4
    m = h.to_matrix()
    assert np.allclose(m, m.conj().T)


def test_particle_number_penalty_selects_the_two_electron_sector():
    """
    Without the penalty the ground state drifts out of the neutral sector: the
    site energies are negative, so extra electrons are favourable until the
    on-site repulsion stops them, and it settles at three. A charged complex is
    not the system being modelled, so the electron count is constrained.
    """
    def electrons(h) -> float:
        w, v = np.linalg.eigh(h.to_matrix())
        gs = v[:, 0]
        # occupation is the number of set bits, weighted by amplitude
        probs = np.abs(gs) ** 2
        return sum(p * bin(i).count("1") for i, p in enumerate(probs))

    unconstrained = hubbard_hamiltonian(-9.0, -8.6, 0.5, 6.0, 4.8, 2.2, penalty=0.0)
    constrained = hubbard_hamiltonian(-9.0, -8.6, 0.5, 6.0, 4.8, 2.2, penalty=20.0)
    assert electrons(unconstrained) > 2.5, "the unconstrained model is not neutral"
    assert electrons(constrained) == pytest.approx(2.0, abs=0.02)


def test_hopping_lowers_the_ground_state():
    """Charge transfer is stabilising; that is the term being modelled."""
    no_hop = exact_ground_energy(hubbard_hamiltonian(-9.0, -8.6, 0.0, 6.0, 4.8, 2.2))
    hop = exact_ground_energy(hubbard_hamiltonian(-9.0, -8.6, 0.6, 6.0, 4.8, 2.2))
    assert hop < no_hop


def test_interaction_is_attractive_and_stronger_for_the_aromatic_residue():
    l = interaction_energy_ev("L", "deltamethrin")["exact_interaction"]
    f = interaction_energy_ev("F", "deltamethrin")["exact_interaction"]
    assert l < 0 and f < 0                      # charge transfer stabilises both
    assert f < l                                # the aromatic residue more so


# ------------------------------------------------------ claim 1: the algorithm --
def test_vqe_reproduces_exact_diagonalisation():
    h = hubbard_hamiltonian(-9.0, -8.6, 0.5, 6.0, 4.8, 2.2)
    exact = exact_ground_energy(h)
    approx, calls = vqe_ground_energy(h, seed=7)
    assert approx >= exact - 1e-9, "variational energy cannot sit below the true ground state"
    assert approx - exact < 0.15, f"VQE did not converge: {approx - exact:.3f} eV above exact"
    assert calls > 0


def test_backend_reports_its_vqe_error_rather_than_hiding_it():
    r = QuantumBackend(use_vqe=True, seed=7).ddg(VSSC, KDR_L993F, "deltamethrin")
    assert "VQE reproduced exact diagonalisation" in r.note
    assert "not an ab initio calculation" in r.note


# ----------------------------------------------------------- claim 2: the model --
def test_the_quantum_backend_gets_the_sign_wrong_and_says_so():
    """
    An honest negative result, pinned. The model captures only charge transfer,
    which favours the aromatic mutant, so it predicts that kdr makes the animal
    *more* susceptible. The measurement says the opposite. The missing physics is
    the geometry of the channel, which a two-site electronic model cannot hold.
    """
    ref = reference_ddg(KDR_L993F)
    r = QuantumBackend(use_vqe=False).ddg(VSSC, KDR_L993F, "deltamethrin")
    assert ref.value > 0                       # measured: resistance
    assert r.value < 0                         # modelled: tighter binding
    assert r.maturity == "demonstration"


def test_comparison_puts_both_backends_against_the_same_measurement():
    out = compare([ClassicalBackend(), QuantumBackend(use_vqe=False)])
    names = [b["backend"] for b in out["backends"]]
    assert names == ["classical-mm", "quantum-vqe"]
    by = {b["backend"]: b for b in out["backends"]}
    assert by["classical-mm"]["sign_agrees_with_reference"] is True
    assert by["quantum-vqe"]["sign_agrees_with_reference"] is False
    text = report(out)
    assert "Sign disagreement" in text and "quantum-vqe" in text
    # every row must state what it is worth
    assert text.count("demonstration") >= 2


def test_uncertainty_reflects_the_unpinnable_scale_factor():
    r = QuantumBackend(use_vqe=False).ddg(VSSC, KDR_L993F, "deltamethrin")
    assert r.uncertainty > abs(r.value) * 0.5, "the scale factor dominates and must show up"


def test_a_mutation_must_belong_to_its_target():
    from blattella.chem import TARGETS
    with pytest.raises(ValueError):
        QuantumBackend(use_vqe=False).ddg(TARGETS["nAChR"], KDR_L993F, "imidacloprid")


def test_unknown_residues_are_refused_rather_than_guessed():
    from blattella.chem.interface import Mutation
    m = Mutation(name="L993P", target="Vssc", wild="L", mutant="P", position=993)
    with pytest.raises(KeyError):
        QuantumBackend(use_vqe=False).ddg(VSSC, m, "deltamethrin")
