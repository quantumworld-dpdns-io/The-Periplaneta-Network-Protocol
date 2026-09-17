"""
netsim command line.

  python -m netsim.cli sweep [--quick] [--out DIR]     design-curve sweep -> CSV, RESULTS.md, PNG
  python -m netsim.cli run  --n 36 --topology grid --noise 0.15 --detector cusum --rule pool --seed 1
  python -m netsim.cli fit-real                        refit the Zenodo cockroach recording (if data/raw has it)
  python -m netsim.cli describe                        print the model and the empirical inputs
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

from . import experiment as ex
from .detectors import DETECTORS, RULES
from .renewal import TWIN_PARAMS, fit_spike_train, load_twin_params
from .scenario import Config, simulate


def cmd_sweep(a: argparse.Namespace) -> int:
    spec = ex.SweepSpec.quick() if a.quick else ex.SweepSpec()
    if a.n_eval:
        spec = replace(spec, n_eval=a.n_eval, n_calib=max(a.n_eval * 2, 8), n_heldout=max(a.n_eval, 4))
    out = Path(a.out) if a.out else ex.OUT_DIR
    summaries = ex.sweep(spec, out_dir=out)
    (out / "RESULTS.md").write_text(ex.markdown_report(summaries, spec))
    for p in ex.plot_design_curves(summaries, out):
        print("wrote", p)
    print("wrote", out / "RESULTS.md")
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    cfg = Config(n_sensors=a.n, topology=a.topology, noise=a.noise)
    nulls = [simulate(cfg, 100_000 + i, perturbed=False) for i in range(a.n_calib)]
    p = ex.Pipeline(a.detector, a.rule)
    h = ex.calibrate(nulls, p, a.alpha)
    run = simulate(cfg, a.seed)
    r = ex.evaluate_run(run, p, h)
    print(json.dumps({
        "config": asdict(cfg), "pipeline": p.name, "threshold": h, "seed": a.seed,
        "origin": run.perturbation.origin, "t_symptom": run.t_symptom,
        "t_alarm": r.t_alarm, "detected": r.detected, "lead_s": r.lead_s, "loc_err": r.loc_err,
    }, indent=2))
    return 0


def cmd_fit_real(a: argparse.Namespace) -> int:
    from .renewal import REPO_ROOT
    h5 = REPO_ROOT / "data" / "raw" / "CockroachDataJNM_2009_181_119.h5"
    if not h5.exists():
        print(f"{h5} not found; run ./data/fetch.sh zenodo first", file=sys.stderr)
        return 2
    import h5py
    fits = []
    with h5py.File(h5, "r") as f:
        for e in sorted(f.keys()):
            if e == "README" or not isinstance(f[e], h5py.Group):
                continue
            for nk in sorted(f[e].keys()):
                g = f[e][nk]
                if nk == "date" or not isinstance(g, h5py.Group) or "spont" not in g:
                    continue
                st = g["spont"][()].astype(float).ravel()
                if st.size < 30:
                    continue
                p = fit_spike_train(st)
                fits.append({"unit": f"{e}/{nk}", "n_spikes": int(st.size), "shape": round(p.shape, 4),
                             "rate_hz": round(p.rate_hz, 4), "refractory_ms": round(p.refractory_ms, 4)})
    out = ex.OUT_DIR / "cockroach_units_python.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"source": "zenodo:10.5281/zenodo.14281 spont trains, netsim.renewal.fit_spike_train",
                               "units": fits}, indent=2))
    print(f"fitted {len(fits)} units -> {out}")
    ref, _ = load_twin_params()
    print(f"committed twin_params.json has {len(ref)} units (cockroach + locust)")
    return 0


def cmd_describe(_: argparse.Namespace) -> int:
    units, meta = load_twin_params()
    cfg = Config()
    print(json.dumps({
        "empirical_input": {"file": str(TWIN_PARAMS.relative_to(TWIN_PARAMS.parents[2])), **meta,
                            "n_units": len(units),
                            "rate_hz_range": [min(u.rate_hz for u in units), max(u.rate_hz for u in units)],
                            "shape_range": [min(u.shape for u in units), max(u.shape for u in units)]},
        "default_config": asdict(cfg),
        "detectors": list(DETECTORS), "rules": list(RULES),
        "ground_truth": "simulated: radial front at `speed`, exponential rate decay tau_s behind it, "
                        "symptom onset = first collapse at the origin (rate below 5% baseline for 2 s), independent of sensors",
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="netsim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sweep")
    s.add_argument("--quick", action="store_true")
    s.add_argument("--n-eval", type=int, default=0)
    s.add_argument("--out")
    s.set_defaults(fn=cmd_sweep)

    r = sub.add_parser("run")
    r.add_argument("--n", type=int, default=36)
    r.add_argument("--topology", default="grid")
    r.add_argument("--noise", type=float, default=0.15)
    r.add_argument("--detector", default="cusum", choices=DETECTORS)
    r.add_argument("--rule", default="pool", choices=RULES)
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--alpha", type=float, default=0.05)
    r.add_argument("--n-calib", type=int, default=30)
    r.set_defaults(fn=cmd_run)

    sub.add_parser("fit-real").set_defaults(fn=cmd_fit_real)
    sub.add_parser("describe").set_defaults(fn=cmd_describe)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
