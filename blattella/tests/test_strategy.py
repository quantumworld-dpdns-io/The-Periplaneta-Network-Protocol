import numpy as np
import pytest

from blattella.experiment import (RESISTANCE_THRESHOLD, compare_strategies, report,
                                  run_once, write_csv)
from blattella.population import Deployment, ExposureProfile, make_population
from blattella.strategy import ACTIVE_SET, mixture, rotation, single, standard_arms, untreated

HEAVY = ExposureProfile(exposed_fraction=0.6, dose_ld50_median=50.0, dose_ld50_log_sd=1.0)


# ------------------------------------------------------------------ schedules --
def test_single_applies_one_active_every_generation():
    s = single("deltamethrin")
    assert s.actives_for(0) == s.actives_for(7) == ("deltamethrin",)
    assert s.dose_share == 1.0


def test_rotation_cycles_and_respects_its_period():
    r1 = rotation(ACTIVE_SET, period=1)
    assert [r1.actives_for(g)[0] for g in range(4)] == list(ACTIVE_SET) + [ACTIVE_SET[0]]
    r3 = rotation(ACTIVE_SET, period=3)
    assert r3.period == 9
    assert [r3.actives_for(g)[0] for g in range(4)] == [ACTIVE_SET[0]] * 3 + [ACTIVE_SET[1]]


def test_mixture_splits_the_dose_so_arms_are_matched_on_total():
    m = mixture(ACTIVE_SET)
    assert set(m.actives_for(0)) == set(ACTIVE_SET)
    assert m.dose_share == pytest.approx(1 / 3)
    # asking for full dose of each is a different experiment, and is explicit
    full = mixture(ACTIVE_SET, dose_share=1.0)
    assert full.dose_share == 1.0


def test_untreated_applies_nothing():
    assert untreated().actives_for(0) == ()
    assert untreated().dose_share == 0.0


def test_deployment_accepts_strings_tuples_and_none():
    d = Deployment(schedule=["deltamethrin", None, ("imidacloprid", "fipronil")])
    assert d.actives_for(0) == ("deltamethrin",)
    assert d.actives_for(1) == ()
    assert d.actives_for(2) == ("imidacloprid", "fipronil")
    assert d.active_for(1) is None


# ------------------------------------------------------------------- mixtures --
def test_a_mixture_kills_more_than_any_of_its_parts_at_the_same_share():
    """Independent action: an animal must survive every active, so survival multiplies."""
    pop = make_population(n=500, seed=0)
    profiles = {a: HEAVY for a in ACTIVE_SET}
    one = pop.survival_probability(("deltamethrin",), profiles, dose_share=1 / 3).mean()
    all_three = pop.survival_probability(ACTIVE_SET, profiles, dose_share=1 / 3).mean()
    assert all_three < one


def test_no_active_means_certain_survival():
    pop = make_population(n=50, seed=0)
    assert np.all(pop.survival_probability((), {}, 1.0) == 1.0)


# ----------------------------------------------------------------- the answer --
def test_the_strategy_comparison_runs_and_reports_every_arm():
    comp = compare_strategies(seeds=4, generations=20, log=lambda *_: None)
    rows = comp.summary()
    assert {r["strategy"] for r in rows} == {s.name for s in standard_arms()}
    assert all(r["seeds"] == 4 for r in rows)


def test_untreated_never_becomes_resistant_and_treated_arms_do():
    comp = compare_strategies(seeds=6, generations=30, log=lambda *_: None)
    by = {r["strategy"]: r for r in comp.summary()}
    assert by["untreated"]["resistant_runs"] == 0
    assert by["single:deltamethrin"]["resistant_runs"] == 6


def test_rotation_beats_single_product_on_resistance():
    """The headline answer, over seeds rather than one run."""
    comp = compare_strategies(seeds=8, generations=30, log=lambda *_: None)
    by = {r["strategy"]: r for r in comp.summary()}
    rot = by["rotation:delt-imid-fipr"]
    sing = by["single:deltamethrin"]
    assert rot["resistant_runs"] < sing["resistant_runs"]
    assert rot["peak_target_site_mean"] < sing["peak_target_site_mean"]


def test_a_matched_dose_mixture_trades_control_for_resistance():
    """
    The trade-off that makes this comparison worth running. At matched total
    dose the mixture kills the most, and selects every resistance mechanism at
    once because each active is under-dosed against its own resistant genotypes.
    """
    comp = compare_strategies(seeds=8, generations=30, log=lambda *_: None)
    by = {r["strategy"]: r for r in comp.summary()}
    mix, rot = by["mixture:delt-imid-fipr"], by["rotation:delt-imid-fipr"]
    assert mix["mean_survival"] < rot["mean_survival"], "mixture should control better"
    assert mix["resistant_runs"] > rot["resistant_runs"], "mixture should select harder"
    # and it selects more than one mechanism, which rotation does not
    assert mix["f_rdl"] > rot["f_rdl"] and mix["f_cyp6"] > rot["f_cyp6"]


def test_single_product_use_loses_on_both_counts():
    comp = compare_strategies(seeds=8, generations=30, log=lambda *_: None)
    by = {r["strategy"]: r for r in comp.summary()}
    sing, rot = by["single:deltamethrin"], by["rotation:delt-imid-fipr"]
    assert sing["resistant_runs"] > rot["resistant_runs"]
    assert sing["mean_survival"] > rot["mean_survival"], "resistance destroys its own control"


# -------------------------------------------------------------------- outputs --
def test_censored_arms_are_not_given_a_number_they_never_produced():
    comp = compare_strategies(arms=(untreated(),), seeds=3, generations=10, log=lambda *_: None)
    row = comp.summary()[0]
    assert row["resistant_runs"] == 0
    assert row["median_time_to_resistance"] is None
    assert "never" in report(comp)


def test_report_states_why_population_size_is_not_the_control_metric():
    comp = compare_strategies(seeds=2, generations=8, log=lambda *_: None)
    text = report(comp)
    assert "mean survival" in text
    assert "carrying capacity" in text and "rebounds" in text


def test_runs_are_written_out_per_seed(tmp_path):
    comp = compare_strategies(arms=(single(), untreated()), seeds=3, generations=8,
                              log=lambda *_: None)
    p = tmp_path / "runs.csv"
    write_csv(comp, p)
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 1 + 6                      # header plus two arms times three seeds
    assert "mean_survival" in lines[0] and "time_to_resistance" in lines[0]


def test_a_single_run_records_its_trajectory():
    r = run_once(single(), seed=1, generations=15, profile=HEAVY)
    assert r.generations == 15
    assert 0.0 <= r.mean_survival <= 1.0
    assert r.peak_target_site >= r.final_freqs["kdr"] - 1e-9
    if r.time_to_resistance is not None:
        assert 0 < r.time_to_resistance <= 15
        assert r.peak_target_site > RESISTANCE_THRESHOLD
