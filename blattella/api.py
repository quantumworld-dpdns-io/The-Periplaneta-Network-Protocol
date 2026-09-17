"""
HTTP API over the model, so the dashboard can ask questions instead of only
displaying a snapshot.

    uvicorn blattella.api:app --reload --port 8000
    just api

Every endpoint runs the same code the command line runs, so a number seen in the
browser can be reproduced from a terminal. Each response therefore carries the
CLI invocation that would reproduce it, in `reproduce`, and the caveats that
apply to it. A dashboard that shows a figure without saying how it was made and
what it is worth is the thing this project keeps removing.

Runs are bounded (see `LIMITS`) because these are real simulations, not lookups:
a strategy comparison with many seeds and generations takes real seconds.
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
from typing import Literal

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from . import experiment as ex
from . import interop
from .behaviour import make_colony, simulate
from .chem import MUTATIONS, TARGETS
from .chem.classical import ClassicalBackend
from .chem.compare import compare as chem_compare, report as chem_report
from .chem.quantum import QuantumBackend
from .contact import ContactRecorder
from .export import build as build_export
from .genome import Genome, default_loci, linked_loci
from .live import GRID, LAYERS, MAX_SESSIONS, REGISTRY
from .params import Source, assumptions, provenance_report, registry
from .phenotype import predictions as neural_predictions, report as neural_report
from .population import Deployment, ExposureProfile, Population
from .strategy import ACTIVE_SET, mixture, rotation, single, standard_arms, untreated
from .toxicology import ACTIVES, Toxicology

MAX_STREAM_FRAMES = 20_000        # a ceiling, not a normal exit

LIMITS = {
    "seeds": 24,
    "generations": 120,
    "colony": 400,
    "hours": 24.0,
    "population": 1200,
}

DESCRIPTION = """
Every endpoint runs the real model. Nothing here is precomputed, and every
response carries the command line that reproduces it plus the caveats that apply
to it.

**What the ground truth is.** The colony, its contacts, the poisoning and the
hundred generations of selection are all simulated. The empirical inputs are the
LD50s, the published resistance ratio behind the reference binding free energy,
and the spike-train statistics; everything else is a stated assumption with a
sensitivity range. `GET /api/parameters` separates the two, and
`GET /api/interop/formats` says per output format what it does and does not claim.

**This is a pest-control model, not a therapeutics model.** It maximises
mortality in the organism it describes. There is no human pharmacokinetics here
and no clinical pharmacodynamics.
"""

TAGS = [
    {"name": "meta", "description": "What the server can do, and where its numbers come from."},
    {"name": "evolution", "description": "Resistance evolution over generations, and the "
                                         "rotation-versus-mixture comparison the project was built to answer."},
    {"name": "colony", "description": "One colony simulated at one-second resolution: movement, "
                                      "harborages, the contact network that emerges from it, and poisoning."},
    {"name": "chemistry", "description": "Binding free energy for a resistance mutation. Every backend "
                                         "is reported against the same literature-derived reference."},
    {"name": "neural", "description": "Predicted spike-train signatures of poisoning. Cross-species "
                                      "extrapolation; the caveat travels with every response."},
    {"name": "live", "description": "A colony running in real time that you can interfere with while "
                                    "it runs."},
    {"name": "export", "description": "Reports for people to read: CSV tables and Markdown."},
    {"name": "interop", "description": "Files for other software to read. Standard formats for the "
                                       "computational-biology toolchain, each stating in its own "
                                       "metadata what it is and what it is not."},
]

app = FastAPI(
    title="The Periplaneta Protocol",
    version=__version__,
    summary="Insecticide resistance evolution in an interacting Blattella germanica colony",
    description=DESCRIPTION,
    openapi_tags=TAGS,
    license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
    # `servers` is deliberately not hard-coded: a fixed absolute URL breaks
    # relative paths behind a reverse proxy. Set BLATTELLA_PUBLIC_URL to
    # advertise one, otherwise FastAPI's root_path handling does the right thing.
    servers=([{"url": os.environ["BLATTELLA_PUBLIC_URL"].rstrip("/")}]
             if os.environ.get("BLATTELLA_PUBLIC_URL") else None),
)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
    # Without this a cross-origin browser fetch cannot read the filename we set,
    # so the dashboard's download helper silently falls back to guessing it from
    # the URL. The header is useless unless it is exposed.
    expose_headers=["Content-Disposition", "X-Blattella-Format"],
)

_CAVEATS = {
    "simulated": "Ground truth is simulated. The empirical inputs are the LD50s, the "
                 "resistance ratio, the karyotype, the life history and the fitted "
                 "spike statistics.",
    "fitness_cost": "The fitness cost of resistance decides this result and has no "
                    "published value for these alleles.",
    "one_profile": "One exposure profile. Metabolic resistance is expected to matter "
                   "under residual sprays and not under baits; that is untested.",
    "backends": "Neither binding-energy backend is fit to drive the science, and both "
                "say so. The population layer uses the measured value.",
    "extrapolation": "The single-unit statistics are fitted to Periplaneta americana "
                     "and locust recordings and used as a prior for Blattella "
                     "germanica. That is extrapolation, not measurement.",
    "static_network": "The contact network is held static within a generation.",
}


def _clamp(name: str, value, lo=1):
    cap = LIMITS[name]
    if value < lo:
        raise HTTPException(422, f"{name} must be at least {lo}")
    if value > cap:
        raise HTTPException(422, f"{name} is capped at {cap} so a request cannot hang the server")
    return value


def _profile(exposed: float, dose: float, sd: float) -> ExposureProfile:
    if not 0.0 <= exposed <= 1.0:
        raise HTTPException(422, "exposed must be between 0 and 1")
    if dose < 0:
        raise HTTPException(422, "dose must not be negative")
    return ExposureProfile(exposed_fraction=exposed, dose_ld50_median=dose, dose_ld50_log_sd=sd)


# ------------------------------------------------------------------- schemas --
class EvolveRequest(BaseModel):
    strategy: Literal["single", "rotation", "rotation3", "mixture", "untreated"] = "rotation"
    generations: int = Field(40, ge=1)
    population: int = Field(400, ge=10)
    exposed: float = Field(0.6, ge=0.0, le=1.0)
    dose_ld50: float = Field(50.0, ge=0.0)
    dose_log_sd: float = Field(1.0, ge=0.0, le=4.0)
    founder_frequency: float = Field(0.05, ge=0.0, le=1.0,
                                     description="starting frequency of every resistance "
                                                 "allele; raise it to model inheriting an "
                                                 "already-resistant infestation")
    fitness_cost: float | None = Field(None, ge=0.0, le=0.9,
                                       description="override the target-site fitness cost")
    linked: bool = False
    seed: int = 1


class CompareRequest(BaseModel):
    seeds: int = Field(8, ge=1)
    generations: int = Field(40, ge=1)
    population: int = Field(400, ge=10)
    exposed: float = Field(0.6, ge=0.0, le=1.0)
    dose_ld50: float = Field(50.0, ge=0.0)
    linked: bool = False


class ContactRequest(BaseModel):
    colony: int = Field(150, ge=10)
    hours: float = Field(4.0, gt=0)
    harborages: int = Field(6, ge=1, le=20)
    resources: int = Field(3, ge=1, le=10)
    arena_cm: float = Field(300.0, ge=50, le=1000)
    bait: str | None = Field(None, description="active to place at station 0, or null")
    stations: int = Field(1, ge=0, le=10)
    seed: int = 1


class LiveStartRequest(BaseModel):
    colony: int = Field(180, ge=10, le=400)
    harborages: int = Field(6, ge=1, le=20)
    resources: int = Field(3, ge=1, le=10)
    arena_cm: float = Field(300.0, ge=50, le=1000)
    speed: int = Field(30, ge=1, le=300, description="simulated seconds per frame")
    seed: int = 1


class LiveActionRequest(BaseModel):
    action: Literal["pause", "speed", "light", "bait", "clear_bait", "toggle_station",
                    "add", "remove", "aggregation", "concentration"]
    params: dict = Field(default_factory=dict)


class ChemRequest(BaseModel):
    mutation: str = "L993F"
    ligand: str = "deltamethrin"
    use_vqe: bool = False


class NeuralRequest(BaseModel):
    burden_ld50: float = Field(0.5, ge=0.0, le=100.0)
    hours: list[float] = Field(default_factory=lambda: [0.5, 2.0, 8.0, 24.0])
    seed: int = 0


def _strategy(name: str):
    from .strategy import arm

    try:
        return arm(name)
    except KeyError as e:
        raise HTTPException(422, str(e)) from e


# -------------------------------------------------------------------- routes --
@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "actives": sorted(ACTIVES), "limits": LIMITS,
            "live_sessions": REGISTRY.count(),
            # one call tells a client which formats can be served right now
            "reference_data": interop.sources.status()["fetched"],
            "max_live_sessions": MAX_SESSIONS}


@app.get("/api/model", tags=["meta"], summary="The full snapshot, as the CLI exports it")
def model(seeds: int = Query(8, ge=1), generations: int = Query(40, ge=1)) -> dict:
    _clamp("seeds", seeds)
    _clamp("generations", generations)
    return build_export(seeds=seeds, generations=generations, contact_hours=4.0, colony_n=150)


@app.get("/api/strategies", tags=["meta"])
def strategies() -> list[dict]:
    from .strategy import ARMS, STANDARD, arm

    # driven by the registry, so an arm cannot exist in the model and be missing
    # here; zip against a hard-coded tuple used to drop anything extra in silence
    return [
        {"id": i, "name": (s := arm(i)).name, "description": s.description,
         "dose_share": s.dose_share, "period": s.period,
         "in_headline_comparison": i in STANDARD}
        for i in ARMS
    ]


@app.get("/api/parameters", tags=["meta"])
def parameters() -> dict:
    return {
        "literature": [{"name": p.name, "value": p.value, "unit": p.unit, "cite": p.cite}
                       for p in registry() if p.source is Source.LITERATURE and p.name],
        "assumptions": [{"name": p.name, "value": p.value, "unit": p.unit,
                         "sweep": list(p.sweep), "note": p.note}
                        for p in assumptions() if p.name],
        "counts": {s: sum(1 for p in registry() if p.source is s and p.name)
                   for s in (Source.LITERATURE, Source.COMPUTED, Source.DERIVED,
                             Source.ASSUMPTION)} | {},
    }


@app.post("/api/evolve", tags=["evolution"], summary="One resistance-evolution run")
def evolve(req: EvolveRequest) -> dict:
    _clamp("generations", req.generations)
    _clamp("population", req.population, lo=10)
    from . import genome as gn

    st = _strategy(req.strategy)
    genome = Genome(loci=linked_loci() if req.linked else default_loci())
    original = gn.P["fitness_cost_target_site"]
    if req.fitness_cost is not None:
        gn.P["fitness_cost_target_site"] = req.fitness_cost
    try:
        t0 = time.perf_counter()
        pop = Population(genome=genome, n=req.population,
                         rng=np.random.default_rng(req.seed),
                         founder_frequency=req.founder_frequency)
        prof = _profile(req.exposed, req.dose_ld50, req.dose_log_sd)
        pop.run(Deployment.from_strategy(st), req.generations,
                {a: prof for a in ACTIVE_SET})
        elapsed = time.perf_counter() - t0
    finally:
        gn.P["fitness_cost_target_site"] = original

    return {
        "strategy": {"id": req.strategy, "name": st.name, "description": st.description},
        "history": pop.history,
        "final": {k: round(v, 4) for k, v in pop.allele_frequencies().items()},
        "generations_run": pop.generation,
        "survivors": int(pop.hap.shape[0]),
        "elapsed_s": round(elapsed, 3),
        "reproduce": f"python -m blattella.cli evolve --strategy "
                     f"{ {'rotation3': 'rotation'}.get(req.strategy, req.strategy) } "
                     f"--generations {req.generations} --n {req.population} "
                     f"--exposed {req.exposed} --dose {req.dose_ld50} --seed {req.seed}",
        "caveats": [_CAVEATS["simulated"], _CAVEATS["fitness_cost"], _CAVEATS["static_network"]],
    }


@app.post("/api/compare", tags=["evolution"], summary="Rotation vs mixture vs single product")
def compare(req: CompareRequest) -> dict:
    _clamp("seeds", req.seeds)
    _clamp("generations", req.generations)
    _clamp("population", req.population, lo=10)
    t0 = time.perf_counter()
    comp = ex.compare_strategies(
        seeds=req.seeds, generations=req.generations, n=req.population,
        profile=_profile(req.exposed, req.dose_ld50, 1.0), linked=req.linked,
        log=lambda *_: None)
    return {
        "config": {"seeds": req.seeds, "generations": req.generations,
                   "population": req.population, "exposed": req.exposed,
                   "dose_ld50": req.dose_ld50, "linked": req.linked},
        "summary": comp.summary(),
        "elapsed_s": round(time.perf_counter() - t0, 3),
        "reproduce": f"python -m blattella.cli compare --seeds {req.seeds} "
                     f"--generations {req.generations} --n {req.population} "
                     f"--exposed {req.exposed} --dose {req.dose_ld50}"
                     + (" --linked" if req.linked else ""),
        "caveats": [_CAVEATS["simulated"], _CAVEATS["fitness_cost"], _CAVEATS["one_profile"]],
    }


@app.post("/api/contact", tags=["colony"], summary="Simulate a colony and its contact network")
def contact(req: ContactRequest) -> dict:
    from .arena import default_arena

    _clamp("colony", req.colony, lo=10)
    _clamp("hours", req.hours, lo=0.1)
    if req.bait is not None and req.bait not in ACTIVES:
        raise HTTPException(422, f"unknown active {req.bait!r}; have {sorted(ACTIVES)}")

    t0 = time.perf_counter()
    arena = default_arena(n_harborages=req.harborages, n_resources=req.resources,
                          width=req.arena_cm, height=req.arena_cm, seed=req.seed)
    colony = make_colony(n=req.colony, seed=req.seed, arena=arena)
    colony.t = 12 * 3600.0
    rec = ContactRecorder()
    observers = []
    tox = None
    if req.bait and req.stations:
        tox = Toxicology(colony=colony, actives=tuple(ACTIVES.values()), seed=req.seed)
        tox.treat_resource(req.bait, list(range(min(req.stations, req.resources))))
        observers.append(tox)
    simulate(colony, req.hours * 3600.0, 1.0, recorder=rec, observers=observers)
    net = rec.network()

    out = {
        "config": req.model_dump(),
        "network": net.summary(),
        "harborages": [{"x": s.x, "y": s.y, "r": s.r} for s in arena.harborages],
        "resources": [{"x": s.x, "y": s.y, "r": s.r} for s in arena.resources],
        "treated": sorted(tox.treated_resources.get(req.bait, set())) if tox else [],
        "positions": colony.positions().round(1).tolist(),
        "degree": net.degree().tolist(),
        "alive": colony.alive.tolist(),
        "trips": colony.emergences.tolist(),
        "states": colony.state_counts(),
        "elapsed_s": round(time.perf_counter() - t0, 3),
        "reproduce": f"python -m blattella.cli "
                     + (f"bait --bait {req.bait} --stations {req.stations} "
                        if req.bait else "contact ")
                     + f"--n {req.colony} --hours {req.hours} --seed {req.seed}",
        "caveats": [_CAVEATS["simulated"]],
    }
    if tox:
        out["toxicology"] = tox.summary()
    return out


def _chem_result(req: ChemRequest) -> dict:
    """Validate, then run both backends. Shared by the JSON route and the export."""
    if req.mutation not in MUTATIONS:
        raise HTTPException(422, f"unknown mutation {req.mutation!r}; have {sorted(MUTATIONS)}")
    if req.ligand not in ACTIVES:
        raise HTTPException(422, f"unknown ligand {req.ligand!r}; have {sorted(ACTIVES)}")
    return chem_compare([ClassicalBackend(), QuantumBackend(use_vqe=req.use_vqe)],
                        mutation=MUTATIONS[req.mutation], ligand=req.ligand)


@app.post("/api/chem", tags=["chemistry"], summary="Binding free energy, every backend")
def chem(req: ChemRequest) -> dict:
    t0 = time.perf_counter()
    out = _chem_result(req)
    out["elapsed_s"] = round(time.perf_counter() - t0, 3)
    out["reproduce"] = (f"python -m blattella.cli chem --mutation {req.mutation} "
                        f"--ligand {req.ligand}" + ("" if req.use_vqe else " --exact"))
    out["caveats"] = [_CAVEATS["backends"]]
    return out


@app.post("/api/neural", tags=["neural"], summary="Predicted spike-train signatures")
def neural(req: NeuralRequest) -> dict:
    if not req.hours:
        raise HTTPException(422, "give at least one time point")
    if len(req.hours) > 12:
        raise HTTPException(422, "at most 12 time points")
    out = neural_predictions(burden_ld50=req.burden_ld50, hours=tuple(req.hours), seed=req.seed)
    out["reproduce"] = f"python -m blattella.cli neural --burden {req.burden_ld50}"
    out["caveats"] = [_CAVEATS["extrapolation"], _CAVEATS["simulated"]]
    return out


# ---------------------------------------------------------------------- live --
@app.post("/api/live", tags=["live"], summary="Start a colony you can watch and interfere with")
def live_start(req: LiveStartRequest) -> dict:
    s = REGISTRY.create(colony_n=req.colony, harborages=req.harborages,
                        resources=req.resources, arena_cm=req.arena_cm,
                        seed=req.seed, speed=req.speed)
    return {
        "session": s.id,
        "grid": GRID,
        "layers": list(LAYERS),
        "arena_cm": req.arena_cm,
        "harborages": [{"x": h.x, "y": h.y, "r": h.r} for h in s.colony.arena.harborages],
        "resources": [{"x": r.x, "y": r.y, "r": r.r} for r in s.colony.arena.resources],
        "actives": sorted(ACTIVES),
        "sessions_open": REGISTRY.count(),
        "frame": s.frame(),
        "reproduce": "python -m blattella.cli contact "
                     f"--n {req.colony} --harborages {req.harborages} --seed {req.seed}",
        "caveats": [_CAVEATS["simulated"]],
    }


@app.get("/api/live/{session}/stream", tags=["live"],
         summary="Server-sent frames while the colony runs")
def live_stream(session: str, fps: float = Query(4.0, ge=0.5, le=10.0)):
    s = REGISTRY.get(session)
    if s is None:
        raise HTTPException(404, "no such session; start one with POST /api/live")

    def frames():
        # Starlette stops pulling when the browser goes away, which closes this
        # generator. The frame ceiling is a second line of defence so a lost
        # connection cannot leave a simulation running for the life of the process.
        period = 1.0 / fps
        sent = 0
        while sent < MAX_STREAM_FRAMES and REGISTRY.get(session) is not None:
            started = time.perf_counter()
            s.advance()
            yield f"data: {json.dumps(s.frame())}\n\n"
            sent += 1
            slack = period - (time.perf_counter() - started)
            if slack > 0:
                time.sleep(slack)
        yield 'data: {"end": true}\n\n'

    return StreamingResponse(frames(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/live/{session}", tags=["live"], summary="One frame, without streaming")
def live_frame(session: str) -> dict:
    s = REGISTRY.get(session)
    if s is None:
        raise HTTPException(404, "no such session")
    return s.frame()


@app.post("/api/live/{session}/act", tags=["live"], summary="Do something to the colony")
def live_act(session: str, req: LiveActionRequest) -> dict:
    s = REGISTRY.get(session)
    if s is None:
        raise HTTPException(404, "no such session")
    try:
        out = s.act(req.action, req.params)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {**out, "frame": s.frame()}


@app.delete("/api/live/{session}", tags=["live"])
def live_stop(session: str) -> dict:
    return {"stopped": REGISTRY.drop(session)}


# -------------------------------------------------------------------- export --
def _csv(rows: list[dict]) -> str:
    """
    Rows to CSV, keeping every column any row has.

    Taking the field names from `rows[0]` alone silently drops columns that only
    later rows carry, and the evolution history is exactly that shape: `extinct`
    appears once a run dies out. Order is first-seen, so the common columns stay
    where a reader expects them.
    """
    if not rows:
        return ""
    fields: dict[str, None] = {}
    for r in rows:
        fields.update(dict.fromkeys(r))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(fields), restval="")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _download(body: str | bytes, *, filename: str, media_type: str,
              fmt: str | None = None) -> Response:
    """
    One place that knows how a download is shaped.

    `Content-Disposition` is only readable by a cross-origin browser because the
    CORS middleware exposes it; see the `expose_headers` note above.
    """
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if fmt:
        headers["X-Blattella-Format"] = fmt
    return Response(body, media_type=media_type, headers=headers)


def _binary(media_type: str, *, needs_data: bool = False) -> dict:
    """
    Declare in OpenAPI what a route actually returns.

    Every export route used to claim `text/plain` through `response_class` while
    returning CSV or Markdown on the wire, so a generated client parsed the wrong
    thing.
    """
    out: dict = {
        200: {"content": {media_type: {"schema": {"type": "string", "format": "binary"}}}},
        422: {"description": "unknown identifier, or a run larger than the limits allow"},
    }
    if needs_data:
        out[409] = {"description": "reference data has not been fetched; the body names the command"}
    return out


@app.post("/api/export/compare.csv", tags=["export"], responses=_binary("text/csv"))
def export_compare_csv(req: CompareRequest) -> Response:
    return _download(_csv(compare(req)["summary"]), filename="strategy_summary.csv",
                     media_type="text/csv", fmt="compare.csv")


@app.post("/api/export/evolve.csv", tags=["export"], responses=_binary("text/csv"))
def export_evolve_csv(req: EvolveRequest) -> Response:
    return _download(_csv(evolve(req)["history"]), filename="evolution.csv",
                     media_type="text/csv", fmt="evolve.csv")


@app.get("/api/export/parameters.md", tags=["export"], responses=_binary("text/markdown"))
def export_parameters_md() -> Response:
    return _download(provenance_report(), filename="PARAMETERS.md",
                     media_type="text/markdown", fmt="parameters.md")


@app.post("/api/export/chem.md", tags=["export"], responses=_binary("text/markdown"))
def export_chem_md(req: ChemRequest) -> Response:
    # the same guards `/api/chem` applies: without them an unknown mutation is a
    # KeyError and a 500, which tells the caller nothing
    return _download(chem_report(_chem_result(req)), filename="DDG_COMPARISON.md",
                     media_type="text/markdown", fmt="chem.md")


@app.post("/api/export/neural.md", tags=["export"], responses=_binary("text/markdown"))
def export_neural_md(req: NeuralRequest) -> Response:
    pred = neural_predictions(burden_ld50=req.burden_ld50, hours=tuple(req.hours), seed=req.seed)
    return _download(neural_report(pred), filename="NEURAL_PREDICTIONS.md",
                     media_type="text/markdown", fmt="neural.md")


# ------------------------------------------------------------------ interop --
# Files for other software to read, as opposed to the `export` tag's reports for
# people. Each format declares in its own metadata what it is and what it is not;
# see `blattella.interop.spec`.
#
# GET when the bytes are a pure function of the committed code plus fetched
# reference data, so the URL can be cited and cached. POST when serving it means
# running a simulation whose cost scales with the request.


@app.exception_handler(interop.DataUnavailable)
def _data_unavailable(_request, exc: interop.DataUnavailable):
    # 409 rather than 404 (the route is correct), 503 (nothing is temporarily
    # down) or 501 (it is implemented): the server's state conflicts with the
    # request and an operator action fixes it.
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=409, content={"detail": exc.as_detail()})


@app.exception_handler(interop.FormatUnsupported)
def _format_unsupported(_request, exc: interop.FormatUnsupported):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=501, content={"detail": {
        "error": "format_unsupported", "format": exc.format_id,
        "missing_library": exc.library,
        "note": "This format needs an optional dependency that is not installed. "
                "Every other format still works."}})


@app.get("/api/interop/formats", tags=["interop"],
         summary="What this server can hand to other software, and what each file claims")
def interop_formats() -> dict:
    return interop.discovery()


def _interop(format_id: str, **params) -> Response:
    art = interop.render(format_id, **params)
    return _download(art.data, filename=art.filename, media_type=art.media_type, fmt=art.id)


@app.get("/api/interop/parameters.csv", tags=["interop"], responses=_binary("text/csv"),
         summary="Every parameter with its source, citation and sweep range")
def interop_parameters_csv() -> Response:
    return _interop("parameters.csv")


@app.get("/api/interop/dose_response.csv", tags=["interop"], responses=_binary("text/csv"),
         summary="Probit dose-response curves, sampled")
def interop_dose_response_csv(
    active: str | None = Query(None, description="one active, or omit for all three"),
    points: int = Query(97, ge=2, le=1000),
    lo: float = Query(1e-3, gt=0, description="lowest dose, in multiples of the LD50"),
    hi: float = Query(1e3, gt=0, description="highest dose, in multiples of the LD50"),
) -> Response:
    if hi <= lo:
        raise HTTPException(422, "hi must exceed lo")
    try:
        return _interop("dose_response.csv", active=active, points=points,
                        lo_ld50=lo, hi_ld50=hi)
    except KeyError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/api/interop/genetic_map.csv", tags=["interop"], responses=_binary("text/csv"),
         summary="The resistance loci and where the model puts them")
def interop_genetic_map_csv(
    linked: bool = Query(False, description="the sensitivity variant: kdr and cyp6 5 cM apart"),
) -> Response:
    return _interop("genetic_map.csv", linked=linked)


@app.get("/api/interop/toxicokinetics.xml", tags=["interop"],
         responses=_binary("application/sbml+xml"),
         summary="The toxicokinetic core as SBML, for COPASI or SimBiology")
def interop_toxicokinetics_xml(
    actives: list[str] | None = Query(None, description="restrict to these actives"),
) -> Response:
    try:
        return _interop("toxicokinetics.xml", actives=tuple(actives) if actives else None)
    except KeyError as e:
        raise HTTPException(422, str(e)) from e


def _run_from(req: ContactRequest):
    from .interop.runs import colony_run

    _clamp("colony", req.colony, lo=10)
    _clamp("hours", req.hours, lo=0.1)
    try:
        return colony_run(colony=req.colony, hours=req.hours, harborages=req.harborages,
                          resources=req.resources, arena_cm=req.arena_cm,
                          bait=req.bait, stations=req.stations, seed=req.seed)
    except KeyError as e:
        raise HTTPException(422, str(e)) from e


@app.post("/api/interop/survival.csv", tags=["interop"], responses=_binary("text/csv"),
          summary="Time-to-event table for a treated colony")
def interop_survival_csv(req: ContactRequest) -> Response:
    if req.bait is None:
        raise HTTPException(422, "a survival table needs a treatment: set `bait`")
    return _interop("survival.csv", run=_run_from(req))


@app.post("/api/interop/contact_edges.csv", tags=["interop"], responses=_binary("text/csv"),
          summary="The weighted contact network, as an edge list")
def interop_contact_edges_csv(req: ContactRequest) -> Response:
    return _interop("contact_edges.csv", run=_run_from(req))


@app.post("/api/interop/contact_nodes.csv", tags=["interop"], responses=_binary("text/csv"),
          summary="Per-animal covariates to join onto the edge list")
def interop_contact_nodes_csv(req: ContactRequest) -> Response:
    return _interop("contact_nodes.csv", run=_run_from(req))


@app.get("/api/interop/ligands.csv", tags=["interop"], responses=_binary("text/csv", needs_data=True),
         summary="The three actives with PubChem identifiers")
def interop_ligands_csv() -> Response:
    return _interop("ligands.csv")


@app.get("/api/interop/ligands.json", tags=["interop"],
         responses=_binary("application/json", needs_data=True),
         summary="The three actives, as JSON")
def interop_ligands_json() -> Response:
    return _interop("ligands.json")


@app.get("/api/interop/ligands.sdf", tags=["interop"],
         responses=_binary("chemical/x-mdl-sdfile", needs_data=True),
         summary="2D structures annotated with this project's toxicology")
def interop_ligands_sdf() -> Response:
    return _interop("ligands.sdf")


@app.get("/api/interop/fep_job.json", tags=["interop"],
         responses=_binary("application/json"),
         summary="A request to a group that can run a free-energy calculation")
def interop_fep_job_json(
    mutation: str = Query("L993F"),
    ligand: str = Query("deltamethrin"),
) -> Response:
    # deliberately no use_vqe: a cacheable, citable URL must not cost a minute of
    # CPU. POST /api/chem carries the VQE path.
    try:
        return _interop("fep_job.json", mutation=mutation, ligand=ligand)
    except KeyError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/api/interop/rnai_target.fasta", tags=["interop"],
         responses=_binary("text/x-fasta", needs_data=True),
         summary="A CYP6K1 window to design dsRNA against")
def interop_rnai_target_fasta(
    start: int | None = Query(None, description="one-based start inside the CDS"),
    length: int = Query(400, ge=19, le=1500),
) -> Response:
    try:
        return _interop("rnai_target.fasta", start=start, length=length)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/api/interop/dsrna_construct.gb", tags=["interop"],
         responses=_binary("chemical/seq-na-genbank", needs_data=True),
         summary="The same window with T7 flanks, annotated. NOT a validated construct")
def interop_dsrna_construct_gb(
    start: int | None = Query(None),
    length: int = Query(400, ge=19, le=1500),
) -> Response:
    try:
        return _interop("dsrna_construct.gb", start=start, length=length)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


class BundleRequest(ContactRequest):
    """A colony run, plus which formats to include. Omit `include` for everything."""

    include: list[str] | None = None


@app.get("/api/interop/bundle.omex", tags=["interop"], responses=_binary("application/zip"),
         summary="Everything that needs no simulation, in one COMBINE archive")
def interop_bundle_get() -> Response:
    # a stable, citable URL: the static half of the archive is a pure function of
    # the committed code, so two builds of it hash identically
    return _interop("bundle.omex")


@app.post("/api/interop/bundle.omex", tags=["interop"], responses=_binary("application/zip"),
          summary="The same archive plus the tables cut from a simulated colony")
def interop_bundle_post(req: BundleRequest) -> Response:
    from . import interop as _io

    if req.include is not None:
        unknown = [f for f in req.include if f not in _io.FORMATS]
        if unknown:
            raise HTTPException(422, f"unknown format(s) {unknown}; "
                                     f"have {sorted(_io.FORMATS)}")
    base = ContactRequest(**{k: v for k, v in req.model_dump().items() if k != "include"})
    return _interop("bundle.omex", run=_run_from(base), include=req.include)
