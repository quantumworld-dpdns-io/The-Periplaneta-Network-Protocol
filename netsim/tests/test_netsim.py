import math

import numpy as np
import pytest

from netsim.detectors import (VOTE_LEVEL, alarm_time, bocpd_stat, cusum_stat, localise, network_series, node_statistic,
                              sustained_max, zscore_stat)
from netsim.experiment import Pipeline, SweepSpec, calibrate, evaluate_run, run_config
from netsim.field import Perturbation
from netsim.renewal import RenewalParams, bin_counts, fit_gamma_renewal, fit_spike_train, load_twin_params, spike_train
from netsim.scenario import Config, simulate
from netsim.sensors import coverage_radius, layout, neighbours


# ----------------------------------------------------------------- renewal --
def test_twin_params_load_and_are_sane():
    units, meta = load_twin_params()
    assert len(units) >= 10
    assert "source" in meta and "zenodo" in meta["source"]
    for u in units:
        assert u.shape > 0 and u.rate_hz > 0 and u.refractory_ms >= 0
        assert 0 < 1.0 / u.rate_hz - u.refractory_s  # mean ISI longer than refractory


def test_twin_params_rate_is_the_firing_rate_not_the_gamma_rate():
    # unit 1 of twin_params.json: shape 0.6492, gamma rate 3.8746 Hz, refractory 2.1523 ms
    # -> firing rate 1/(0.0021523 + 0.6492/3.8746) = 5.893 Hz, which the Python refit of the
    # raw Zenodo 14281 train (e060517/Neuron1, 356 spikes) reproduces to three decimals.
    units, _ = load_twin_params()
    assert abs(units[0].rate_hz - 5.893) < 0.01
    # simulated train realises that firing rate
    t = spike_train(units[0], 3000.0, np.random.default_rng(0))
    assert abs(t.size / 3000.0 - units[0].rate_hz) / units[0].rate_hz < 0.05


def test_stationary_train_rate_and_cv():
    p = RenewalParams(shape=2.0, rate_hz=20.0, refractory_ms=2.0)
    rng = np.random.default_rng(0)
    t = spike_train(p, 600.0, rng)
    assert abs(t.size / 600.0 - 20.0) / 20.0 < 0.03
    isi = np.diff(t)
    assert isi.min() >= p.refractory_s - 1e-9
    assert abs(isi.std() / isi.mean() - p.isi_cv) < 0.05


def test_modulated_train_follows_rate_profile_and_preserves_shape():
    p = RenewalParams(shape=3.0, rate_hz=15.0, refractory_ms=1.0)
    rng = np.random.default_rng(1)
    T = 200
    m = np.ones(T)
    m[100:] = 0.25
    m[180:] = 0.0
    t = spike_train(p, float(T), rng, modulation=m)
    c = bin_counts(t, float(T))
    assert abs(c[:100].mean() - 15.0) < 1.0
    assert abs(c[100:180].mean() - 3.75) < 0.6
    assert c[180:].sum() == 0
    # ISI CV inside the stationary first half matches the parametric CV
    isi = np.diff(t[t < 100])
    assert abs(isi.std() / isi.mean() - p.isi_cv) < 0.08


def test_mle_recovers_parameters():
    truth = RenewalParams(shape=1.8, rate_hz=12.0, refractory_ms=3.0)
    rng = np.random.default_rng(7)
    t = spike_train(truth, 2000.0, rng)
    est = fit_spike_train(t)
    assert abs(est.shape - truth.shape) / truth.shape < 0.12
    assert abs(est.rate_hz - truth.rate_hz) / truth.rate_hz < 0.03
    assert abs(est.refractory_ms - truth.refractory_ms) < 1.0


def test_mle_without_refractory_snaps_to_zero():
    rng = np.random.default_rng(3)
    isi = rng.gamma(2.5, 0.04, size=5000)
    est = fit_gamma_renewal(isi)
    # shifted-gamma profile likelihood is shallow near r=0: accept a small spurious shift (< 10% of the mean ISI)
    assert est.refractory_ms < 0.1 * 1e3 * isi.mean()
    assert abs(est.shape - 2.5) < 0.3
    assert abs(est.rate_hz - 1.0 / isi.mean()) / (1.0 / isi.mean()) < 0.01


# ------------------------------------------------------------------- field --
def test_front_arrival_collapse_and_symptom_onset():
    xy = np.array([[0.0, 0.0], [30.0, 40.0]])  # second sensor 50 units away
    pert = Perturbation(origin=(0.0, 0.0), onset_s=10.0, speed=2.0, tau_s=8.0)
    arr = pert.arrival_times(xy)
    assert arr[0] == 10.0 and arr[1] == 35.0
    col = pert.collapse_times(xy)
    assert math.isclose(col[0], 10.0 + 8.0 * math.log(20.0) + 2.0)
    assert math.isclose(pert.symptom_onset(), col[0])  # sensor 0 sits at the origin
    m = pert.modulation(xy, 60)
    assert np.all(m[:, :10] == 1.0)
    assert m[0, 20] < m[0, 12] < 1.0
    assert m[1, 30] == 1.0 and m[1, 45] < 1.0


# ----------------------------------------------------------------- sensors --
@pytest.mark.parametrize("kind", ["grid", "random", "clustered"])
def test_layouts_have_n_points_inside_domain(kind):
    xy = layout(kind, 50, (100.0, 60.0), np.random.default_rng(0))
    assert xy.shape == (50, 2)
    assert xy[:, 0].min() >= 0 and xy[:, 0].max() <= 100
    assert xy[:, 1].min() >= 0 and xy[:, 1].max() <= 60


def test_grid_covers_better_than_clustered():
    rng = np.random.default_rng(0)
    g = coverage_radius(layout("grid", 36, (100.0, 100.0), rng), (100.0, 100.0))
    c = coverage_radius(layout("clustered", 36, (100.0, 100.0), rng), (100.0, 100.0))
    assert g < c


def test_neighbours_self_first():
    xy = layout("grid", 16, (40.0, 40.0), np.random.default_rng(0), jitter=0.0)
    nb = neighbours(xy, 4)
    assert nb.shape == (16, 4)
    assert np.all(nb[:, 0] == np.arange(16))


# --------------------------------------------------------------- detectors --
def _step_counts(n=5, T=80, B=30, drop_at=50, base=10.0, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.poisson(base, size=(n, T)).astype(float)
    X[0, drop_at:] = rng.poisson(base * 0.1, size=T - drop_at)
    return X, B


def test_zscore_cusum_bocpd_respond_to_a_drop_only_after_baseline():
    X, B = _step_counts()
    for name in ("zscore", "cusum", "bocpd"):
        s = node_statistic(name, X, B)
        assert np.all(s[:, :B] == 0.0)
        if name == "bocpd":
            assert s[0, 55:].mean() > 0.6 and s[1:, B:].max() < 0.6
        else:
            assert s[0, 60:].mean() > s[1:, 60:].mean() + 1.0


def test_bocpd_is_a_probability_and_ignores_upward_changes():
    X, B = _step_counts()
    X[1, 50:] += 30.0  # upward change on sensor 1
    s = bocpd_stat(X, B)
    assert s.min() >= 0.0 and s.max() <= 1.0 + 1e-9
    assert s[0, 52:60].min() > 0.5
    assert s[1, 50:].max() < 0.1


def test_network_rules_and_alarm_time():
    X, B = _step_counts()
    z = zscore_stat(X, B)
    nb = np.tile(np.arange(5)[:, None], (1, 1))
    assert network_series(z, "max").shape == (80,)
    assert np.allclose(network_series(z, "pool", nb), z.max(axis=0))  # k=1 pooling is identity
    v = network_series(z, "vote")
    assert v[60:].max() >= 1
    S = network_series(cusum_stat(X, B), "max")
    h = sustained_max(S[:50], B, 3) if S[:50].size else 0
    t = alarm_time(S, max(h, 1.0), B, 3)
    assert t is not None and 50 < t < 60
    assert alarm_time(np.zeros(80), 0.5, B, 3) is None


def test_vote_level_is_reachable_for_every_detector():
    X, B = _step_counts()
    for name in ("zscore", "cusum", "bocpd"):
        v = network_series(node_statistic(name, X, B), "vote", vote_level=VOTE_LEVEL[name])
        assert v[55:].max() >= 1, name


def test_localise_weighted_centroid():
    xy = np.array([[0.0, 0.0], [10.0, 0.0], [100.0, 100.0]])
    est = localise(np.array([3.0, 3.0, 0.0]), xy, level=1.0)
    assert np.allclose(est, [5.0, 0.0])
    est2 = localise(np.array([0.1, 0.2, 0.05]), xy, level=1.0)
    assert np.allclose(est2, [10.0, 0.0])  # fallback: argmax


# ---------------------------------------------------------------- scenario --
def test_simulate_shapes_and_ground_truth():
    cfg = Config(n_sensors=20, topology="grid", noise=0.1, duration_s=90.0)
    run = simulate(cfg, seed=5)
    assert run.counts.shape == (20, 90)
    assert run.xy.shape == (20, 2)
    assert run.perturbation is not None and math.isclose(run.t_symptom, cfg.onset_s + cfg.tau_s * math.log(20.0) + 2.0)
    null = simulate(cfg, seed=5, perturbed=False)
    assert null.t_symptom is None
    # perturbed run ends quieter than the null run on average
    assert run.clean_counts[:, -10:].mean() < null.clean_counts[:, -10:].mean()


def test_simulation_is_deterministic_in_seed():
    cfg = Config(n_sensors=12, duration_s=60.0)
    a, b = simulate(cfg, 11), simulate(cfg, 11)
    assert np.array_equal(a.counts, b.counts)
    assert a.perturbation == b.perturbation


# -------------------------------------------------------------- experiment --
def test_calibration_and_evaluation_end_to_end():
    cfg = Config(n_sensors=25, topology="grid", noise=0.1, duration_s=120.0)
    nulls = [simulate(cfg, 1000 + i, perturbed=False) for i in range(10)]
    p = Pipeline("cusum", "pool")
    h = calibrate(nulls, p, alpha=0.1)
    assert math.isfinite(h) and h > 0
    hits = 0
    for i in range(5):
        r = evaluate_run(simulate(cfg, i), p, h)
        hits += r.detected
        if r.detected:
            assert r.lead_s > 0 and 0 <= r.loc_err <= 1
    assert hits >= 3  # a 5x5 grid with CUSUM pooling should catch most fronts before first collapse


def test_run_config_returns_one_summary_per_pipeline():
    cfg = Config(n_sensors=16, duration_s=100.0)
    pipes = [Pipeline("zscore", "max"), Pipeline("cusum", "pool")]
    s, rows = run_config(cfg, pipes, n_calib=6, n_heldout=4, n_eval=3, alpha=0.2, seed0=1)
    assert [x.pipeline for x in s] == ["zscore+max", "cusum+pool"]
    assert len(rows) == 6
    assert all(0 <= x.far_heldout <= 1 for x in s)


def test_quick_spec_is_small():
    q = SweepSpec.quick()
    assert q.n_eval <= 10 and len(q.n_sensors) <= 3
