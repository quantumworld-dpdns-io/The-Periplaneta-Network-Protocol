import numpy as np
import pytest

from blattella import toxicology as tx
from blattella.behaviour import make_colony, simulate
from blattella.toxicology import (DELTAMETHRIN, FIPRONIL, IMIDACLOPRID, Toxicology,
                                  _LD50_WINDOW_S, probit_mortality)

ALL = (DELTAMETHRIN, IMIDACLOPRID, FIPRONIL)


def _run(hours=3.0, n=100, seed=2, active="fipronil", actives=ALL, sites=(0,)):
    """One baited station, which is both the realistic placement and the setup in
    which secondary kill is distinguishable from direct feeding."""
    c = make_colony(n=n, seed=seed)
    c.t = 12 * 3600.0                       # start at dusk so the colony forages
    tox = Toxicology(colony=c, actives=actives)
    if active:
        tox.treat_resource(active, list(sites))
    simulate(c, hours * 3600.0, 1.0, observers=[tox])
    return c, tox


# ----------------------------------------------------------------- dose-response --
def test_probit_is_a_half_at_the_ld50_and_monotone():
    for a in ALL:
        assert probit_mortality(a.ld50, a.ld50) == pytest.approx(0.5)
        assert probit_mortality(0.1 * a.ld50, a.ld50) < 0.05
        assert probit_mortality(10 * a.ld50, a.ld50) > 0.95
    doses = np.array([0.1, 0.5, 1.0, 2.0, 10.0]) * FIPRONIL.ld50
    p = probit_mortality(doses, FIPRONIL.ld50)
    assert np.all(np.diff(p) > 0)


def test_hazard_reproduces_the_probit_below_the_delayed_action_cap():
    """
    The hazard is the inversion of the probit, so holding a burden constant for
    the assay window returns the probit value -- until the delayed-action cap
    binds. An earlier version scaled a fixed hazard by the probit, which capped
    24 h mortality at 75 % however large the dose; that was simply wrong.
    """
    c = make_colony(n=5, seed=0)
    tox = Toxicology(colony=c, actives=(FIPRONIL,))
    for mult in (0.1, 1.0, 2.0):                 # probit below ~0.95, cap not binding
        tox.burden[0, :] = mult * FIPRONIL.ld50
        p24 = 1.0 - np.exp(-tox.mortality_hazard()[0] * _LD50_WINDOW_S)
        assert p24 == pytest.approx(probit_mortality(mult * FIPRONIL.ld50, FIPRONIL.ld50), abs=1e-6)


def test_delayed_action_caps_how_fast_an_overdose_can_kill(monkeypatch):
    """
    Gel baits are formulated for delayed action so a poisoned animal gets back to
    the harborage before it dies. The hazard therefore has a ceiling of one death
    per `min_time_to_death_h`, no matter how many LD50s the animal is carrying.
    """
    c = make_colony(n=3, seed=0)
    tox = Toxicology(colony=c, actives=(FIPRONIL,))
    tox.burden[0, :] = 1e4 * FIPRONIL.ld50        # a realistic gel-bait dose
    cap = 1.0 / (float(tx.P["min_time_to_death_h"]) * 3600.0)
    assert tox.mortality_hazard()[0] == pytest.approx(cap)
    # without the cap the hazard rises to the ceiling set by the probit clamp,
    # about seven times faster, i.e. a median time to death near an hour
    monkeypatch.setitem(tx.P, "min_time_to_death_h", 1e-6)
    uncapped = tox.mortality_hazard()[0]
    assert uncapped > 5 * cap
    assert np.log(2) / uncapped < 3600.0


def test_lethal_fraction_adds_across_actives_in_ld50_units():
    c = make_colony(n=3, seed=0)
    tox = Toxicology(colony=c, actives=(DELTAMETHRIN, FIPRONIL))
    tox.burden[0, 0] = DELTAMETHRIN.ld50
    tox.burden[1, 0] = FIPRONIL.ld50
    assert tox.lethal_fraction()[0] == pytest.approx(2.0)
    assert tox.lethal_fraction()[1] == 0.0


def test_ld50_values_are_ordered_as_published():
    # fipronil has the lowest topical LD50 of the three, i.e. it is the most toxic
    assert FIPRONIL.ld50 < DELTAMETHRIN.ld50 < IMIDACLOPRID.ld50


# ------------------------------------------------------------ horizontal transfer --
def test_more_bait_stations_expose_more_of_the_colony():
    """
    Bait coverage drives the kill. Harborage groups forage at their nearest
    resource, so baiting one of three stations reaches only the groups that use
    it; baiting all three reaches everyone.
    """
    exposed = {}
    for stations in (1, 3):
        c, tox = _run(hours=8.0, sites=tuple(range(stations)))
        exposed[stations] = int((tox.acquired_from.sum(axis=0) > 0).sum())
    assert exposed[3] > 1.5 * exposed[1], exposed
    assert exposed[3] > 0.9 * 100


def test_insecticide_equilibrates_within_the_exposed_group():
    """
    Contact transfer is symmetric, so animals resting together converge on a
    common burden. This is why the exposed group shares one dose rather than the
    feeder keeping it all.
    """
    c, tox = _run(hours=8.0)
    b = tox.burden.sum(axis=0)
    top = np.sort(b)[::-1][:8]
    assert top[0] > 0
    # all but one sit on the same value to within a fraction of a percent; the
    # outlier is whoever is still at the bait taking up more, so the assertion
    # allows exactly one animal to be out of equilibrium
    med = float(np.median(top))
    close = np.abs(top - med) / med < 0.01
    assert close.sum() >= len(top) - 1, f"burdens have not equilibrated: {top}"


def test_secondary_kill_is_small_in_this_model():
    """
    An honest negative result, recorded so it is not quietly lost.

    Horizontal transfer moves most of the insecticide mass, but it moves it
    mostly between animals that also fed at the bait themselves, because the bait
    sits at a food resource and the group that contacts it is largely the group
    that eats there. Deaths among animals that never fed are therefore few.

    Two modelling choices probably understate the real effect and are recorded in
    docs/blattella/PHASE_LOG.md: contact is proximity only, with no trophallaxis,
    and harborage groups are strongly structured with only partial exchange.
    """
    c, tox = _run(hours=8.0)
    fed = tox.acquired_from[0] > 0
    dead = ~c.alive
    assert dead.sum() > 0
    assert (dead & ~fed).sum() <= 0.15 * dead.sum()


def test_imperfect_harborage_fidelity_widens_exposure(monkeypatch):
    """Letting some trips end in a different refuge carries insecticide between
    groups. With fidelity pinned at 1 the groups are effectively disjoint."""
    import blattella.behaviour as bh

    def exposed(switch: float) -> int:
        monkeypatch.setitem(bh.P, "harborage_switch_prob", switch)
        _, tox = _run(hours=8.0)
        return int((tox.acquired_from.sum(axis=0) > 0).sum())

    strict = exposed(0.0)
    monkeypatch.undo()
    loose = exposed(0.15)
    assert loose > strict, f"strict={strict} loose={loose}"


def test_without_transfer_routes_only_the_feeders_die(monkeypatch):
    """Control: shut every transfer route and the kill collapses to the animals
    that fed at the bait themselves."""
    monkeypatch.setitem(tx.P, "cuticular_transfer_rate", 0.0)
    monkeypatch.setitem(tx.P, "faecal_shed_fraction", 0.0)
    monkeypatch.setitem(tx.P, "corpse_scavenge_rate", 0.0)
    c, tox = _run(hours=3.0)
    fed = tox.acquired_from[0] > 0
    assert np.all(tox.acquired_from[1:, ~fed] == 0)
    assert (~c.alive)[~fed].sum() == 0, "an animal that never fed died with all transfer off"


def test_each_transfer_route_moves_insecticide_on_its_own(monkeypatch):
    for keep in ("cuticular_transfer_rate", "faecal_shed_fraction"):
        for off in ("cuticular_transfer_rate", "faecal_shed_fraction", "corpse_scavenge_rate"):
            monkeypatch.setitem(tx.P, off, 0.0 if off != keep else tx.P[off])
        c, tox = _run(hours=2.0)
        route = {"cuticular_transfer_rate": 1, "faecal_shed_fraction": 2}[keep]
        assert tox.acquired_from[route].sum() > 0, f"{keep} moved nothing"
        monkeypatch.undo()


def test_untreated_colony_suffers_no_mortality():
    c, tox = _run(hours=2.0, active=None)
    assert c.alive.all()
    assert tox.lethal_fraction().max() == 0.0
    assert tox.summary()["mortality"] == 0.0


# ---------------------------------------------------------------------- dynamics --
def test_higher_bait_concentration_kills_faster(monkeypatch):
    """
    Concentration response, measured well below label rate. A 2.15 % gel delivers
    of the order of 10^4 LD50s per feeding bout, so at field strength the outcome
    saturates and a hundredfold dilution changes nothing. That saturation is a
    property of real baits, not an artefact: it is why horizontal transfer works.
    """
    def mortality(conc: float) -> float:
        monkeypatch.setitem(tx.P, "bait_concentration", conc)
        c, _ = _run(hours=2.0, seed=5)
        return float((~c.alive).mean())

    low = mortality(2e-7)
    monkeypatch.undo()
    high = mortality(2e-5)
    assert low < high, f"low={low:.3f} high={high:.3f}"
    assert low < 0.5, "the low arm should not already saturate"


def test_field_strength_bait_saturates(monkeypatch):
    """
    Companion to the test above. A hundredfold dilution of a label-rate gel
    changes nothing, because both still deliver thousands of LD50s per bout. The
    ceiling here is bait *coverage*, not concentration.
    """
    def mortality(conc: float) -> float:
        monkeypatch.setitem(tx.P, "bait_concentration", conc)
        c, _ = _run(hours=4.0, seed=5)
        return float((~c.alive).mean())

    label = mortality(0.0215)
    monkeypatch.undo()
    hundredth = mortality(0.000215)
    assert abs(label - hundredth) < 0.1, f"label={label:.3f} hundredth={hundredth:.3f}"
    assert label > 0.15


def test_clearance_removes_burden_from_survivors(monkeypatch):
    c = make_colony(n=10, seed=1)
    tox = Toxicology(colony=c, actives=(FIPRONIL,))
    tox.burden[0, :] = 0.2 * FIPRONIL.ld50        # sublethal, so nobody dies
    start = tox.burden[0].copy()
    simulate(c, 3600.0, 10.0, observers=[tox])
    assert np.all(tox.burden[0] < start)


def test_corpses_stop_donating_once_they_have_decayed(monkeypatch):
    monkeypatch.setitem(tx.P, "corpse_persistence_h", 0.0)
    c, tox = _run(hours=2.0)
    assert tox.acquired_from[3].sum() == 0.0


def test_deaths_are_timestamped_and_corpses_age():
    c, tox = _run(hours=3.0)
    dead = ~c.alive
    assert dead.any()
    assert np.all(np.isfinite(tox.deaths[dead]))
    assert np.all(np.isnan(tox.deaths[~dead]))
    assert np.all(tox.corpse_age[dead] >= 0)


def test_summary_route_shares_sum_to_one():
    _, tox = _run(hours=2.0)
    shares = tox.summary()["acquisition_routes"]
    assert sum(shares.values()) == pytest.approx(1.0, abs=1e-3)


# ------------------------------------------------------------------- provenance --
def test_published_ld50s_are_cited_and_the_unpinned_one_is_declared():
    from blattella.params import Source
    assert DELTAMETHRIN.ld50_ug.source is Source.LITERATURE
    assert FIPRONIL.ld50_ug.source is Source.LITERATURE
    # no susceptible-strain value was located for imidacloprid, so it must be an
    # explicit assumption with a sweep rather than a number presented as fact
    assert IMIDACLOPRID.ld50_ug.source is Source.ASSUMPTION
    assert IMIDACLOPRID.ld50_ug.sweep is not None
