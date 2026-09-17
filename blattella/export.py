"""
Export everything the model knows as one JSON document.

This is the contract between the science and anything that displays it. The
dashboard reads exactly this file and nothing else: there is no live backend any
more, and a dashboard that appears to be showing live data when it is not would
be the same dishonesty the rebuild removed elsewhere.

    python -m blattella.cli export --out dashboard/public/blattella.json
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import experiment as ex
from .behaviour import make_colony, simulate
from .chem import KDR_L993F, VSSC, reference_ddg
from .chem.classical import ClassicalBackend
from .chem.compare import compare as chem_compare
from .chem.quantum import QuantumBackend
from .contact import ContactRecorder
from .params import Source, assumptions, registry
from .phenotype import predictions as neural_predictions
from .population import Deployment, ExposureProfile, Population
from .genome import Genome
from .strategy import standard_arms


def _trajectories(arms, generations: int, profile: ExposureProfile, n: int, seed: int) -> dict:
    """One representative run per arm, with its full allele-frequency history."""
    out = {}
    g = Genome()
    for st in arms:
        pop = Population(genome=g, n=n, rng=np.random.default_rng(seed))
        profiles = {a: profile for a in ("deltamethrin", "imidacloprid", "fipronil")}
        pop.run(Deployment.from_strategy(st), generations, profiles)
        out[st.name] = {
            "description": st.description,
            "history": [{k: v for k, v in row.items() if k != "extinct"} for row in pop.history],
        }
    return out


def build(seeds: int = 12, generations: int = 40, contact_hours: float = 6.0,
          colony_n: int = 200, seed: int = 1) -> dict:
    arms = standard_arms()
    profile = ExposureProfile(exposed_fraction=0.6, dose_ld50_median=50.0, dose_ld50_log_sd=1.0)

    comp = ex.compare_strategies(seeds=seeds, generations=generations, profile=profile,
                                 log=lambda *_: None)

    colony = make_colony(n=colony_n, seed=seed)
    colony.t = 12 * 3600.0
    rec = ContactRecorder()
    simulate(colony, contact_hours * 3600.0, 1.0, recorder=rec)
    net = rec.network()

    ddg = chem_compare([ClassicalBackend(), QuantumBackend(use_vqe=False)])

    by_source: dict[str, int] = {}
    for p in registry():
        by_source[p.source.value] = by_source.get(p.source.value, 0) + 1

    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "question": "Which insecticide deployment strategy -- rotation, mixture or "
                    "single-product -- best delays resistance evolution in an "
                    "interacting Blattella germanica colony?",
        "answer": "Rotation, and specifically fast rotation. It is the only arm that "
                  "holds target-site resistance below threshold in most runs while "
                  "still killing well. The matched-dose mixture controls best and "
                  "selects worst.",
        "caveats": [
            "Ground truth is simulated. The empirical inputs are the LD50s, the "
            "resistance ratio, the karyotype, the life history and the fitted spike "
            "statistics.",
            f"{by_source.get('assumption', 0)} of {sum(by_source.values())} named "
            "parameters are assumptions, each with a declared sweep range. The "
            "systematic sensitivity analysis has not been run.",
            "The fitness cost of resistance decides the headline result and has no "
            "published value for these alleles.",
            "Neither ddG backend is fit to drive the science and both say so; the "
            "population layer uses the measured value.",
            "No reinvasion from neighbouring units, which is among the strongest "
            "influences on real resistance dynamics.",
        ],
        "parameters": {
            "by_source": by_source,
            "assumptions_needing_sensitivity": [
                {"name": p.name, "value": p.value, "unit": p.unit, "sweep": list(p.sweep)}
                for p in assumptions() if p.name
            ],
            "literature": [
                {"name": p.name, "value": p.value, "unit": p.unit, "cite": p.cite}
                for p in registry() if p.source is Source.LITERATURE and p.name
            ],
        },
        "strategy": {
            "config": {"seeds": seeds, "generations": generations,
                       "exposed_fraction": profile.exposed_fraction,
                       "median_dose_ld50": profile.dose_ld50_median},
            "summary": comp.summary(),
            "trajectories": _trajectories(arms, generations, profile, 400, 1000),
        },
        "contact": {
            "config": {"n": colony_n, "hours": contact_hours,
                       "arena_cm": colony.arena.width,
                       "harborages": len(colony.arena.harborages),
                       "resources": len(colony.arena.resources)},
            "network": net.summary(),
            "harborages": [{"x": s.x, "y": s.y, "r": s.r} for s in colony.arena.harborages],
            "resources": [{"x": s.x, "y": s.y, "r": s.r} for s in colony.arena.resources],
            "positions": colony.positions().round(1).tolist(),
            "degree": net.degree().tolist(),
            "trips": colony.emergences.tolist(),
        },
        "chemistry": ddg,
        "neural": neural_predictions(burden_ld50=0.5),
    }


def write(path: Path, **kwargs) -> Path:
    doc = build(**kwargs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1))
    return path
