"""
The live session is what the dashboard homepage runs.

These tests never touch the SSE endpoint: an endless stream and a synchronous
test client do not mix, and a test that hangs is worse than no test. The stream
is a thin loop around `advance()` and `frame()`, both of which are exercised
directly here, plus a frame ceiling asserted below.
"""
import base64

import numpy as np
import pytest
from fastapi.testclient import TestClient

from blattella.api import MAX_STREAM_FRAMES, app
from blattella.behaviour import make_colony
from blattella.live import GRID, LAYERS, MAX_SESSIONS, Registry, _quantise
from blattella.toxicology import ACTIVES, Toxicology

client = TestClient(app)


@pytest.fixture
def session():
    sid = client.post("/api/live", json={"colony": 40, "speed": 60}).json()["session"]
    yield sid
    client.delete(f"/api/live/{sid}")


# ------------------------------------------------------------------ quantise --
def test_quantise_keeps_the_hot_cell_hot_and_the_cold_floor_cold():
    f = np.zeros((96, 96))
    f[40, 40] = 5.0
    data, peak = _quantise(f)
    assert peak == 5.0
    px = np.frombuffer(base64.b64decode(data), dtype=np.uint8).reshape(GRID, GRID)
    assert px.max() == 255
    assert px[20, 20] == 255, "the block containing the hot cell must carry its value"
    assert px.sum() == 255, "and nothing else may light up"


def test_quantise_survives_a_grid_that_does_not_divide_evenly():
    data, peak = _quantise(np.ones((100, 73)))
    assert peak == 1.0
    assert len(base64.b64decode(data)) == GRID * GRID


def test_an_empty_field_is_sent_as_zeros_rather_than_dividing_by_its_peak():
    data, peak = _quantise(np.zeros((60, 60)))
    assert peak == 0.0
    assert set(base64.b64decode(data)) == {0}


# ------------------------------------------------------------ open population --
def test_animals_can_be_let_in_and_trapped_and_the_burden_array_follows():
    colony = make_colony(n=30, seed=1)
    tox = Toxicology(colony=colony, actives=tuple(ACTIVES.values()), seed=1)
    colony.add(12)
    tox.resize()
    assert colony.n == 42
    assert tox.burden.shape[1] == 42
    assert colony.alive.sum() == 42

    taken = colony.remove(10)
    assert len(taken) == 10
    assert colony.alive.sum() == 32
    assert colony.n == 42, "indices stay stable so the frame's arrays keep lining up"


def test_a_trap_cannot_take_more_animals_than_are_present():
    colony = make_colony(n=8, seed=2)
    assert len(colony.remove(50)) == 8
    assert colony.alive.sum() == 0
    assert len(colony.remove(1)) == 0


# -------------------------------------------------------------------- session --
def test_starting_a_session_returns_a_drawable_first_frame(session):
    body = client.get(f"/api/live/{session}").json()
    assert body["grid"] == GRID
    assert set(body["layers"]) == set(LAYERS)
    for name in LAYERS:
        assert len(base64.b64decode(body["layers"][name]["data"])) == GRID * GRID
    assert len(body["positions"]) == len(body["alive"]) == len(body["state"]) == 40
    assert body["counts"]["alive"] == 40
    assert body["dark"] is True, "sessions start at dusk, when the animals are out"


def test_reading_a_frame_does_not_advance_the_clock(session):
    a = client.get(f"/api/live/{session}").json()
    b = client.get(f"/api/live/{session}").json()
    assert a["elapsed_hours"] == b["elapsed_hours"] == 0.0


def test_baiting_a_live_session_puts_residue_on_the_floor_and_kills(session):
    client.post(f"/api/live/{session}/act",
                json={"action": "bait", "params": {"active": "fipronil", "stations": 1}})
    client.post(f"/api/live/{session}/act", json={"action": "speed", "params": {"speed": 300}})
    from blattella.live import REGISTRY
    s = REGISTRY.get(session)
    for _ in range(40):                       # about three simulated hours
        s.advance()
    f = s.frame()
    assert f["treated"]["fipronil"] == [0]
    assert f["layers"]["residue"]["peak"] > 0, "the bait must leave something behind"
    assert f["counts"]["dead"] > 0
    assert f["counts"]["alive"] + f["counts"]["dead"] == 40


def test_a_paused_session_holds_still(session):
    from blattella.live import REGISTRY
    s = REGISTRY.get(session)
    s.act("pause", {"paused": True})
    before = s.colony.t
    for _ in range(5):
        s.advance()
    assert s.colony.t == before
    s.act("pause", {"paused": False})
    s.advance()
    assert s.colony.t > before


def test_holding_the_lights_on_drives_the_colony_into_its_harborages(session):
    from blattella.live import REGISTRY
    s = REGISTRY.get(session)
    s.act("speed", {"speed": 300})
    s.act("light", {"phase": "light"})
    for _ in range(24):
        s.advance()
    assert not s.colony.is_dark()
    f = s.frame()
    assert f["counts"]["resting"] > f["counts"]["foraging"]


def test_every_action_is_logged_with_the_hour_it_happened(session):
    for action, params in [("add", {"n": 5}), ("remove", {"n": 2}),
                           ("aggregation", {"value": 0.5}), ("clear_bait", {})]:
        r = client.post(f"/api/live/{session}/act", json={"action": action, "params": params})
        assert r.status_code == 200, r.text
    events = client.get(f"/api/live/{session}").json()["events"]
    assert [e["note"] for e in events][-4:] == [
        "5 immigrated", "2 trapped", "aggregation pull 0.50", "bait removed",
    ]
    assert all("t_hours" in e for e in events)


def test_letting_animals_in_grows_every_per_animal_array_together(session):
    f = client.post(f"/api/live/{session}/act",
                    json={"action": "add", "params": {"n": 25}}).json()["frame"]
    assert f["counts"]["alive"] == 65
    assert len(f["positions"]) == len(f["alive"]) == len(f["lethal_fraction"]) == 65


# --------------------------------------------------------------------- limits --
def test_an_unknown_action_is_refused_by_the_schema(session):
    """The action list is a Literal, so a bad name never reaches the simulation."""
    r = client.post(f"/api/live/{session}/act", json={"action": "nuke", "params": {}})
    assert r.status_code == 422
    detail = r.json()["detail"][0]
    assert detail["loc"] == ["body", "action"] and detail["input"] == "nuke"


def test_an_unknown_active_is_refused(session):
    r = client.post(f"/api/live/{session}/act",
                    json={"action": "bait", "params": {"active": "DDT"}})
    assert r.status_code == 422


def test_acting_on_a_stopped_session_is_a_404():
    sid = client.post("/api/live", json={"colony": 10}).json()["session"]
    assert client.delete(f"/api/live/{sid}").json() == {"stopped": True}
    assert client.get(f"/api/live/{sid}").status_code == 404
    assert client.post(f"/api/live/{sid}/act", json={"action": "pause"}).status_code == 404


def test_the_registry_evicts_the_oldest_session_rather_than_growing():
    reg = Registry()
    ids = [reg.create(10, 3, 2, 100.0, i, 30).id for i in range(MAX_SESSIONS + 3)]
    assert reg.count() == MAX_SESSIONS
    assert reg.get(ids[0]) is None
    assert reg.get(ids[-1]) is not None


def test_the_stream_has_a_frame_ceiling():
    """A browser that vanishes must not leave a colony running for the process's life."""
    assert 0 < MAX_STREAM_FRAMES <= 100_000


def test_health_reports_how_many_colonies_are_running():
    body = client.get("/api/health").json()
    assert "live_sessions" in body
    assert body["max_live_sessions"] == MAX_SESSIONS


# ------------------------------------------------------------------ stations --
def test_a_station_no_animal_walks_to_is_reported_as_unused():
    """
    Animals head for their *nearest* food, so a site that is nearest to no
    harborage is never visited. Baiting it kills nobody, and the frame has to
    say so rather than leaving the reader waiting for a death that cannot come.
    """
    from blattella.live import Registry

    reg = Registry()
    s = reg.create(180, 6, 4, 300.0, 7, 60)
    for _ in range(40):
        s.advance()
    visits = s.station_visits()
    assert visits[0] == 0, "seed 7 puts station 0 out of everybody's way"
    assert sum(visits) > 0
    assert s.frame()["station_visits"] == visits


def test_the_bait_button_treats_a_station_the_animals_actually_use():
    from blattella.live import Registry

    reg = Registry()
    s = reg.create(180, 6, 4, 300.0, 7, 60)
    for _ in range(40):
        s.advance()
    s.act("bait", {"active": "fipronil", "stations": 1})
    assert s.tox.treated_resources["fipronil"] != {0}
    for _ in range(160):
        s.advance()
    f = s.frame()
    assert f["counts"]["dead"] > 0, "a busy station must actually kill"
    assert f["layers"]["residue"]["peak"] > 0


def test_an_explicit_station_index_overrides_the_busiest_choice(session):
    from blattella.live import REGISTRY

    s = REGISTRY.get(session)
    s.act("bait", {"active": "fipronil", "indices": [2]})
    assert s.tox.treated_resources["fipronil"] == {2}


def test_clicking_a_station_toggles_only_that_one(session):
    from blattella.live import REGISTRY

    s = REGISTRY.get(session)
    s.act("bait", {"active": "fipronil", "indices": [0, 1]})
    s.act("toggle_station", {"index": 0, "active": "fipronil"})
    assert s.tox.treated_resources["fipronil"] == {1}, "the other station must survive"
    s.act("toggle_station", {"index": 0, "active": "fipronil"})
    assert s.tox.treated_resources["fipronil"] == {0, 1}


def test_toggling_a_station_that_does_not_exist_is_refused(session):
    r = client.post(f"/api/live/{session}/act",
                    json={"action": "toggle_station",
                          "params": {"index": 99, "active": "fipronil"}})
    assert r.status_code == 422
