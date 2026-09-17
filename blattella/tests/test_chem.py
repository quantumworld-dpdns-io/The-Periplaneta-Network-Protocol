import math

import pytest

from blattella.chem import (KDR_L993F, RT_KCAL, TARGETS, VSSC, binding_affinity,
                            ddg_from_resistance_ratio, reference_ddg, resistance_ratio_from_ddg)
from blattella.chem import classical as cl
from blattella.chem import interface as iface
from blattella.chem.classical import ClassicalBackend, interaction_energy
from blattella.chem.compare import compare, report
from blattella.params import Source


# ------------------------------------------------------- the derivation itself --
def test_ddg_and_resistance_ratio_are_inverses():
    for rr in (2.0, 10.0, 202.0, 1000.0):
        assert resistance_ratio_from_ddg(ddg_from_resistance_ratio(rr)) == pytest.approx(rr)


def test_no_resistance_means_no_energy_shift():
    assert ddg_from_resistance_ratio(1.0) == pytest.approx(0.0)
    assert ddg_from_resistance_ratio(10.0) > 0        # weaker binding
    assert ddg_from_resistance_ratio(0.5) < 0         # tighter binding
    with pytest.raises(ValueError):
        ddg_from_resistance_ratio(0.0)


def test_rt_is_about_six_tenths_of_a_kcal_at_room_temperature():
    assert RT_KCAL == pytest.approx(0.5925, abs=0.002)
    # a tenfold resistance ratio is a little over one and a third kcal/mol
    assert ddg_from_resistance_ratio(10.0) == pytest.approx(RT_KCAL * math.log(10))


def test_reference_is_literature_backed_and_carries_its_caveat():
    ref = reference_ddg(KDR_L993F)
    assert ref.backend == "literature" and ref.maturity == "reference"
    assert ref.value == pytest.approx(ddg_from_resistance_ratio(202.0))
    assert ref.uncertainty > 0
    assert "upper bound" in ref.note
    assert iface.P["pyrethroid_resistance_ratio"].source is Source.LITERATURE
    # the ratio is whole-organism, so the note must say it carries metabolic resistance too
    assert "metabolic" in iface.P["pyrethroid_resistance_ratio"].note


def test_binding_affinity_defaults_to_the_measured_reference():
    """The science runs on the measurement unless a backend is asked for by name."""
    d = binding_affinity(VSSC, KDR_L993F, "deltamethrin")
    assert d.backend == "literature"
    assert d.resistance_ratio() == pytest.approx(202.0, rel=1e-6)


def test_a_mutation_must_belong_to_the_target_it_is_applied_to():
    with pytest.raises(ValueError):
        ClassicalBackend().ddg(TARGETS["nAChR"], KDR_L993F, "imidacloprid")


# ------------------------------------------------------------ classical backend --
def test_interaction_energies_are_physically_scaled():
    """Sane binding energies are a few kcal/mol, not hundreds.

    An earlier version set the Lennard-Jones sigma to the sum of the radii rather
    than to the contact distance divided by 2^(1/6), which put every pose on the
    repulsive wall and produced energies near -150 kcal/mol.
    """
    for lig in ("deltamethrin", "imidacloprid", "fipronil"):
        for res in ("L", "F", "A", "W"):
            e = interaction_energy(res, lig)
            assert -20.0 < e < 5.0, f"{res}/{lig} gave {e}"


def test_the_lennard_jones_minimum_sits_at_van_der_waals_contact():
    r_contact = cl._radius(cl.RESIDUES["L"]["volume"]) + cl.LIGANDS["deltamethrin"]["radius"]
    at_contact = interaction_energy("L", "deltamethrin", r_contact)
    assert interaction_energy("L", "deltamethrin", r_contact * 0.75) > at_contact   # repulsive wall
    assert interaction_energy("L", "deltamethrin", r_contact * 2.0) > at_contact    # too far


def test_unknown_residue_or_ligand_is_refused():
    with pytest.raises(KeyError):
        interaction_energy("X", "deltamethrin")
    with pytest.raises(KeyError):
        interaction_energy("L", "DDT")


def test_backend_reports_uncertainty_from_the_pose_it_cannot_know():
    r = ClassicalBackend().ddg(VSSC, KDR_L993F, "deltamethrin")
    assert r.uncertainty >= 0
    assert "no protein structure" in r.note


# ------------------------------------------------ the honest comparison result --
def test_classical_backend_is_marked_demonstration_not_production():
    """It underestimates the kdr effect roughly fifteenfold, so it must not be
    labelled as something the science can lean on."""
    assert ClassicalBackend().maturity == "demonstration"


def test_ddg_sign_convention_is_mutant_minus_wild():
    """
    Regression test for a real bug. ΔΔG is ΔG_bind(mutant) − ΔG_bind(wild), and
    both backends originally subtracted the other way round, which inverted every
    result and produced a false 'the classical backend gets the sign wrong'
    conclusion in the phase-3 log.

    Leucine binds deltamethrin more strongly than phenylalanine in this model, so
    the substitution must come out positive, i.e. resistance.
    """
    e_wild = interaction_energy("L", "deltamethrin")
    e_mutant = interaction_energy("F", "deltamethrin")
    assert e_mutant > e_wild, "the mutant should bind less strongly in this model"
    r = ClassicalBackend().ddg(VSSC, KDR_L993F, "deltamethrin")
    assert r.value == pytest.approx(e_mutant - e_wild)
    assert r.value > 0


def test_classical_backend_agrees_on_direction_but_not_magnitude():
    """
    The honest score, pinned so it cannot drift unnoticed: the right sign, and
    about a fifteenth of the measured effect.
    """
    out = compare([ClassicalBackend()])
    row = out["backends"][0]
    assert row["sign_agrees_with_reference"] is True
    assert 0.0 < row["ddg_kcal_per_mol"] < 1.0
    assert out["reference"]["ddg_kcal_per_mol"] / row["ddg_kcal_per_mol"] > 8


def test_report_lists_maturity_for_every_backend():
    text = report(compare([ClassicalBackend()]))
    assert "demonstration" in text and "reference" in text
