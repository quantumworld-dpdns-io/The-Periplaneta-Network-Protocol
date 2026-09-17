"""
blattella command line.

  python -m blattella.cli contact --n 300 --hours 12       simulate and summarise the contact network
  python -m blattella.cli provenance [--out FILE]          dump the parameter provenance table
  python -m blattella.cli describe                         what is implemented so far

Phase 1 of the rebuild (see .claude/PLAN.md) covers behaviour and contact only.
Toxicology, chemistry, genetics and the neural readout land in later phases.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .arena import default_arena
from .behaviour import STATE_NAMES, make_colony, simulate
from .contact import ContactRecorder
from .params import assumptions, provenance_report, registry
from .toxicology import ACTIVES, Toxicology
from .chem import MUTATIONS
from .chem.classical import ClassicalBackend
from .chem.quantum import QuantumBackend
from .chem.compare import compare as chem_compare, report as chem_report
from .genome import Genome, default_loci, linked_loci
from .population import Deployment, ExposureProfile, make_population
from . import experiment as ex
from .phenotype import predictions as neural_predictions, report as neural_report
from . import export as exporter

ROOT = Path(__file__).resolve().parents[1]


def cmd_contact(a: argparse.Namespace) -> int:
    arena = default_arena(n_harborages=a.harborages, n_resources=a.resources,
                          width=a.size, height=a.size, seed=a.seed)
    colony = make_colony(n=a.n, seed=a.seed, arena=arena)
    colony.t = 12 * 3600.0                       # start at dusk
    rec = ContactRecorder()
    simulate(colony, duration_s=a.hours * 3600.0, dt=a.dt, recorder=rec)
    net = rec.network()
    out = {
        "config": {"n": a.n, "hours": a.hours, "dt": a.dt, "arena_cm": a.size,
                   "harborages": a.harborages, "resources": a.resources, "seed": a.seed},
        "network": net.summary(),
        "states": colony.state_counts(),
        "trips_per_animal": {
            "mean": round(float(colony.emergences.mean()), 3),
            "max": int(colony.emergences.max()),
            "never_left": int((colony.emergences == 0).sum()),
        },
        "super_spreaders": [int(i) for i in net.super_spreaders(5)],
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_bait(a: argparse.Namespace) -> int:
    """Place a gel bait and report the kill and how the insecticide travelled."""
    arena = default_arena(n_harborages=a.harborages, n_resources=a.resources,
                          width=a.size, height=a.size, seed=a.seed)
    colony = make_colony(n=a.n, seed=a.seed, arena=arena)
    colony.t = 12 * 3600.0                       # dusk
    actives = tuple(ACTIVES[name] for name in a.actives)
    tox = Toxicology(colony=colony, actives=actives)
    tox.treat_resource(a.bait, list(range(a.stations)))
    rec = ContactRecorder()
    simulate(colony, duration_s=a.hours * 3600.0, dt=a.dt, recorder=rec, observers=[tox])
    net = rec.network()
    print(json.dumps({
        "config": {"n": a.n, "hours": a.hours, "bait": a.bait, "stations": a.stations,
                   "of_resources": a.resources, "seed": a.seed},
        "toxicology": tox.summary(),
        "network": net.summary(),
        "fed_directly": int((tox.acquired_from[0] > 0).sum()),
        "secondary_kill": int((~colony.alive).sum() - ((tox.acquired_from[0] > 0) & ~colony.alive).sum()),
    }, indent=2))
    return 0


def cmd_chem(a: argparse.Namespace) -> int:
    """Compare every ΔΔG backend against the literature-derived reference."""
    backends = [ClassicalBackend()]
    if not a.no_quantum:
        backends.append(QuantumBackend(use_vqe=not a.exact))
    out = chem_compare(backends, mutation=MUTATIONS[a.mutation], ligand=a.ligand)
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        print(chem_report(out), end="")
    if a.out:
        Path(a.out).write_text(chem_report(out))
        print(f"wrote {a.out}")
    return 0


STRATEGIES = {
    "single": ["deltamethrin"],
    "rotation": ["deltamethrin", "imidacloprid", "fipronil"],
    "untreated": [None],
}


def cmd_evolve(a: argparse.Namespace) -> int:
    """Run resistance evolution under one deployment strategy."""
    genome = Genome(loci=linked_loci() if a.linked else default_loci())
    pop = make_population(n=a.n, seed=a.seed, genome=genome)
    profile = ExposureProfile(exposed_fraction=a.exposed, dose_ld50_median=a.dose,
                              dose_ld50_log_sd=a.dose_sd)
    none = ExposureProfile(0.0, 0.0)
    profiles = {k: profile for k in ACTIVES}
    profiles[None] = none
    schedule = STRATEGIES[a.strategy]
    pop.run(Deployment(schedule), a.generations, profiles)
    out = {
        "config": {"strategy": a.strategy, "schedule": schedule, "generations": a.generations,
                   "n": a.n, "linked_loci": a.linked, "seed": a.seed,
                   "exposure": {"fraction": a.exposed, "median_dose_ld50": a.dose}},
        "final_allele_frequencies": {k: round(v, 4) for k, v in pop.allele_frequencies().items()},
        "final_n": int(pop.hap.shape[0]),
        "generations_run": pop.generation,
        "history": pop.history if a.full_history else pop.history[:: max(1, a.generations // 10)],
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_compare(a: argparse.Namespace) -> int:
    """The headline experiment: rotation vs mixture vs single product."""
    profile = ExposureProfile(exposed_fraction=a.exposed, dose_ld50_median=a.dose,
                              dose_ld50_log_sd=a.dose_sd)
    comp = ex.compare_strategies(seeds=a.seeds, generations=a.generations,
                                 profile=profile, n=a.n, linked=a.linked)
    out = Path(a.out) if a.out else ex.OUT_DIR
    ex.write_csv(comp, out / "STRATEGY_RUNS.csv")
    (out / "STRATEGY_COMPARISON.md").write_text(ex.report(comp))
    fig = ex.plot(comp, out / "strategy_comparison.png")
    print(ex.report(comp))
    print(f"wrote {out / 'STRATEGY_COMPARISON.md'}, {out / 'STRATEGY_RUNS.csv'} and {fig}")
    return 0


def cmd_neural(a: argparse.Namespace) -> int:
    """Predicted spike-train signatures of intoxication, as falsifiable claims."""
    pred = neural_predictions(burden_ld50=a.burden, seed=a.seed)
    text = neural_report(pred)
    print(json.dumps(pred, indent=2) if a.json else text, end="" if not a.json else "\n")
    if a.out:
        Path(a.out).write_text(text)
        print(f"wrote {a.out}")
    return 0


def cmd_export(a: argparse.Namespace) -> int:
    """Everything the model knows, as one JSON document for the dashboard."""
    out = exporter.write(Path(a.out), seeds=a.seeds, generations=a.generations,
                         contact_hours=a.contact_hours, colony_n=a.colony)
    size = out.stat().st_size / 1024
    print(f"wrote {out} ({size:.0f} KiB)")
    return 0


def cmd_provenance(a: argparse.Namespace) -> int:
    report = provenance_report()
    if a.out:
        Path(a.out).write_text(report)
        print(f"wrote {a.out}: {len(registry())} parameters, {len(assumptions())} assumptions")
    else:
        print(report, end="")
    return 0


def cmd_interop(a) -> int:
    """
    The same bytes the API serves, without needing a server.

    A format whose reference data has not been fetched exits 3 with the command
    that fixes it on stderr, rather than a traceback: this is the one failure a
    user is expected to hit and it has an obvious remedy.
    """
    from . import interop
    from .interop import sources

    if a.list or not (a.format or a.all or a.bundle):
        print(f"{'id':<24} {'availability':<12} {'consumers'}")
        for f in interop.FORMATS.values():
            print(f"{f.id:<24} {interop.availability(f.id):<12} {', '.join(f.consumers)}")
        missing = [k for k, ok in sources.status()["fetched"].items() if not ok]
        if missing:
            print(f"\nreference data not fetched: {', '.join(missing)}"
                  f"\n  {sources.status()['fetch_command']}", file=sys.stderr)
        return 0

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.bundle and not a.format:
        wanted = ["bundle.omex"]
    elif a.format:
        wanted = [a.format]
    else:
        # the archive is a container, not a content; --bundle asks for it
        wanted = [f for f, s in interop.FORMATS.items() if s.in_bundle]
    if a.format and a.format not in interop.FORMATS:
        print(f"unknown format {a.format!r}; have {', '.join(interop.FORMATS)}", file=sys.stderr)
        return 2

    # one simulation shared by every run-backed format, so the survival table and
    # the contact network describe the same colony rather than two different ones
    run = None
    if any(interop.FORMATS[f].needs_run or f == "bundle.omex"
           for f in wanted if f in interop.FORMATS):
        from .interop.runs import colony_run
        run = colony_run(colony=a.colony, hours=a.hours, bait=a.bait,
                         stations=a.stations, seed=a.seed)

    written, skipped = [], []
    for fid in wanted:
        spec = interop.FORMATS[fid]
        try:
            if fid == "bundle.omex":
                art = interop.render(fid, run=run)
            else:
                art = interop.render(fid, run=run) if spec.needs_run else interop.render(fid)
        except interop.DataUnavailable as e:
            skipped.append((fid, e.fetch))
            if a.format:
                print(f"{fid}: needs data that is not fetched\n  run: {e.fetch}", file=sys.stderr)
                return 3
            continue
        except interop.FormatUnsupported as e:
            skipped.append((fid, f"pip install {e.library}"))
            continue
        (out / art.filename).write_bytes(art.data)
        written.append(art)

    for art in written:
        print(f"wrote {out / art.filename} ({len(art) / 1024:.1f} KiB)")
    for fid, fix in skipped:
        print(f"skipped {fid}: {fix}", file=sys.stderr)
    return 0


def cmd_describe(_: argparse.Namespace) -> int:
    counts: dict[str, int] = {}
    for p in registry():
        counts[p.source.value] = counts.get(p.source.value, 0) + 1
    print(json.dumps({
        "question": "Which insecticide deployment strategy -- rotation, mixture or "
                    "single-product -- best delays resistance evolution in an "
                    "interacting Blattella germanica colony?",
        "implemented": ["behaviour (movement, harborage use, aggregation)",
                        "contact network (emergent, weighted by contact-seconds)",
                        "toxicology (probit dose-response, internal burden, "
                        "horizontal transfer by contact / faeces / corpse)",
                        "chemistry interface: ddG from resistance ratio "
                        "(reference) plus a classical backend, with maturity "
                        "labels and a backend comparison",
                        "genome (2n=42, haplotypes, linkage, recombination) and "
                        "population genetics (selection, fitness cost, allele "
                        "frequency trajectories)",
                        "quantum ddG backend: VQE on a two-site Hubbard model of "
                        "the residue-ligand contact, validated against exact "
                        "diagonalisation and scored against the measurement",
                        "strategy comparison: rotation, mixture and single "
                        "product, matched on total dose, over multiple seeds",
                        "neural phenotype readout: predicted spike-train "
                        "signatures per mode of action, written as falsifiable "
                        "claims with their blind spots declared"],
        "pending": [],
        "coupling_channels": ["aggregation pheromone (stigmergy)",
                              "conspecific attraction", "volume exclusion"],
        "states": list(STATE_NAMES.values()),
        "parameters_by_source": counts,
        "note": "Every parameter carries provenance; assumptions declare a sweep "
                "range and must appear in the sensitivity analysis.",
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="blattella", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("contact", help="simulate a colony and summarise the contact network")
    c.add_argument("--n", type=int, default=300)
    c.add_argument("--hours", type=float, default=12.0)
    c.add_argument("--dt", type=float, default=1.0)
    c.add_argument("--size", type=float, default=300.0, help="arena side, cm")
    c.add_argument("--harborages", type=int, default=6)
    c.add_argument("--resources", type=int, default=3)
    c.add_argument("--seed", type=int, default=1)
    c.set_defaults(fn=cmd_contact)

    b = sub.add_parser("bait", help="place a gel bait and report kill and transfer routes")
    b.add_argument("--n", type=int, default=200)
    b.add_argument("--hours", type=float, default=24.0)
    b.add_argument("--dt", type=float, default=1.0)
    b.add_argument("--bait", default="fipronil", choices=sorted(ACTIVES))
    b.add_argument("--actives", nargs="+", default=sorted(ACTIVES), choices=sorted(ACTIVES))
    b.add_argument("--stations", type=int, default=1, help="how many resource sites are treated")
    b.add_argument("--size", type=float, default=300.0)
    b.add_argument("--harborages", type=int, default=6)
    b.add_argument("--resources", type=int, default=3)
    b.add_argument("--seed", type=int, default=1)
    b.set_defaults(fn=cmd_bait)

    ch = sub.add_parser("chem", help="compare ddG backends against the measured reference")
    ch.add_argument("--mutation", default="L993F", choices=sorted(MUTATIONS))
    ch.add_argument("--ligand", default="deltamethrin", choices=sorted(ACTIVES))
    ch.add_argument("--json", action="store_true")
    ch.add_argument("--no-quantum", action="store_true", help="skip the VQE backend")
    ch.add_argument("--exact", action="store_true",
                    help="diagonalise the quantum model exactly instead of running VQE")
    ch.add_argument("--out")
    ch.set_defaults(fn=cmd_chem)

    e = sub.add_parser("evolve", help="resistance evolution under a deployment strategy")
    e.add_argument("--strategy", default="rotation", choices=sorted(STRATEGIES))
    e.add_argument("--generations", type=int, default=30)
    e.add_argument("--n", type=int, default=400)
    e.add_argument("--exposed", type=float, default=0.6, help="fraction of the colony exposed")
    e.add_argument("--dose", type=float, default=50.0, help="median dose in susceptible LD50s")
    e.add_argument("--dose-sd", type=float, default=1.0, help="log sd of the dose")
    e.add_argument("--linked", action="store_true", help="place kdr and cyp6 5 cM apart")
    e.add_argument("--full-history", action="store_true")
    e.add_argument("--seed", type=int, default=1)
    e.set_defaults(fn=cmd_evolve)

    cp = sub.add_parser("compare", help="the headline experiment: rotation vs mixture vs single")
    cp.add_argument("--seeds", type=int, default=12)
    cp.add_argument("--generations", type=int, default=40)
    cp.add_argument("--n", type=int, default=400)
    cp.add_argument("--exposed", type=float, default=0.6)
    cp.add_argument("--dose", type=float, default=50.0, help="median dose in susceptible LD50s")
    cp.add_argument("--dose-sd", type=float, default=1.0)
    cp.add_argument("--linked", action="store_true", help="place kdr and cyp6 5 cM apart")
    cp.add_argument("--out", help="output directory (default docs/blattella)")
    cp.set_defaults(fn=cmd_compare)

    nu = sub.add_parser("neural", help="predicted spike-train signatures of intoxication")
    nu.add_argument("--burden", type=float, default=0.5, help="internal burden in LD50 units")
    nu.add_argument("--seed", type=int, default=0)
    nu.add_argument("--json", action="store_true")
    nu.add_argument("--out")
    nu.set_defaults(fn=cmd_neural)

    xp = sub.add_parser("export", help="write the whole model state as JSON for the dashboard")
    xp.add_argument("--out", default="dashboard/public/blattella.json")
    xp.add_argument("--seeds", type=int, default=12)
    xp.add_argument("--generations", type=int, default=40)
    xp.add_argument("--contact-hours", type=float, default=6.0)
    xp.add_argument("--colony", type=int, default=200)
    xp.set_defaults(fn=cmd_export)

    p = sub.add_parser("provenance", help="dump the parameter provenance table")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_provenance)

    sub.add_parser("describe", help="what this package models so far").set_defaults(fn=cmd_describe)

    ip = sub.add_parser("interop", help="write results in formats other software reads")
    ip.add_argument("--list", action="store_true", help="what can be written, and whether it can be")
    ip.add_argument("--format", help="write one format by id")
    ip.add_argument("--all", action="store_true", help="write every available format")
    ip.add_argument("--bundle", action="store_true", help="write the COMBINE archive")
    ip.add_argument("--out", default="handoff", help="directory to write into")
    ip.add_argument("--colony", type=int, default=150, help="animals, for run-backed formats")
    ip.add_argument("--hours", type=float, default=4.0)
    ip.add_argument("--bait", default="fipronil", help="active to put down, or none")
    ip.add_argument("--stations", type=int, default=1)
    ip.add_argument("--seed", type=int, default=1)
    ip.set_defaults(fn=cmd_interop)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
