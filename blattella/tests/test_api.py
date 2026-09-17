import pytest
from fastapi.testclient import TestClient

from blattella.api import LIMITS, app

client = TestClient(app)


# ---------------------------------------------------------------------- meta --
def test_health_lists_what_the_server_can_do():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body["actives"]) == {"deltamethrin", "imidacloprid", "fipronil"}
    assert body["limits"] == LIMITS


def test_strategies_describe_themselves_in_words():
    rows = client.get("/api/strategies").json()
    ids = {r["id"] for r in rows}
    assert {"single", "rotation", "rotation3", "mixture", "untreated"} <= ids
    for r in rows:
        assert len(r["description"]) > 10
    mix = next(r for r in rows if r["id"] == "mixture")
    assert mix["dose_share"] == pytest.approx(1 / 3), "arms must stay matched on total dose"


def test_starting_from_a_resistant_colony_is_a_supported_scenario():
    """Most control programmes inherit resistance rather than creating it."""
    rare = client.post("/api/evolve", json={"strategy": "untreated", "generations": 1,
                                            "population": 600}).json()
    common = client.post("/api/evolve", json={"strategy": "untreated", "generations": 1,
                                              "population": 600,
                                              "founder_frequency": 0.7}).json()
    assert rare["history"][0]["f_kdr"] < 0.12
    assert common["history"][0]["f_kdr"] > 0.6


def test_parameters_separate_measurements_from_assumptions():
    body = client.get("/api/parameters").json()
    assert all(p["cite"] for p in body["literature"])
    assert all(len(p["sweep"]) == 2 for p in body["assumptions"])
    assert len(body["assumptions"]) > len(body["literature"])


# ----------------------------------------------------------------- evolution --
def test_evolve_returns_a_trajectory_and_how_to_reproduce_it():
    r = client.post("/api/evolve", json={"strategy": "rotation", "generations": 10,
                                         "population": 200})
    assert r.status_code == 200
    b = r.json()
    assert b["generations_run"] == 10
    assert len(b["history"]) == 11                 # founders plus ten generations
    assert set(b["final"]) >= {"kdr", "rdl", "cyp6"}
    assert "blattella.cli evolve" in b["reproduce"]
    assert any("simulated" in c for c in b["caveats"])


def test_single_product_selects_harder_than_rotation_through_the_api():
    def kdr(strategy: str) -> float:
        r = client.post("/api/evolve", json={"strategy": strategy, "generations": 30,
                                             "population": 300, "seed": 1})
        return r.json()["final"]["kdr"]

    assert kdr("single") > kdr("rotation")


def test_the_fitness_cost_override_is_applied_and_then_restored():
    """
    The override is what makes the dashboard's most instructive control work: set
    the cost to zero and rotation stops helping. It must not leak into later runs.
    """
    from blattella import genome as gn

    before = float(gn.P["fitness_cost_target_site"])
    # start from an already-resistant colony, otherwise drift from a 5 % founder
    # frequency swamps the effect and the test measures noise
    base = {"strategy": "untreated", "generations": 30, "population": 800,
            "founder_frequency": 0.6, "seed": 3}
    free = client.post("/api/evolve", json={**base, "fitness_cost": 0.0}).json()
    costly = client.post("/api/evolve", json={**base, "fitness_cost": 0.3}).json()
    assert free["final"]["kdr"] > costly["final"]["kdr"] + 0.1, (free["final"], costly["final"])
    assert float(gn.P["fitness_cost_target_site"]) == before


def test_compare_reports_every_arm_with_its_censoring():
    r = client.post("/api/compare", json={"seeds": 3, "generations": 15, "population": 200})
    assert r.status_code == 200
    b = r.json()
    rows = {x["strategy"]: x for x in b["summary"]}
    assert len(rows) == 5
    untreated = rows["untreated"]
    assert untreated["resistant_runs"] == 0
    assert untreated["median_time_to_resistance"] is None
    assert "blattella.cli compare" in b["reproduce"]


# -------------------------------------------------------------------- colony --
def test_contact_returns_something_drawable():
    r = client.post("/api/contact", json={"colony": 60, "hours": 1.0})
    b = r.json()
    assert len(b["positions"]) == len(b["degree"]) == len(b["alive"]) == 60
    assert b["network"]["edges"] > 0
    assert len(b["harborages"]) == 6
    assert "toxicology" not in b


def test_baiting_the_colony_kills_some_of_it():
    r = client.post("/api/contact", json={"colony": 80, "hours": 3.0, "bait": "fipronil",
                                          "stations": 1})
    b = r.json()
    assert b["toxicology"]["dead"] > 0
    assert b["treated"] == [0]
    assert sum(b["alive"]) < 80


def test_an_unknown_active_is_rejected_rather_than_ignored():
    r = client.post("/api/contact", json={"colony": 20, "hours": 0.5, "bait": "DDT"})
    assert r.status_code == 422
    assert "DDT" in r.json()["detail"]


# ------------------------------------------------------- chemistry and neural --
def test_chem_puts_both_backends_against_the_measurement():
    b = client.post("/api/chem", json={"use_vqe": False}).json()
    assert b["reference"]["maturity"] == "reference"
    names = [x["backend"] for x in b["backends"]]
    assert names == ["classical-mm", "quantum-vqe"]
    by = {x["backend"]: x for x in b["backends"]}
    assert by["classical-mm"]["sign_agrees_with_reference"] is True
    assert by["quantum-vqe"]["sign_agrees_with_reference"] is False
    assert any("fit to drive the science" in c for c in b["caveats"])


def test_neural_returns_its_predictions_and_its_blind_spots():
    b = client.post("/api/neural", json={"burden_ld50": 0.5, "hours": [1.0, 24.0]}).json()
    assert len(b["rows"]) == 6                       # three actives times two time points
    assert len(b["testable_orderings"]) >= 3
    assert len(b["known_blind_spots"]) >= 2
    assert any("extrapolation" in c for c in b["caveats"])


# -------------------------------------------------------------------- limits --
@pytest.mark.parametrize("payload,field", [
    ({"generations": 9999}, "generations"),
    ({"population": 99999}, "population"),
])
def test_oversized_runs_are_refused_with_a_reason(payload, field):
    r = client.post("/api/evolve", json=payload)
    assert r.status_code == 422
    assert field in str(r.json())


def test_a_contact_run_cannot_be_asked_to_run_for_a_week():
    r = client.post("/api/contact", json={"colony": 50, "hours": 999})
    assert r.status_code == 422


# -------------------------------------------------------------------- export --
def test_exports_come_back_as_downloads_with_sensible_names():
    cases = [
        ("post", "/api/export/compare.csv", {"seeds": 2, "generations": 6, "population": 100},
         "strategy_summary.csv", "text/csv"),
        ("post", "/api/export/evolve.csv", {"generations": 6, "population": 100},
         "evolution.csv", "text/csv"),
        ("get", "/api/export/parameters.md", None, "PARAMETERS.md", "text/markdown"),
        ("post", "/api/export/chem.md", {"use_vqe": False}, "DDG_COMPARISON.md", "text/markdown"),
        ("post", "/api/export/neural.md", {"burden_ld50": 0.5, "hours": [1.0]},
         "NEURAL_PREDICTIONS.md", "text/markdown"),
    ]
    for method, url, payload, filename, media in cases:
        r = client.get(url) if method == "get" else client.post(url, json=payload)
        assert r.status_code == 200, url
        assert filename in r.headers["content-disposition"]
        assert r.headers["content-type"].startswith(media)
        assert len(r.text) > 50, url


def test_the_exported_csv_has_a_header_and_one_row_per_arm():
    r = client.post("/api/export/compare.csv",
                    json={"seeds": 2, "generations": 6, "population": 100})
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("strategy,")
    assert len(lines) == 1 + 5


# ------------------------------------------------- what the API says it returns --
def test_the_openapi_document_advertises_the_media_type_each_route_returns():
    """
    Every export route used to declare `text/plain` through `response_class`
    while putting CSV or Markdown on the wire, so a generated client parsed the
    wrong thing. The document and the response have to agree.
    """
    paths = app.openapi()["paths"]
    cases = [
        ("post", "/api/export/compare.csv", {"seeds": 2, "generations": 5, "population": 100}),
        ("post", "/api/export/evolve.csv", {"generations": 5, "population": 100}),
        ("get", "/api/export/parameters.md", None),
        ("post", "/api/export/chem.md", {"use_vqe": False}),
        ("post", "/api/export/neural.md", {"burden_ld50": 0.5, "hours": [1.0]}),
    ]
    for method, url, payload in cases:
        declared = set(paths[url][method]["responses"]["200"]["content"])
        r = client.get(url) if method == "get" else client.post(url, json=payload)
        actual = r.headers["content-type"].split(";")[0]
        assert actual in declared, f"{url} returns {actual}, document says {declared}"


def test_a_download_names_itself_in_a_header_a_browser_is_allowed_to_read():
    """
    Setting Content-Disposition is pointless cross-origin unless CORS exposes it;
    without that the dashboard silently falls back to guessing from the URL.
    """
    exposed = None
    for m in app.user_middleware:
        if m.cls.__name__ == "CORSMiddleware":
            exposed = m.kwargs["expose_headers"]
    assert exposed is not None, "the CORS middleware went missing"
    assert "Content-Disposition" in exposed


def test_an_unknown_mutation_is_refused_by_the_export_rather_than_crashing_it():
    """The JSON route always guarded this; the Markdown export raised KeyError."""
    r = client.post("/api/export/chem.md", json={"mutation": "L1014F"})
    assert r.status_code == 422
    assert "L1014F" in r.json()["detail"]
    assert client.post("/api/export/chem.md", json={"ligand": "DDT"}).status_code == 422


def test_a_csv_keeps_columns_that_only_some_rows_carry():
    """`extinct` appears partway through an evolution history, not in row zero."""
    from blattella.api import _csv

    out = _csv([{"a": 1}, {"a": 2, "b": 3}])
    assert out.splitlines()[0] == "a,b"
    assert out.splitlines()[1] == "1,"


def test_the_api_describes_itself_well_enough_to_be_cited():
    info = app.openapi()["info"]
    assert info["license"]["name"] == "MIT"
    assert "pest-control model" in info["description"]
    tags = {t["name"] for t in app.openapi()["tags"]}
    assert {"export", "interop", "colony"} <= tags
