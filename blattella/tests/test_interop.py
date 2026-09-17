"""
The interoperability layer.

Most of these are parametrised over the format registry rather than written per
format, so the registry cannot claim a format it cannot render, or ship a file
that does not say what it refuses to claim.
"""
import csv
import io

import pytest
from fastapi.testclient import TestClient

from blattella import interop
from blattella.api import app
from blattella.interop import ligands, provenance as pv, sources, tables
from blattella.interop.spec import FormatSpec

client = TestClient(app)
IDS = sorted(interop.FORMATS)


def rows(data: bytes) -> list[dict]:
    body = [ln for ln in data.decode().splitlines() if not ln.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(body))))


def header(data: bytes) -> str:
    return "\n".join(ln for ln in data.decode().splitlines() if ln.startswith("#"))


# ------------------------------------------------------------ the registry --
@pytest.mark.parametrize("fid", IDS)
def test_every_declared_format_states_what_it_is_not(fid):
    """
    The receiving tool's conventions assert more than this model earned unless
    the file says otherwise. This is the export layer's version of the rule that
    `Param` enforces for citations.
    """
    spec = interop.FORMATS[fid]
    assert len(spec.what_it_is_not.strip()) > 30
    assert len(spec.what_it_is.strip()) > 20


@pytest.mark.parametrize("fid", IDS)
def test_every_declared_format_names_software_that_would_read_it(fid):
    assert interop.FORMATS[fid].consumers


@pytest.fixture(scope="session")
def run():
    from blattella.interop.runs import colony_run

    return colony_run(colony=40, hours=2.0, bait="fipronil", seed=5)


def _render(fid, run):
    spec = interop.FORMATS[fid]
    return interop.render(fid, run=run) if spec.needs_run else interop.render(fid)


@pytest.mark.parametrize("fid", IDS)
def test_every_declared_format_renders_or_says_which_data_it_is_waiting_for(fid, run):
    try:
        art = _render(fid, run)
    except interop.DataUnavailable as e:
        assert e.missing and e.fetch.startswith("./data/fetch.sh")
        assert interop.availability(fid) in ("needs_data", "degraded")
        return
    except interop.FormatUnsupported as e:
        assert e.library
        return
    assert len(art) > 0
    assert art.filename.startswith("blattella_")


@pytest.mark.parametrize("fid", IDS)
def test_every_rendered_file_carries_its_provenance_inside_itself(fid, run):
    """A caveat in a README is a caveat that gets stripped."""
    import io as _bio
    import zipfile as _zip

    try:
        data = _render(fid, run).data
    except (interop.DataUnavailable, interop.FormatUnsupported):
        pytest.skip("reference data or optional library not present")
    if data[:2] == b"PK":
        # an archive carries its provenance in the README it is required to hold
        body = _zip.ZipFile(_bio.BytesIO(data)).read("README.md").decode()
    else:
        body = data.decode(errors="replace")
    assert "Periplaneta" in body
    assert "what_it_is_not" in body or "WHAT THIS IS NOT" in body or "What it is not" in body
    assert "git_commit" in body or "commit" in body


def test_a_format_cannot_be_registered_without_declaring_its_negative():
    with pytest.raises(ValueError, match="what it is NOT"):
        FormatSpec(id="x", title="t", spec="s", spec_url="u", media_type="text/csv",
                   filename="f", method="GET", path="/p", stage="st",
                   consumers=("R",), what_it_is="something", what_it_is_not="  ",
                   cli="c", render=lambda **_: b"")


def test_a_format_cannot_be_registered_without_naming_a_consumer():
    with pytest.raises(ValueError, match="at least one tool"):
        FormatSpec(id="x", title="t", spec="s", spec_url="u", media_type="text/csv",
                   filename="f", method="GET", path="/p", stage="st",
                   consumers=(), what_it_is="something",
                   what_it_is_not="not a calibrated anything at all",
                   cli="c", render=lambda **_: b"")


# ------------------------------------------------------------- provenance --
def test_the_parameter_table_counts_every_parameter_the_model_declares():
    """
    Reading the registry without importing the modules that declare parameters
    reports zero assumptions, which is the most flattering possible lie about
    this model.
    """
    c = pv.counts()
    assert c["total"] > 50
    assert c["assumption"] > c["literature"], "this model is mostly assumptions and must say so"
    assert len(rows(pv.params_csv())) == c["total"]


def test_every_literature_row_carries_a_citation_and_every_assumption_a_sweep():
    for r in rows(pv.params_csv()):
        if r["source"] == "literature":
            assert r["cite"], r
        if r["source"] == "assumption":
            assert r["sweep_low"] and r["sweep_high"], r


def test_the_provenance_block_says_this_is_not_a_therapeutics_model():
    body = pv.params_csv().decode()
    assert "pest-control model" in body
    assert "no human pharmacokinetics" in body.lower() or "no human pharmacokinetics" in body


# ----------------------------------------------------------- dose-response --
def test_the_dose_response_curve_passes_through_half_at_the_ld50():
    data = rows(tables.dose_response_csv(active="fipronil", points=3, lo_ld50=0.01, hi_ld50=100))
    at_ld50 = [r for r in data if float(r["dose_ld50_multiples"]) == pytest.approx(1.0)]
    assert len(at_ld50) == 1
    assert float(at_ld50[0]["mortality_fraction_24h"]) == pytest.approx(0.5, abs=1e-6)


def test_the_dose_response_dose_column_is_the_multiple_times_the_published_ld50():
    from blattella.toxicology import ACTIVES

    for r in rows(tables.dose_response_csv(points=5)):
        expected = float(r["dose_ld50_multiples"]) * ACTIVES[r["active"]].ld50
        assert float(r["dose_ug_per_insect"]) == pytest.approx(expected, rel=1e-5)


def test_mortality_never_decreases_as_the_dose_rises():
    data = rows(tables.dose_response_csv(active="deltamethrin", points=40))
    m = [float(r["mortality_fraction_24h"]) for r in data]
    assert m == sorted(m)


def test_the_dose_response_file_admits_the_slope_is_assumed():
    assert "assumption" in header(tables.dose_response_csv(points=3))


def test_an_unknown_active_is_refused_rather_than_silently_returning_nothing():
    with pytest.raises(KeyError, match="DDT"):
        tables.dose_response_csv(active="DDT")


# ------------------------------------------------------------ genetic map --
def test_the_genetic_map_never_puts_a_locus_on_a_chromosome_the_karyotype_lacks():
    from blattella.genome import N_CHROMOSOME_PAIRS

    for r in rows(tables.genetic_map_csv()):
        assert 1 <= int(r["chromosome"]) <= N_CHROMOSOME_PAIRS


def test_every_map_position_is_labelled_an_assumption_because_none_is_published():
    """The karyotype is literature. Not one centimorgan in this file is."""
    data = rows(tables.genetic_map_csv())
    assert {r["position_source"] for r in data} == {"assumption"}
    assert "NOT a linkage map" in header(tables.genetic_map_csv())


def test_the_unlinked_map_keeps_every_locus_on_its_own_chromosome():
    """Asserting linkage with no published map would be inventing data."""
    chroms = [r["chromosome"] for r in rows(tables.genetic_map_csv())]
    assert len(set(chroms)) == len(chroms)
    linked = [r["chromosome"] for r in rows(tables.genetic_map_csv(linked=True))]
    assert len(set(linked)) < len(linked), "the linked variant must actually link two loci"


def test_the_map_carries_the_targets_and_actives_the_model_uses():
    data = {r["locus"]: r for r in rows(tables.genetic_map_csv())}
    assert data["kdr"]["target"] == "Vssc" and data["kdr"]["kind"] == "target-site"
    assert data["cyp6"]["kind"] == "metabolic"
    assert "deltamethrin" in data["cyp6"]["actives"]


# -------------------------------------------------------- reference data --
def test_an_unfetched_source_reports_what_is_missing_and_how_to_get_it():
    st = sources.status()
    assert set(st["fetched"]) == {"pubchem", "ncbi", "uniprot"}
    assert st["accessions"]["ncbi"] == "AF281328.1"
    assert st["accessions"]["uniprot"] == "O01306"


# -------------------------------------------------------------- endpoints --
def test_the_discovery_endpoint_describes_every_format_and_its_audience():
    d = client.get("/api/interop/formats").json()
    assert {f["id"] for f in d["formats"]} == set(IDS)
    assert set(d["audiences"]) >= {"resistance-management", "nucleic-acid-therapeutics"}
    assert "no human pharmacokinetics" in d["audiences"]["nucleic-acid-therapeutics"]
    for f in d["formats"]:
        assert f["what_it_is_not"] and f["consumers"] and f["cli"]
        assert f["availability"] in ("ready", "degraded", "needs_data", "unsupported")


def test_every_registered_format_is_actually_routed():
    paths = {r.path for r in app.routes}
    for spec in interop.FORMATS.values():
        if "{" not in spec.path:
            assert spec.path in paths, f"{spec.id} claims {spec.path}, which does not exist"


def test_a_format_that_needs_a_simulation_is_a_post_and_the_rest_are_gets():
    """
    GET when the bytes are a pure function of committed code plus fetched data,
    so the URL is cacheable and can be cited. POST when serving it means running
    a colony whose cost scales with the request.
    """
    for spec in interop.FORMATS.values():
        assert spec.method == ("POST" if spec.needs_run else "GET"), spec.id


def test_a_download_names_the_format_it_is_in_a_header():
    r = client.get("/api/interop/genetic_map.csv")
    assert r.status_code == 200
    assert r.headers["x-blattella-format"] == "genetic_map.csv"
    assert 'filename="blattella_genetic_map.csv"' in r.headers["content-disposition"]


def test_a_dose_range_that_runs_backwards_is_refused():
    assert client.get("/api/interop/dose_response.csv?lo=10&hi=1").status_code == 422
    assert client.get("/api/interop/dose_response.csv?active=DDT").status_code == 422


# ------------------------------------------------------------------- SBML --
libsbml = pytest.importorskip("libsbml", reason="python-libsbml is an optional dependency")


# A libsbml Model is owned by its Document. Returning the model from a helper
# and letting the document fall out of scope leaves a dangling pointer, and
# dereferencing it crashes the interpreter rather than raising. The fixture holds
# the document for the whole session so that cannot happen.
@pytest.fixture(scope="session")
def sbml_doc():
    from blattella.interop.sbml import toxicokinetic_sbml

    doc = libsbml.readSBMLFromString(toxicokinetic_sbml().decode())
    assert doc.getNumErrors() == 0, [doc.getError(i).getMessage()
                                     for i in range(doc.getNumErrors())]
    return doc


@pytest.fixture(scope="session")
def sbml_model(sbml_doc):
    return sbml_doc.getModel()


@pytest.fixture(scope="session")
def sbml_params(sbml_model):
    return {sbml_model.getParameter(i).getId(): sbml_model.getParameter(i).getValue()
            for i in range(sbml_model.getNumParameters())}


def test_the_exported_model_is_valid_sbml_and_the_writer_refuses_to_emit_otherwise():
    """
    `toxicokinetic_sbml` runs checkConsistency and raises rather than shipping a
    file another tool would reject. The unit definitions were caught this way.
    """
    from blattella.interop.sbml import toxicokinetic_sbml

    doc = libsbml.readSBMLFromString(toxicokinetic_sbml().decode())
    assert doc.getNumErrors(libsbml.LIBSBML_SEV_ERROR) == 0
    assert doc.getLevel() == 3 and doc.getVersion() == 2


def test_the_sbml_clearance_constant_is_the_number_the_simulation_actually_uses(sbml_params):
    """
    The failure that would cost this project the most credibility is an exported
    model that has quietly drifted from the simulated one. Recompute, do not
    hard-code.
    """
    import math

    from blattella.toxicology import P

    expected = math.log(2.0) / (float(P["clearance_half_life_h"]) * 3600.0)
    assert sbml_params["k_clear"] == pytest.approx(expected, rel=1e-12)


def test_the_sbml_ingestion_constant_is_the_number_the_simulation_actually_uses(sbml_params):
    from blattella.toxicology import P

    expected = float(P["gel_ingestion_rate"]) * 1000.0 * float(P["bait_concentration"])
    assert sbml_params["k_ingest"] == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize("pid,param", [
    ("k_shed", "faecal_shed_fraction"), ("k_uptake", "faecal_uptake_rate"),
    ("k_decay", "residue_decay"), ("k_scavenge", "corpse_scavenge_rate"),
])
def test_every_sbml_rate_constant_matches_its_parameter_in_the_model(pid, param, sbml_params):
    from blattella.toxicology import P

    assert sbml_params[pid] == pytest.approx(float(P[param]), rel=1e-12)


def test_each_ld50_in_the_sbml_is_the_published_value_for_that_active(sbml_params):
    from blattella.toxicology import ACTIVES

    got = sbml_params
    for name, ins in ACTIVES.items():
        assert got[f"LD50_{name}"] == pytest.approx(ins.ld50, rel=1e-12)


def test_clearance_halves_the_burden_in_exactly_the_stated_half_life(sbml_params):
    import math

    from blattella.toxicology import P

    t_half = float(P["clearance_half_life_h"]) * 3600.0
    assert math.exp(-sbml_params["k_clear"] * t_half) == pytest.approx(0.5, abs=1e-9)


def test_the_model_declares_itself_an_insect_burden_model_and_not_a_pk_model(sbml_model):
    """
    "PK model" imports plasma and tissue kinetics in a vertebrate. This is
    whole-body burden in an arthropod and the identifier has to say so.
    """
    m = sbml_model
    assert "pk" not in m.getId().lower().replace("_", "")
    notes = m.getNotesString()
    assert "NOT a pharmacokinetic or PBPK model" in notes
    assert "NOT the agent-based model" in notes


def test_the_model_admits_that_contact_transfer_cannot_survive_the_reduction(sbml_model):
    """
    Horizontal transfer along the contact network is what makes gel baits work.
    The term is symmetric, so one representative individual gives and takes
    equally and it cancels. Dropping that silently would misrepresent the project.
    """
    notes = sbml_model.getNotesString()
    assert "cuticular transfer" in notes and "cancels" in notes
    ids = {sbml_model.getReaction(i).getId() for i in range(sbml_model.getNumReactions())}
    assert not any("contact" in i or "cuticular" in i for i in ids)


def test_the_model_says_why_mortality_is_absent_rather_than_looking_broken(sbml_model):
    """SBML's MathML has no error function, so a probit is not expressible."""
    notes = sbml_model.getNotesString()
    assert "error function" in notes and "dose_response.csv" in notes
    ids = {sbml_model.getReaction(i).getId() for i in range(sbml_model.getNumReactions())}
    assert not any("death" in i or "mortality" in i for i in ids)


def test_no_volume_is_invented_to_satisfy_the_schema(sbml_model):
    """
    The amounts are micrograms per insect. Writing a litre in to make the schema
    happy would put a fabricated number where a tool computes with it.
    """
    m = sbml_model
    for i in range(m.getNumSpecies()):
        s = m.getSpecies(i)
        assert s.getHasOnlySubstanceUnits(), s.getId()
        assert s.getSubstanceUnits() == "microgram"
    for i in range(m.getNumCompartments()):
        c = m.getCompartment(i)
        assert c.getSpatialDimensions() == 0, c.getId()


def test_the_model_starts_untreated_so_it_does_nothing_until_you_treat_it(sbml_params, sbml_model):
    got = sbml_params
    assert got["f_feeding"] == 0.0
    m = sbml_model
    for i in range(m.getNumSpecies()):
        assert m.getSpecies(i).getInitialAmount() == 0.0


def test_lethal_fraction_is_reported_in_ld50_units_across_every_active(sbml_model):
    from blattella.toxicology import ACTIVES

    m = sbml_model
    assert m.getNumRules() == 1
    rule = m.getRule(0)
    assert rule.getVariable() == "lethal_fraction"
    formula = libsbml.formulaToL3String(rule.getMath())
    for name in ACTIVES:
        assert f"burden_{name} / LD50_{name}" in formula


def test_every_assumed_rate_constant_carries_its_sweep_range_in_the_file(sbml_model):
    """An assumption that travels without its range is just a number."""
    m = sbml_model
    for pid in ("k_shed", "k_uptake", "k_decay", "k_scavenge"):
        notes = m.getParameter(pid).getNotesString()
        assert "assumption" in notes
        assert "sensitivity sweep" in notes
        assert "no published measurement" in notes


def test_restricting_to_one_active_drops_the_others_entirely():
    from blattella.interop.sbml import toxicokinetic_sbml

    m = libsbml.readSBMLFromString(
        toxicokinetic_sbml(actives=("fipronil",)).decode()).getModel()
    ids = {m.getSpecies(i).getId() for i in range(m.getNumSpecies())}
    assert ids == {"burden_fipronil", "residue_fipronil", "corpse_fipronil"}
    with pytest.raises(KeyError, match="DDT"):
        toxicokinetic_sbml(actives=("DDT",))


def test_the_sbml_endpoint_serves_what_it_says_it_serves():
    r = client.get("/api/interop/toxicokinetics.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/sbml+xml")
    assert libsbml.readSBMLFromString(r.text).getNumErrors() == 0
    assert client.get("/api/interop/toxicokinetics.xml?actives=DDT").status_code == 422


# ------------------------------------------------------- survival and network --
def test_the_survival_table_censors_survivors_instead_of_dropping_them(run):
    """
    Keeping only the deaths turns a survival table into a table of deaths and
    inflates every hazard fitted to it.
    """
    data = rows(tables.survival_csv(run))
    assert len(data) == run.colony.n
    events = sum(int(r["event"]) for r in data)
    assert events == int((~run.colony.alive).sum())
    for r in data:
        if r["event"] == "0":
            assert r["censored_at_s"], "a survivor must say when observation stopped"


def test_the_survival_table_reports_the_dose_at_death_not_the_dose_left_afterwards():
    """
    A corpse is scavenged, so its remaining burden falls towards zero. Reporting
    that next to an event would understate the dose that killed it by orders of
    magnitude.
    """
    from blattella.interop.runs import colony_run

    r = colony_run(colony=60, hours=4.0, bait="fipronil", seed=3)
    data = [d for d in rows(tables.survival_csv(r)) if d["event"] == "1"]
    assert data, "this seed is supposed to kill something"
    for d in data:
        assert float(d["lethal_fraction_at_event"]) > 1.0, d
    import numpy as np
    dead = ~r.colony.alive
    assert float(np.nanmax(r.tox.burden[:, dead])) < float(
        np.nanmax(r.tox.burden_at_death[:, dead])), "scavenging must actually remove burden"


def test_the_survival_table_says_its_censoring_is_only_the_end_of_the_run(run):
    head = header(tables.survival_csv(run))
    assert "administrative only" in head
    assert "NOT a bioassay" in head


def test_the_contact_edge_list_agrees_with_the_network_it_came_from(run):
    data = rows(tables.contact_edges_csv(run))
    assert len(data) == run.network.n_edges
    total = sum(float(r["contact_seconds"]) for r in data)
    assert 2 * total == pytest.approx(run.network.strength().sum(), rel=1e-9)


def test_no_animal_is_recorded_as_contacting_itself(run):
    for r in rows(tables.contact_edges_csv(run)):
        assert int(r["source"]) < int(r["target"])


def test_the_node_table_has_one_row_per_animal_and_joins_to_the_edge_list(run):
    nodes = rows(tables.contact_nodes_csv(run))
    assert len(nodes) == run.colony.n
    ids = {int(n["id"]) for n in nodes}
    for e in rows(tables.contact_edges_csv(run)):
        assert int(e["source"]) in ids and int(e["target"]) in ids


def test_the_node_degree_matches_the_number_of_edges_the_animal_appears_in(run):
    from collections import Counter

    seen = Counter()
    for e in rows(tables.contact_edges_csv(run)):
        seen[int(e["source"])] += 1
        seen[int(e["target"])] += 1
    for n in rows(tables.contact_nodes_csv(run)):
        assert int(n["degree"]) == seen[int(n["id"])]


def test_the_edge_list_says_it_is_not_an_observed_network(run):
    assert "NOT an observed network" in header(tables.contact_edges_csv(run))


def test_a_survival_table_without_a_treatment_is_refused_rather_than_returned_empty():
    r = client.post("/api/interop/survival.csv",
                    json={"colony": 30, "hours": 1.0})
    assert r.status_code == 422
    assert "treatment" in r.json()["detail"]


def test_the_run_backed_endpoints_serve_csv_for_a_treated_colony():
    body = {"colony": 40, "hours": 2.0, "bait": "fipronil", "stations": 1, "seed": 5}
    for url in ("/api/interop/survival.csv", "/api/interop/contact_edges.csv",
                "/api/interop/contact_nodes.csv"):
        resp = client.post(url, json=body)
        assert resp.status_code == 200, (url, resp.text[:200])
        assert resp.headers["content-type"].startswith("text/csv")
        assert "Periplaneta" in resp.text


def test_an_unknown_bait_is_refused_by_the_run_backed_endpoints():
    r = client.post("/api/interop/contact_edges.csv",
                    json={"colony": 20, "hours": 0.5, "bait": "DDT"})
    assert r.status_code == 422


# ------------------------------------------------------- reference data ----
def test_the_residue_the_chemistry_layer_mutates_is_a_leucine_in_the_real_record():
    """
    The single most valuable check in this file.

    `KDR_L993F` says leucine 993 of the sodium channel. L993F is the Blattella
    germanica numbering; L1014F is the Musca domestica numbering for the
    equivalent site, and the two are easy to confuse -- this project has been
    bitten by that confusion before. Asserting it against the fetched sequence
    turns a comment into something a machine verifies.
    """
    from blattella.chem import KDR_L993F

    path = sources.RAW / f"{sources.UNIPROT_ACCESSION}.fasta"
    if not path.is_file():
        pytest.skip(f"run: {sources.FETCH['uniprot']}")
    lines = path.read_text().splitlines()
    seq = "".join(lines[1:])
    assert len(seq) == 2031
    assert seq[KDR_L993F.position - 1] == KDR_L993F.wild == "L"
    # domain II S6 is a transmembrane helix; the context should look like one
    assert "GNLVVLNLFLAL" in seq


def test_the_fetched_cyp6k1_record_is_the_gene_and_the_coding_region_we_claim():
    path = sources.RAW / f"{sources.NCBI_ACCESSION}.gb"
    if not path.is_file():
        pytest.skip(f"run: {sources.FETCH['ncbi']}")
    text = path.read_text()
    assert "Blattella germanica" in text and "CYP6K1" in text
    lo, hi = sources.NCBI_CDS
    assert f"CDS             {lo}..{hi}" in text
    assert "2035 bp" in text


def test_every_fetched_structure_is_the_compound_its_inchikey_claims():
    """
    An InChIKey is a structural checksum, which is why fetch.sh verifies these
    rather than an MD5: PubChem stamps the fetch time into every SDF it serves.
    """
    if not sources.have("pubchem"):
        pytest.skip(f"run: {sources.FETCH['pubchem']}")
    for name, cid in sources.PUBCHEM_CIDS.items():
        text = (sources.RAW / f"pubchem-{cid}.sdf").read_text()
        assert sources.INCHIKEYS[name] in text, name


def test_health_says_in_one_call_which_reference_data_is_present():
    body = client.get("/api/health").json()
    assert set(body["reference_data"]) == {"pubchem", "ncbi", "uniprot"}
    assert all(isinstance(v, bool) for v in body["reference_data"].values())


def test_a_format_waiting_on_data_names_the_command_that_fixes_it(tmp_path, monkeypatch):
    """
    An error that does not say how to proceed just moves the problem to the
    reader. 409 rather than 404 (the route is right), 503 (nothing is
    temporarily down) or 501 (it is implemented): the server's state conflicts
    with the request and an operator action fixes it.
    """
    monkeypatch.setattr(sources, "RAW", tmp_path)
    gone = sources.missing("pubchem")
    assert len(gone) == 4, "an empty directory is missing every PubChem record"
    assert not sources.have("pubchem")

    exc = interop.DataUnavailable("ligands.sdf", gone, sources.FETCH["pubchem"],
                                  "PubChem structures are not committed.")
    detail = exc.as_detail()
    assert detail["error"] == "reference_data_not_fetched"
    assert detail["fetch"].startswith("./data/fetch.sh")
    assert detail["missing"]


# ---------------------------------------------------------------- ligands --
def test_the_ligand_table_joins_each_active_to_a_locus_that_exists():
    """
    `IMIDACLOPRID.resistance_locus` was "p450", which names no locus: the model
    has kdr, rdl, cyp6, est and gst. The field was never read, so nothing caught
    it until this table tried to join on it.
    """
    from blattella.genome import default_loci
    from blattella.toxicology import ACTIVES

    names = {loc.name for loc in default_loci()}
    for ins in ACTIVES.values():
        assert ins.resistance_locus in names, ins.name
    for r in rows(ligands.ligand_table("csv")):
        assert r["resistance_kind"] in ("target-site", "metabolic"), r


def test_the_ligand_table_carries_each_ld50_with_where_it_came_from():
    from blattella.toxicology import ACTIVES

    for r in rows(ligands.ligand_table("csv")):
        ins = ACTIVES[r["name"]]
        assert float(r["ld50_ug_per_insect"]) == pytest.approx(ins.ld50)
        if r["ld50_source"] == "literature":
            assert r["ld50_cite"]
        else:
            assert r["ld50_sweep_low"] and r["ld50_sweep_high"]


def test_the_ligand_table_still_works_with_nothing_fetched(tmp_path, monkeypatch):
    """
    The LD50s, targets and loci are this project's own and are the load-bearing
    part. Refusing to emit them because PubChem has not been fetched would be
    withholding the science over the metadata.
    """
    monkeypatch.setattr(sources, "RAW", tmp_path)
    data = rows(ligands.ligand_table("csv"))
    assert len(data) == 3
    for r in data:
        assert r["identifier_source"] == "not-fetched"
        assert r["pubchem_cid"] == "" and r["inchikey"] == ""
        assert float(r["ld50_ug_per_insect"]) > 0, "the science must survive"
    assert "NOT FETCHED" in header(ligands.ligand_table("csv"))


def test_an_sdf_cannot_degrade_because_a_file_with_no_structure_is_not_an_sdf(
        tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "RAW", tmp_path)
    with pytest.raises(interop.DataUnavailable) as e:
        ligands.ligand_sdf()
    assert e.value.fetch == "./data/fetch.sh pubchem"
    assert "ligands.csv still carries" in e.value.note


def test_pubchem_never_renames_this_projects_own_compounds():
    """
    CID 86418's PubChem title is not "Imidacloprid". The name column is always
    ours; PubChem's is kept in a separate column.
    """
    from blattella.toxicology import ACTIVES

    assert {r["name"] for r in rows(ligands.ligand_table("csv"))} == set(ACTIVES)
    assert "never" in header(ligands.ligand_table("csv"))


def test_the_sdf_annotates_every_structure_with_our_toxicology_and_our_caveat():
    if not sources.have("pubchem"):
        pytest.skip(f"run: {sources.FETCH['pubchem']}")
    text = ligands.ligand_sdf().decode()
    assert text.count("$$$$") == 3
    for field in ("blattella_name", "blattella_ld50_ug_per_insect",
                  "blattella_resistance_locus", "blattella_what_this_is_not"):
        assert text.count(f"> <{field}>") == 3
    assert text.count("2D only") == 3
    assert "no structure of the target" in text


# -------------------------------------------------------------------- FEP --
def fep():
    import json

    from blattella.interop.fep import fep_job_spec

    return json.loads(fep_job_spec())


def test_the_fep_request_refuses_to_name_a_structure_it_does_not_have():
    """A plausible-looking accession is worse than none: someone would use it."""
    d = fep()
    assert d["target"]["structure"] is None
    assert "no experimental structure" in d["target"]["structure_note"].lower()
    assert "homology model" in d["target"]["structure_note"]


def test_the_fep_request_warns_about_the_numbering_that_bit_this_project():
    d = fep()
    assert "L1014F" in d["mutation"]["numbering"]
    assert "Musca domestica" in d["mutation"]["numbering"]
    assert d["mutation"]["position"] == 993 and d["mutation"]["wild_type"] == "L"


def test_the_fep_request_says_its_reference_is_an_upper_bound():
    """
    The published ratio is whole-organism, so it carries metabolic resistance
    too. Handed over bare, a group would tune to a target that is too large.
    """
    crit = fep()["acceptance_criterion"]
    assert "upper bound" in crit["this_is_an_upper_bound"].lower()
    assert "metabolic" in crit["this_is_an_upper_bound"]
    assert "cypermethrin" in crit["ligand_substitution"]


def test_the_fep_request_states_the_sign_convention_explicitly():
    """A sign error in this convention invalidated a conclusion in phase 5."""
    calc = fep()["calculation"]
    assert "POSITIVE means" in calc["sign_convention"]
    assert "weakly" in calc["sign_convention"]


def test_the_fep_request_carries_the_measurement_it_asks_to_be_reproduced():
    from blattella.chem import reference_ddg

    ref = reference_ddg()
    crit = fep()["acceptance_criterion"]
    assert crit["reference_ddg_kcal_per_mol"] == pytest.approx(ref.value, abs=1e-4)
    assert crit["implied_resistance_ratio"] == pytest.approx(202.0, rel=1e-3)


def test_the_fep_request_admits_both_of_our_backends_fail():
    d = fep()
    backends = d["what_this_project_produced"]["backends"]
    assert {b["backend"] for b in backends} == {"classical-mm", "quantum-vqe"}
    assert any(b["sign_agrees_with_reference"] is False for b in backends)
    assert "fail against the reference" in d["what_this_project_produced"]["honest_summary"]
    assert "NOT a result" in d["_provenance"]["what_it_is_not"]


def test_the_fep_request_is_refused_for_a_mutation_or_ligand_we_do_not_model():
    from blattella.interop.fep import fep_job_spec

    with pytest.raises(KeyError, match="L1014F"):
        fep_job_spec(mutation="L1014F")
    with pytest.raises(KeyError, match="DDT"):
        fep_job_spec(ligand="DDT")
    assert client.get("/api/interop/fep_job.json?mutation=L1014F").status_code == 422


def test_the_chemistry_endpoints_serve_what_they_declare():
    for url, media in [("/api/interop/ligands.csv", "text/csv"),
                       ("/api/interop/ligands.json", "application/json"),
                       ("/api/interop/fep_job.json", "application/json")]:
        r = client.get(url)
        assert r.status_code == 200, url
        assert r.headers["content-type"].startswith(media), url


# --------------------------------------------------------------- sequence --
Bio = pytest.importorskip("Bio", reason="biopython is an optional dependency")


def needs_ncbi():
    if not sources.have("ncbi"):
        pytest.skip(f"run: {sources.FETCH['ncbi']}")


def test_the_target_window_lies_inside_the_coding_sequence():
    from blattella.interop.sequence import target_window

    lo, hi = target_window()
    cds_lo, cds_hi = sources.NCBI_CDS
    assert cds_lo <= lo < hi <= cds_hi
    assert hi - lo + 1 == 400


def test_a_window_that_will_not_work_is_refused_with_the_reason():
    from blattella.interop.sequence import target_window

    with pytest.raises(ValueError, match="19 bp"):
        target_window(length=10)
    with pytest.raises(ValueError, match="does not fit"):
        target_window(length=5000)
    with pytest.raises(ValueError, match="outside the CDS"):
        target_window(start=1)


def test_the_fasta_defline_carries_the_disclaimer_because_a_defline_survives_pasting():
    """
    A header comment is dropped the moment someone copies the sequence into
    another tool. The defline is the line that travels with it.
    """
    needs_ncbi()
    from blattella.interop.sequence import rnai_target_fasta

    defline = next(l for l in rnai_target_fasta().decode().splitlines()
                   if l.startswith(">"))
    assert "NO specificity analysis was performed" in defline
    assert "NOT a validated construct" in defline
    assert sources.NCBI_ACCESSION in defline
    assert "gene=CYP6K1" in defline


def test_the_fasta_sequence_is_the_window_the_defline_claims():
    needs_ncbi()
    from Bio import SeqIO

    from blattella.interop.sequence import rnai_target_fasta, target_window

    lo, hi = target_window()
    text = rnai_target_fasta().decode()
    seq = "".join(l for l in text.splitlines() if not l.startswith((">", ";")))
    record = SeqIO.read(sources.RAW / f"{sources.NCBI_ACCESSION}.gb", "genbank")
    assert seq == str(record.seq[lo - 1:hi])
    assert len(seq) == 400
    assert set(seq) <= set("ACGTacgt")


def test_the_genbank_construct_round_trips_with_its_features_intact():
    needs_ncbi()
    import io

    from Bio import SeqIO

    from blattella.interop.sequence import T7, dsrna_construct_genbank, target_window

    rec = SeqIO.read(io.StringIO(dsrna_construct_genbank().decode()), "genbank")
    lo, hi = target_window()
    assert len(rec.seq) == len(T7) * 2 + (hi - lo + 1)
    kinds = [f.type for f in rec.features]
    assert kinds == ["promoter", "misc_RNA", "promoter"]
    target = next(f for f in rec.features if f.type == "misc_RNA")
    assert target.qualifiers["gene"] == ["CYP6K1"]
    assert len(target.location) == 400


def test_the_genbank_comment_says_no_off_target_screen_was_performed():
    """
    A feature table reads as a designed construct. Nothing in this project
    analyses nucleic acid, and the record has to say so where it cannot be
    stripped.
    """
    needs_ncbi()
    import io

    from Bio import SeqIO

    from blattella.interop.sequence import dsrna_construct_genbank

    rec = SeqIO.read(io.StringIO(dsrna_construct_genbank().decode()), "genbank")
    # GenBank hard-wraps the COMMENT at about seventy characters, so every phrase
    # has to be matched against the unwrapped text rather than the raw field
    comment = " ".join(rec.annotations["comment"].split())
    assert "no BLAST" in comment and "off-target screen" in comment
    assert "NOT a validated construct" in comment
    assert "BLAST the target window against the" in comment
    assert "before synthesising anything" in comment
    assert "UNVALIDATED" in rec.description


def test_the_construct_puts_opposing_promoters_around_the_target():
    """Two opposing T7 promoters is how the double-stranded product is made."""
    needs_ncbi()
    from Bio.Seq import Seq

    from blattella.interop.sequence import T7, dsrna_construct_genbank

    text = dsrna_construct_genbank().decode()
    seq = "".join(text.split("ORIGIN")[1].split()).replace("//", "")
    seq = "".join(c for c in seq if c.isalpha()).upper()
    assert seq.startswith(T7)
    assert seq.endswith(str(Seq(T7).reverse_complement()))


def test_the_sequence_formats_refuse_to_guess_when_nothing_is_fetched(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "RAW", tmp_path)
    from blattella.interop.sequence import rnai_target_fasta

    with pytest.raises(interop.DataUnavailable) as e:
        rnai_target_fasta()
    assert e.value.fetch == "./data/fetch.sh ncbi"


def test_the_sequence_endpoints_serve_what_they_declare():
    needs_ncbi()
    for url, media in [("/api/interop/rnai_target.fasta", "text/x-fasta"),
                       ("/api/interop/dsrna_construct.gb", "chemical/seq-na-genbank")]:
        r = client.get(url)
        assert r.status_code == 200, url
        assert r.headers["content-type"].startswith(media)
    assert client.get("/api/interop/rnai_target.fasta?start=1").status_code == 422


# ------------------------------------------------------------------- OMEX --
import io as _io
import xml.etree.ElementTree as ET
import zipfile

OMEX_NS = "{http://identifiers.org/combine.specifications/omex-manifest}"
FIXED = "2026-09-12T00:00:00Z"


def archive(**kw):
    from blattella.interop.omex import combine_archive

    return zipfile.ZipFile(_io.BytesIO(combine_archive(generated=FIXED, **kw)))


def manifest_rows(z):
    root = ET.fromstring(z.read("manifest.xml"))
    return [(c.get("location"), c.get("format"), c.get("master")) for c in root]


def test_the_manifest_lists_every_file_in_the_archive_and_no_others():
    z = archive()
    listed = {loc.lstrip("./") for loc, _, _ in manifest_rows(z)} - {""}
    assert listed == set(z.namelist())


def test_the_archive_names_exactly_one_master_and_it_is_the_model():
    masters = [loc for loc, _, m in manifest_rows(archive()) if m == "true"]
    assert len(masters) == 1
    assert masters[0].endswith("toxicokinetics.xml")


def test_the_archive_is_byte_identical_across_two_builds():
    """
    Fixed entry timestamps and sorted order, so the file can be cited by
    checksum. The real generation time lives in the metadata.
    """
    from blattella.interop.omex import combine_archive

    assert combine_archive(generated=FIXED) == combine_archive(generated=FIXED)


def test_the_archive_cannot_contain_itself():
    names = archive().namelist()
    assert not any(n.endswith(".omex") for n in names)
    assert interop.FORMATS["bundle.omex"].in_bundle is False


def test_the_archive_says_it_is_not_a_re_executable_experiment():
    """
    A COMBINE archive usually carries SED-ML. There is none here and there will
    not be: what this project runs is a stochastic spatial agent-based model that
    SED-ML cannot describe.
    """
    z = archive()
    readme = z.read("README.md").decode()
    assert "NOT a re-executable simulation experiment" in readme
    assert "SED-ML" in readme
    assert not any(n.endswith(".sedml") for n in z.namelist())


def test_the_archive_readme_says_per_file_what_it_is_and_is_not():
    z = archive()
    readme = z.read("README.md").decode()
    for path in z.namelist():
        if path in ("README.md", "manifest.xml", "metadata.rdf"):
            continue
        assert f"`{path}`" in readme, path
    assert readme.count("**What it is not.**") == len(z.namelist()) - 3
    assert "pest-control and resistance-management" in readme


def test_the_archive_carries_the_whole_provenance_table():
    readme = archive().read("README.md").decode()
    assert "## Parameter provenance" in readme
    from blattella.params import registry

    for p in registry()[:5]:
        assert p.name in readme


def test_the_metadata_describes_every_file_with_its_negative():
    z = archive()
    root = ET.fromstring(z.read("metadata.rdf"))
    abouts = {d.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about") for d in root}
    for path in z.namelist():
        if path in ("README.md", "manifest.xml", "metadata.rdf"):
            continue
        assert f"./{path}" in abouts, path
    text = z.read("metadata.rdf").decode()
    assert text.count("<dcterms:abstract>") >= len(z.namelist()) - 3


def test_an_archive_built_without_a_colony_records_what_it_left_out():
    """A partial archive is useful; a silently partial one is not."""
    z = archive()
    readme = z.read("README.md").decode()
    assert "## What is missing, and why" in readme
    for fid in ("survival.csv", "contact_edges.csv", "contact_nodes.csv"):
        assert fid in readme
        assert not any(fid.replace(".csv", "") in n for n in z.namelist())


def test_an_archive_built_with_a_colony_contains_the_run_backed_tables(run):
    z = archive(run=run)
    names = " ".join(z.namelist())
    for want in ("survival", "contact_edges", "contact_nodes"):
        assert want in names
    readme = z.read("README.md").decode()
    for fid in ("survival.csv", "contact_edges.csv", "contact_nodes.csv"):
        assert f"`{fid}`: needs a simulated colony" not in readme
    # other optional inputs (e.g. unfetched reference files) may still be listed


def test_asking_for_an_unavailable_format_by_name_fails_loudly(tmp_path, monkeypatch):
    """
    An implicit "everything" degrades quietly but visibly; an explicit ask for
    something that cannot be produced must not come back silently empty.
    """
    from blattella.interop.omex import combine_archive

    monkeypatch.setattr(sources, "RAW", tmp_path)
    with pytest.raises(interop.DataUnavailable):
        combine_archive(include=["ligands.sdf"], generated=FIXED)
    # the same absence is merely recorded when nothing was named
    z = zipfile.ZipFile(_io.BytesIO(combine_archive(generated=FIXED)))
    assert "ligands.sdf" in z.read("README.md").decode()


def test_an_unknown_format_in_an_archive_request_is_refused():
    from blattella.interop.omex import combine_archive

    with pytest.raises(KeyError, match="nope"):
        combine_archive(include=["nope"], generated=FIXED)
    r = client.post("/api/interop/bundle.omex",
                    json={"colony": 20, "hours": 0.5, "include": ["nope"]})
    assert r.status_code == 422


def test_the_bundle_endpoints_serve_a_readable_archive():
    r = client.get("/api/interop/bundle.omex")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")
    assert 'filename="blattella_interop.omex"' in r.headers["content-disposition"]
    z = zipfile.ZipFile(_io.BytesIO(r.content))
    assert "manifest.xml" in z.namelist()

    p = client.post("/api/interop/bundle.omex",
                    json={"colony": 30, "hours": 1.0, "bait": "fipronil", "seed": 2})
    assert p.status_code == 200
    assert any("survival" in n for n in zipfile.ZipFile(_io.BytesIO(p.content)).namelist())
