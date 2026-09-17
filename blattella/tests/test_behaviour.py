import numpy as np
import pytest

from blattella import behaviour as bh
from blattella.arena import Arena, Site, default_arena
from blattella.behaviour import FORAGING, RESTING, make_colony, simulate
from blattella.contact import ContactRecorder
from blattella.params import Param, Source, assumptions, provenance_report


# --------------------------------------------------------------------- arena --
def test_default_arena_places_harborages_and_resources_apart():
    a = default_arena(n_harborages=6, n_resources=3, seed=1)
    assert len(a.harborages) == 6 and len(a.resources) == 3
    for h in a.harborages:
        for r in a.resources:
            assert np.hypot(h.x - r.x, h.y - r.y) > h.r + r.r


def test_site_contains_and_arena_clip():
    s = Site(10.0, 10.0, 3.0)
    pts = np.array([[10.0, 10.0], [12.0, 10.0], [20.0, 20.0]])
    assert list(s.contains(pts)) == [True, True, False]
    a = Arena(width=50, height=40, harborages=(s,))
    clipped = a.clip(np.array([[-5.0, 100.0], [60.0, -3.0]]))
    assert (clipped >= 0).all() and clipped[0, 1] <= 40 and clipped[1, 0] <= 50


# ----------------------------------------------------------------- behaviour --
def test_colony_starts_inside_harborages_and_rests():
    c = make_colony(n=50, seed=0)
    assert c.xy.shape == (50, 2)
    assert (c.state == RESTING).all()
    # everyone starts near their own harborage
    hc = c.arena.harborage_centres()[c.home]
    assert np.hypot(*(c.xy - hc).T).max() < 6 * c.arena.harborages[0].r


def test_individuals_forage_at_night_and_mostly_stay_in_by_day():
    """
    Nocturnality, measured as cumulative foraging trips rather than a snapshot:
    a snapshot misses everyone who has already been out, fed and come home.
    """
    def trips(start_hours: float) -> float:
        c = make_colony(n=150, seed=3)
        c.t = start_hours * 3600.0
        c.satiety[:] = 0.2          # everyone hungry, so light is the only difference
        simulate(c, duration_s=300, dt=1.0)
        return float(c.emergences.mean())

    night = trips(start_hours=13.0)   # scotophase under 12:12 LD
    day = trips(start_hours=1.0)      # photophase
    # the hazard ratio is 17x, but over a 300 s window the dark phase saturates at
    # roughly one trip per animal, so the observable ratio is bounded well below that
    assert night > 3 * day, f"night={night:.2f} day={day:.2f} trips per animal"
    assert night > 0.8, "nearly every hungry animal should get out during the dark phase"


def test_is_dark_follows_the_light_cycle():
    c = make_colony(n=5, seed=0)
    c.t = 2 * 3600.0
    assert not c.is_dark()
    c.t = 18 * 3600.0
    assert c.is_dark()


def test_pheromone_accumulates_where_animals_rest_and_then_decays():
    c = make_colony(n=80, seed=2)
    simulate(c, duration_s=120, dt=1.0)
    assert c.pheromone.sum() > 0
    peak = c.pheromone.max()
    # the peak sits near a harborage, not in open floor
    cy, cx = np.unravel_index(np.argmax(c.pheromone), c.pheromone.shape)
    px, py = cx * bh.PHERO_CELL_CM, cy * bh.PHERO_CELL_CM
    assert min(np.hypot(px - h.x, py - h.y) for h in c.arena.harborages) < 15.0
    # with nobody depositing, the field decays
    c.alive[:] = False
    simulate(c, duration_s=3600, dt=10.0)
    assert c.pheromone.max() < peak


# ---------------------------------------------- the property that defines this project --
def _trajectory(seed: int, n: int, remove: int | None, steps: int) -> np.ndarray:
    c = make_colony(n=n, seed=seed)
    c.satiety[:] = 0.2
    c.t = 13 * 3600.0
    if remove is not None:
        c.alive[remove] = False
    simulate(c, duration_s=steps, dt=1.0)
    return c.positions()


def test_removing_an_individual_changes_the_others():
    """
    The defining property of this project: individuals are coupled, so deleting
    one changes where the others end up. The old prototype failed this by
    construction (its nodes were conditionally independent and a test asserted
    that nodes outside an intervention were untouched).
    """
    full = _trajectory(seed=7, n=120, remove=None, steps=400)
    without = _trajectory(seed=7, n=120, remove=11, steps=400)
    others = [i for i in range(120) if i != 11]
    moved = np.hypot(*(full[others] - without[others]).T)
    assert moved.max() > 1.0, "removing an individual left every other trajectory unchanged"
    assert (moved > 0.1).mean() > 0.2, "the perturbation should reach a good share of the colony"


def test_without_coupling_removing_an_individual_changes_nothing(monkeypatch):
    """
    Control for the test above. Zeroing *every* coupling channel -- pheromone,
    conspecific attraction and volume exclusion -- decouples the colony exactly,
    and the same deletion then has no effect at all. This proves the divergence
    above comes from the interaction terms and not from RNG misalignment, and it
    enumerates the complete set of channels through which individuals influence
    each other.
    """
    monkeypatch.setitem(bh.P, "pheromone_weight", 0.0)
    monkeypatch.setitem(bh.P, "conspecific_weight", 0.0)
    monkeypatch.setitem(bh.P, "repulsion_weight", 0.0)
    full = _trajectory(seed=7, n=60, remove=None, steps=200)
    without = _trajectory(seed=7, n=60, remove=11, steps=200)
    others = [i for i in range(60) if i != 11]
    assert np.allclose(full[others], without[others])


def test_pheromone_alone_is_enough_to_couple(monkeypatch):
    """Stigmergy on its own couples the colony: an animal that has stopped
    depositing still changes where the others go, with no direct social forces."""
    monkeypatch.setitem(bh.P, "conspecific_weight", 0.0)
    monkeypatch.setitem(bh.P, "repulsion_weight", 0.0)
    full = _trajectory(seed=5, n=80, remove=None, steps=300)
    without = _trajectory(seed=5, n=80, remove=3, steps=300)
    others = [i for i in range(80) if i != 3]
    assert not np.allclose(full[others], without[others])


# ------------------------------------------------------------------- contact --
def test_contacts_emerge_and_produce_a_connected_network():
    c = make_colony(n=120, seed=4)
    c.satiety[:] = 0.3
    c.t = 13 * 3600.0
    rec = ContactRecorder()
    simulate(c, duration_s=600, dt=1.0, recorder=rec)
    net = rec.network()
    s = net.summary()
    assert s["edges"] > 0, "no contacts emerged from the behaviour layer"
    assert 0.0 < s["density"] < 1.0
    assert s["largest_component_fraction"] > 0.1
    assert len(net.super_spreaders(3)) == 3
    assert net.strength().sum() == pytest.approx(2 * sum(net.weights.values()))
    # volume exclusion plus a kitchen-scale arena keep the aggregation from
    # collapsing to one point, so the graph is informative rather than complete
    assert s["density"] < 0.9


def test_activity_cycle_closes_and_animals_end_up_back_in_harborages():
    """
    Hungry -> foraging -> returning -> resting in a harborage. An earlier version
    conflated returning with resting, so satiated animals were stranded on the
    open floor at 0.05 cm/s and never got home; the colony slowly dispersed
    instead of re-aggregating.
    """
    c = make_colony(n=100, seed=8)
    c.satiety[:] = 0.3
    c.t = 13 * 3600.0
    simulate(c, duration_s=90, dt=1.0)
    assert (c.state == FORAGING).any(), "nobody emerged to forage"
    simulate(c, duration_s=1200, dt=1.0)
    assert (c.state == RESTING).mean() > 0.9, "the colony never settled back"
    assert c.arena.in_any_harborage(c.xy).mean() > 0.9, "animals settled outside any harborage"


def test_resting_animals_in_harborages_do_not_drift():
    c = make_colony(n=60, seed=9)
    c.satiety[:] = 1.0                      # nobody has any reason to leave
    before = c.positions()
    simulate(c, duration_s=600, dt=1.0)
    settled = c.arena.in_any_harborage(before)
    assert settled.any()
    assert np.allclose(c.positions()[settled], before[settled])


def test_contact_recorder_ignores_dead_individuals():
    c = make_colony(n=40, seed=6)
    c.alive[:] = False
    rec = ContactRecorder()
    simulate(c, duration_s=60, dt=1.0, recorder=rec)
    assert rec.network().n_edges == 0


# -------------------------------------------------------------------- params --
def test_assumption_parameters_must_declare_a_sweep_range():
    with pytest.raises(ValueError, match="sweep"):
        Param(1.0, "cm", Source.ASSUMPTION)
    with pytest.raises(ValueError, match="cite"):
        Param(1.0, "cm", Source.LITERATURE)
    with pytest.raises(ValueError, match="outside sweep"):
        Param(9.0, "cm", Source.ASSUMPTION, sweep=(0.0, 1.0))


def test_every_assumption_is_sweepable_and_the_report_lists_it():
    for p in assumptions():
        assert p.sweep is not None and p.sweep[0] <= p.value <= p.sweep[1]
    report = provenance_report()
    assert "forage_speed" in report and "contact_radius" in report
    assert "Assumptions requiring sensitivity analysis" in report
