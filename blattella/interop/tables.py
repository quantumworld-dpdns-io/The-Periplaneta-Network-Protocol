"""
Tables a statistician or a population geneticist can load without a converter.

Everything here is already computed by the model and, until now, thrown away:
the dose-response curve was a function with no way to sample it, the genetic map
existed only as Python objects, individual times of death were overwritten every
run, and the weighted contact edge list -- arguably the single most distinctive
thing this project produces -- never left `ContactNetwork.weights`.

Each table carries a commented provenance header and, where the rows differ in
how much they are trusted, a per-row `source` column. That matters most for the
genetic map: the karyotype is published, every map position in it is not.
"""
from __future__ import annotations

import csv
import io

import numpy as np

from .provenance import comment_block, stamp
from .spec import FormatSpec, register

CLI = "python -m blattella.cli interop --format {}"


def _csv(header: dict, columns: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    w.writerows(rows)
    return (comment_block(header) + buf.getvalue()).encode()


# ------------------------------------------------------------ dose-response --
def dose_response_csv(*, active: str | None = None, points: int = 97,
                      lo_ld50: float = 1e-3, hi_ld50: float = 1e3,
                      generated: str | None = None) -> bytes:
    """The probit curve, sampled. Log-spaced in multiples of the LD50."""
    from ..toxicology import ACTIVES, P, probit_mortality

    if active is not None and active not in ACTIVES:
        raise KeyError(f"unknown active {active!r}; have {sorted(ACTIVES)}")
    chosen = [ACTIVES[active]] if active else list(ACTIVES.values())
    slope = float(P["probit_slope"])
    mult = np.logspace(np.log10(lo_ld50), np.log10(hi_ld50), points)

    rows = []
    for ins in chosen:
        dose = mult * ins.ld50
        p = probit_mortality(dose, ins.ld50, slope)
        rows += [[ins.name, ins.iclass, f"{m:.6g}", f"{d:.6g}", ins.ld50,
                  f"{float(q):.6f}", slope] for m, d, q in zip(mult, dose, p)]

    head = stamp(
        "dose_response.csv",
        "A 24-hour probit dose-response curve per active ingredient, sampled over "
        "six decades of dose. The LD50 of each active is a published topical value "
        "for a susceptible strain; the probit slope is one assumed value shared by "
        "all three.",
        "NOT a fitted bioassay. No animals were dosed. The curve is the model's "
        "own assumption made explicit, so a reader can substitute a measured slope "
        "rather than inherit ours. It is also not what kills animals in the "
        "simulation: mortality there is a time-resolved hazard derived from this "
        "curve, because an agent-based model has to know when an animal dies, not "
        "only whether it does.",
        cli=CLI.format("dose_response.csv"),
        extra={"probit_slope": slope, "probit_slope_source": "assumption, swept 1.5 to 6.0"},
        generated=generated,
    )
    return _csv(head, ["active", "class", "dose_ld50_multiples", "dose_ug_per_insect",
                       "ld50_ug_per_insect", "mortality_fraction_24h", "probit_slope"], rows)


# -------------------------------------------------------------- genetic map --
def genetic_map_csv(*, linked: bool = False, generated: str | None = None) -> bytes:
    """
    The resistance loci and where the model puts them.

    Emitted as CSV with a per-row source rather than as a PED/MAP pair, because
    the linkage format implies a mapping study. There has not been one: the
    karyotype is published, and every centimorgan in this file is a placement we
    chose so that recombination has something to act on.
    """
    from ..genome import METABOLIC, N_CHROMOSOME_PAIRS, TARGET_SITE, default_loci, linked_loci

    loci = linked_loci() if linked else default_loci()
    rows = [[l.name, l.chromosome, N_CHROMOSOME_PAIRS, f"{l.position_cM:g}", l.kind,
             l.target or "", "|".join(l.actives),
             "literature" if False else "assumption", l.note]
            for l in loci]

    head = stamp(
        "genetic_map.csv",
        f"The {len(loci)} resistance loci the model carries, their kind "
        f"(target-site or metabolic), which actives each acts on, and their "
        f"position on a {N_CHROMOSOME_PAIRS}-pair karyotype.",
        "NOT a linkage map. Blattella germanica has no published one. The "
        "chromosome count is literature; every position in the position_cM column "
        "is an assumption, which is why the loci sit on separate chromosomes by "
        "default -- asserting linkage would be inventing data. Use `linked=true` "
        "for the variant that puts kdr and cyp6 5 cM apart as a sensitivity check.",
        cli=CLI.format("genetic_map.csv"),
        extra={"variant": "linked" if linked else "unlinked",
               "karyotype_source": "literature: Blattella germanica 2n = 42",
               "position_source": "assumption: no linkage map published for this species"},
        generated=generated,
    )
    assert {TARGET_SITE, METABOLIC} >= {l.kind for l in loci}
    return _csv(head, ["locus", "chromosome", "chromosome_pairs", "position_cM", "kind",
                       "target", "actives", "position_source", "note"], rows)


# ----------------------------------------------------------------- registry --
register(FormatSpec(
    id="parameters.csv", title="Parameter provenance table",
    spec="RFC 4180 CSV", spec_url="https://www.rfc-editor.org/rfc/rfc4180",
    media_type="text/csv", filename="blattella_parameters.csv",
    method="GET", path="/api/interop/parameters.csv",
    stage="provenance",
    consumers=("R", "pandas", "Excel", "any audit"),
    audience=("resistance-management", "computational-chemistry"),
    what_it_is="Every declared parameter with its source, citation and sweep range.",
    what_it_is_not="NOT a calibrated parameter set. Most rows are declared "
                   "assumptions; the sweep range is the honest statement of what "
                   "is unknown about each.",
    cli=CLI.format("parameters.csv"),
    render=lambda generated=None, **_: __import__(
        "blattella.interop.provenance", fromlist=["x"]).params_csv(generated=generated),
))

register(FormatSpec(
    id="dose_response.csv", title="Probit dose-response curves",
    spec="RFC 4180 CSV", spec_url="https://www.rfc-editor.org/rfc/rfc4180",
    media_type="text/csv", filename="blattella_dose_response.csv",
    method="GET", path="/api/interop/dose_response.csv",
    stage="toxicodynamics",
    consumers=("R (drc)", "GraphPad Prism", "pandas", "MATLAB"),
    audience=("resistance-management",),
    what_it_is="24-hour probit mortality against dose for each active ingredient.",
    what_it_is_not="NOT a fitted bioassay; no animals were dosed. The slope is an "
                   "assumption shared by all three actives.",
    cli=CLI.format("dose_response.csv"),
    render=dose_response_csv,
    caveats=("The probit slope is a single assumed value swept from 1.5 to 6.0.",),
))

register(FormatSpec(
    id="genetic_map.csv", title="Resistance loci and their map positions",
    spec="RFC 4180 CSV", spec_url="https://www.rfc-editor.org/rfc/rfc4180",
    media_type="text/csv", filename="blattella_genetic_map.csv",
    method="GET", path="/api/interop/genetic_map.csv",
    stage="population genetics",
    consumers=("R/qtl", "pandas", "any population-genetics pipeline"),
    audience=("resistance-management",),
    what_it_is="The modelled resistance loci, their kind, and their placement on a "
               "21-pair karyotype.",
    what_it_is_not="NOT a linkage map. No linkage map is published for this "
                   "species; every position is an assumption and the file says so "
                   "per row.",
    cli=CLI.format("genetic_map.csv"),
    render=genetic_map_csv,
))


# ------------------------------------------------------------------ survival --
def survival_csv(run, *, generated: str | None = None) -> bytes:
    """
    One row per animal: when it died, or that it was still alive when the run
    ended.

    The model records a time of death per individual and has always thrown it
    away, keeping only a mortality fraction. That discards the whole of the
    time-to-event structure, which is the form a toxicologist actually wants.

    Survivors are censored rows, not missing rows. Dropping them would turn a
    survival table into a table of deaths and inflate every hazard fitted to it.
    """
    import numpy as np

    c, tox, net = run.colony, run.tox, run.network
    n = c.n
    end_s = run.hours * 3600.0
    deaths = tox.deaths if tox is not None else np.full(n, np.nan)
    routes = tox.acquired_from if tox is not None else np.zeros((4, n))
    actives = [a.name for a in tox.actives] if tox is not None else []
    degree, strength = net.degree(), net.strength()
    start = c.t - end_s

    def dose(i: int) -> tuple[list[str], str]:
        """
        The dose that mattered: what it was carrying when it died, or what it is
        carrying now if it is still alive. A corpse is scavenged, so its burden
        at the end of the run is not the burden that killed it.
        """
        if tox is None:
            return [], "0"
        col = (tox.burden_at_death[:, i] if np.isfinite(deaths[i]) else tox.burden[:, i])
        ld50 = [a.ld50 for a in tox.actives]
        lf = float(np.nansum(col / np.asarray(ld50)))
        return [f"{v:.6g}" for v in col], f"{lf:.6g}"

    rows = []
    for i in range(n):
        died = bool(np.isfinite(deaths[i]))
        t = float(deaths[i] - start) if died else end_s
        burdens, lf = dose(i)
        rows.append([i, f"{t:.1f}", f"{t / 3600.0:.4f}", int(died),
                     "" if died else f"{end_s:.1f}",
                     *burdens, lf,
                     *[f"{routes[k, i]:.6g}" for k in range(4)],
                     int(degree[i]), f"{strength[i]:.1f}", int(c.emergences[i])])

    head = stamp(
        "survival.csv",
        "One row per individual: time to death in seconds and hours, an event "
        "indicator, the internal dose at the moment that mattered (at death for "
        "an animal that died, at the end of the run for one that survived), how "
        "much insecticide it acquired by each of the four routes, and its position "
        "in the contact network. Ready for a Surv() object or a lifelines fitter.",
        "NOT a bioassay, and NOT real censoring. No animals were dosed. Times of "
        "death come from a hazard that is the exact inversion of an assumed probit "
        "with an assumed floor on time-to-death, so a Cox model fitted to this "
        "recovers those assumptions and nothing else. The only censoring is "
        "administrative: the run ended. There is no loss to follow-up and no "
        "competing risk.",
        cli=run.reproduce,
        extra={"run": run.config, "observation_window_s": end_s,
               "event_definition": "death from insecticide",
               "censoring": "administrative only, at the end of the run",
               "dose_columns": "measured at death for event rows and at the end of "
                               "the run for censored rows. A corpse loses its burden "
                               "to scavengers, so its final burden is near zero and "
                               "would badly understate the dose that killed it."},
        generated=generated,
    )
    cols = (["id", "time_to_event_s", "time_to_event_h", "event", "censored_at_s"]
            + [f"burden_{a}_ug_at_event" for a in actives]
            + ["lethal_fraction_at_event", "route_bait_ug", "route_contact_ug",
               "route_faeces_ug", "route_corpse_ug",
               "degree", "contact_seconds", "foraging_trips"])
    return _csv(head, cols, rows)


# ------------------------------------------------------------ contact network --
def contact_edges_csv(run, *, generated: str | None = None) -> bytes:
    """
    The weighted contact edge list.

    This is the most distinctive thing the project produces and the thing it has
    been least able to hand over: an edge here exists because two animals wanted
    the same refuge or the same food, not because an edge was prescribed. Until
    now `ContactNetwork.weights` never left the process.
    """
    net = run.network
    rows = [[i, j, f"{w:.1f}", f"{w / net.total_time:.6g}" if net.total_time else ""]
            for (i, j), w in sorted(net.weights.items())]
    head = stamp(
        "contact_edges.csv",
        f"Undirected weighted contact network of {net.n} animals over "
        f"{run.hours:g} simulated hours: {len(rows)} edges, weighted by seconds "
        f"spent within contact range. The network emerges from where the animals "
        f"chose to go; nothing prescribes who touches whom.",
        "NOT an observed network. No animals were tracked. It is also not a "
        "transmission network on its own: insecticide moves along these edges in "
        "the simulation, but the per-second transfer efficiency that governs how "
        "much moves is a declared assumption, not a measurement.",
        cli=run.reproduce,
        extra={"run": run.config, "nodes": net.n, "edges": len(rows),
               "observed_seconds": net.total_time,
               "weight_units": "seconds within contact range"},
        generated=generated,
    )
    return _csv(head, ["source", "target", "contact_seconds", "fraction_of_observation"], rows)


def contact_nodes_csv(run, *, generated: str | None = None) -> bytes:
    """Per-animal covariates, so the edge list can be joined to something."""
    import numpy as np

    c, net = run.colony, run.network
    degree, strength = net.degree(), net.strength()
    lethal = run.tox.lethal_fraction() if run.tox is not None else np.zeros(c.n)
    # a dead animal's remaining burden has been scavenged away; survival.csv has
    # the dose at death, this column is deliberately the current one
    comp = {i: k for k, members in enumerate(net.components()) for i in members}
    rows = [[i, int(degree[i]), f"{strength[i]:.1f}", int(c.home[i]),
             int(c.emergences[i]), int(bool(c.alive[i])),
             f"{float(lethal[i]):.6g}", comp.get(i, -1)]
            for i in range(c.n)]
    head = stamp(
        "contact_nodes.csv",
        "Per-animal covariates for the contact network: number of distinct "
        "partners, total contact seconds, home harborage, foraging trips, whether "
        "it survived, its internal dose in LD50 units, and which connected "
        "component it belongs to.",
        "NOT observations of real animals. Every column is model state. The home "
        "harborage is an index into a generated arena, not a place. The "
        "lethal_fraction column is the CURRENT dose, so for a dead animal it is "
        "near zero because scavengers took the burden; survival.csv carries the "
        "dose at death.",
        cli=run.reproduce,
        extra={"run": run.config},
        generated=generated,
    )
    return _csv(head, ["id", "degree", "contact_seconds", "home_harborage",
                       "foraging_trips", "alive", "lethal_fraction_now", "component"], rows)


for _id, _title, _fn, _what, _not, _consumers in [
    ("survival.csv", "Time-to-event table", survival_csv,
     "One row per animal with its time of death or censoring, internal dose, "
     "acquisition route and network position.",
     "NOT a bioassay and not real censoring: the times come from an assumed "
     "probit, and the only censoring is the end of the run.",
     ("R (survival, survminer)", "lifelines", "SAS", "pandas")),
    ("contact_edges.csv", "Weighted contact edge list", contact_edges_csv,
     "Undirected weighted contact network, in seconds within contact range, that "
     "emerged from the animals' own movement.",
     "NOT an observed network, and not a transmission network by itself: the "
     "transfer efficiency along an edge is an assumption.",
     ("igraph", "NetworkX", "Gephi", "Cytoscape")),
    ("contact_nodes.csv", "Contact network node table", contact_nodes_csv,
     "Per-animal covariates to join onto the edge list.",
     "NOT observations of real animals; every column is model state.",
     ("igraph", "NetworkX", "Gephi", "pandas")),
]:
    register(FormatSpec(
        id=_id, title=_title, spec="RFC 4180 CSV",
        spec_url="https://www.rfc-editor.org/rfc/rfc4180",
        media_type="text/csv", filename=f"blattella_{_id.replace('.csv', '')}.csv",
        # POST: serving this means running a colony, and the cost scales with the body
        method="POST", path=f"/api/interop/{_id}",
        stage="colony / toxicology",
        consumers=_consumers, audience=("resistance-management",),
        what_it_is=_what, what_it_is_not=_not,
        cli="python -m blattella.cli interop --format " + _id,
        render=_fn, needs_run=True,
    ))
