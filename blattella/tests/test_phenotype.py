import pytest

from blattella.phenotype import (BLOCKS, cv_modulation, predictions, rate_modulation, report,
                                 signature)
from blattella.toxicology import ACTIVES


# ------------------------------------------------------------ the modulations --
def test_no_dose_and_no_time_leave_the_neuron_alone():
    for a in ACTIVES:
        assert rate_modulation(a, hours=0.0, burden_ld50=1.0) == 1.0
        assert rate_modulation(a, hours=5.0, burden_ld50=0.0) == 1.0
        assert cv_modulation(a, hours=5.0, burden_ld50=0.0) == 1.0


def test_every_active_excites_before_it_does_anything_else():
    """Prediction 2: an early drop with no excitatory phase would falsify the mapping."""
    for a in ACTIVES:
        assert rate_modulation(a, hours=1.0, burden_ld50=1.0) > 1.0


def test_blocking_actives_go_quiet_and_the_disinhibitor_does_not():
    """Prediction 1, the ordering the module exists to state."""
    late = {a: rate_modulation(a, hours=48.0, burden_ld50=2.0) for a in ACTIVES}
    assert late["deltamethrin"] < 1.0 and late["imidacloprid"] < 1.0
    assert late["fipronil"] >= 1.0
    assert late["fipronil"] > late["deltamethrin"]


def test_block_only_applies_to_targets_declared_to_block():
    assert BLOCKS["Vssc"] and BLOCKS["nAChR"] and not BLOCKS["GABA-Cl"]


def test_a_larger_burden_produces_a_larger_effect():
    small = rate_modulation("fipronil", hours=2.0, burden_ld50=0.1)
    large = rate_modulation("fipronil", hours=2.0, burden_ld50=5.0)
    assert large > small
    assert cv_modulation("deltamethrin", 2.0, 5.0) > cv_modulation("deltamethrin", 2.0, 0.1)


def test_variability_rises_monotonically_with_time():
    """Prediction 3: irregularity is the earlier marker."""
    cvs = [cv_modulation("deltamethrin", h, 1.0) for h in (0.5, 2.0, 8.0, 24.0)]
    assert all(b >= a for a, b in zip(cvs, cvs[1:]))


# ----------------------------------------------------------------- the readout --
def test_an_untreated_unit_reads_out_at_its_fitted_rate():
    s = signature(None, hours=10.0, burden_ld50=0.0, duration_s=300.0)
    assert s.active is None and not s.silent
    assert s.rate_hz == pytest.approx(s.baseline_rate_hz, rel=0.25)
    assert s.rate_fold == pytest.approx(1.0, rel=0.25)


def test_a_poisoned_unit_fires_faster_early_on():
    base = signature(None, 0.0, 0.0, duration_s=300.0)
    hot = signature("fipronil", hours=2.0, burden_ld50=2.0, duration_s=300.0)
    assert hot.rate_hz > base.rate_hz


def test_a_blocked_unit_can_fall_silent():
    s = signature("deltamethrin", hours=200.0, burden_ld50=50.0, duration_s=60.0)
    assert s.silent or s.rate_fold < 0.2


def test_the_readout_uses_the_real_fitted_units():
    """The empirical anchor: parameters fitted to published recordings."""
    from netsim.renewal import load_twin_params
    units, meta = load_twin_params()
    assert len(units) >= 10 and "zenodo" in meta["source"]
    s = signature(None, 0.0, 0.0)
    assert any(abs(s.baseline_rate_hz - u.rate_hz) < 1e-9 for u in units)


def test_higher_variability_means_a_smaller_gamma_shape():
    """CV scales as 1/sqrt(shape), so the readout must lower the shape, not raise it."""
    quiet = signature("fipronil", hours=0.01, burden_ld50=0.01, duration_s=600.0)
    rough = signature("fipronil", hours=24.0, burden_ld50=5.0, duration_s=600.0)
    assert rough.isi_cv > quiet.isi_cv


# ------------------------------------------------------------- falsifiability --
def test_predictions_carry_the_cross_species_caveat():
    p = predictions(0.5)
    assert "extrapolation, not measurement" in p["species_caveat"]
    assert "Periplaneta" in p["species_caveat"] and "Blattella" in p["species_caveat"]


def test_predictions_state_what_would_falsify_them_and_what_they_cannot_see():
    p = predictions(0.5)
    assert len(p["testable_orderings"]) >= 3
    assert len(p["known_blind_spots"]) >= 2
    text = report(p)
    assert "What would falsify this" in text
    assert "What this model cannot tell apart" in text
    assert "predictions, not results" in text


def test_the_stated_ordering_actually_holds_in_the_generated_table():
    """The claims and the numbers must not drift apart."""
    p = predictions(burden_ld50=2.0, hours=(24.0,))
    by = {r["active"]: r for r in p["rows"]}
    assert by["fipronil"]["rate_fold"] > by["deltamethrin"]["rate_fold"]
    assert by["fipronil"]["rate_fold"] > by["imidacloprid"]["rate_fold"]


def test_the_declared_blind_spot_is_real():
    """
    The model says it cannot separate deltamethrin from imidacloprid. If that
    ever stops being true the claim must be removed, so it is pinned.
    """
    p = predictions(burden_ld50=1.0, hours=(2.0, 24.0))
    rows = {(r["active"], r["hours"]): r for r in p["rows"]}
    for h in (2.0, 24.0):
        assert rows[("deltamethrin", h)]["rate_fold"] == rows[("imidacloprid", h)]["rate_fold"]
