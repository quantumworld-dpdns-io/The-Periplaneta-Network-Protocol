"""
Matched-false-alarm evaluation and design-curve sweeps.

For every configuration (n_sensors, topology, noise) and every pipeline
(detector x network rule):

  1. simulate `n_calib` null runs (no perturbation), take the sustained maximum
     of the network series after the baseline, and set the threshold h to the
     (1 - alpha) quantile -- so every pipeline is compared at the *same*
     per-run false-alarm probability;
  2. simulate `n_heldout` fresh null runs to report the realised false-alarm
     rate at that h;
  3. simulate `n_eval` perturbed runs and record, per run, whether the network
     alarmed before the conventional endpoint (first collapse at the origin,
     independent of the sensors), the lead time (t_symptom - t_alarm), and the
     localisation error (distance from the
     estimated to the true origin, as a fraction of the domain diagonal).

Null runs are simulated once per configuration and reused across pipelines.
"""
from __future__ import annotations

import csv
import itertools
import math
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

from .detectors import DETECTORS, RULES, VOTE_LEVEL, alarm_time, localise, network_series, node_statistic, sustained_max
from .scenario import Config, Run, simulate

OUT_DIR = Path(__file__).resolve().parent / "out"


@dataclass
class Pipeline:
    detector: str
    rule: str

    @property
    def name(self) -> str:
        return f"{self.detector}+{self.rule}"


def all_pipelines() -> list[Pipeline]:
    return [Pipeline(d, r) for d in DETECTORS for r in RULES]


def _stat(run: Run, detector: str) -> np.ndarray:
    if detector not in run.stats:
        run.stats[detector] = node_statistic(detector, run.counts, run.cfg.baseline_bins)
    return run.stats[detector]


def _series(run: Run, p: Pipeline) -> np.ndarray:
    return network_series(_stat(run, p.detector), p.rule, run.nbrs, vote_level=VOTE_LEVEL[p.detector])


def calibrate(null_runs: list[Run], p: Pipeline, alpha: float) -> float:
    B, hold = null_runs[0].cfg.baseline_bins, null_runs[0].cfg.hold
    m = np.array([sustained_max(_series(r, p), B, hold) for r in null_runs])
    return float(np.quantile(m, 1.0 - alpha))


@dataclass
class RunResult:
    detected: bool
    t_alarm: float | None
    lead_s: float | None
    loc_err: float | None


def evaluate_run(run: Run, p: Pipeline, h: float) -> RunResult:
    cfg = run.cfg
    S = _series(run, p)
    t_a = alarm_time(S, h, cfg.baseline_bins, cfg.hold, cfg.dt)
    if t_a is None or run.t_symptom is None:
        return RunResult(False, t_a, None, None)
    if t_a >= run.t_symptom:
        return RunResult(False, t_a, run.t_symptom - t_a, None)
    stat = _stat(run, p.detector)[:, int(round(t_a / cfg.dt)) - 1]
    level = float(np.quantile(stat, 0.75))
    est = localise(stat, run.xy, level)
    diag = math.hypot(*cfg.domain)
    err = float(np.hypot(*(est - np.asarray(run.perturbation.origin)))) / diag
    return RunResult(True, t_a, run.t_symptom - t_a, err)


@dataclass
class ConfigSummary:
    n_sensors: int
    topology: str
    noise: float
    pipeline: str
    threshold: float
    far_heldout: float
    detect_prob: float
    lead_mean: float
    lead_sd: float
    lead_median: float
    loc_err_mean: float
    n_eval: int
    t_symptom_mean: float


def run_config(cfg: Config, pipelines: list[Pipeline], n_calib: int, n_heldout: int, n_eval: int,
               alpha: float, seed0: int) -> tuple[list[ConfigSummary], list[dict]]:
    null_calib = [simulate(cfg, seed0 + 100_000 + i, perturbed=False) for i in range(n_calib)]
    null_held = [simulate(cfg, seed0 + 200_000 + i, perturbed=False) for i in range(n_heldout)]
    pert = [simulate(cfg, seed0 + i, perturbed=True) for i in range(n_eval)]
    B, hold = cfg.baseline_bins, cfg.hold

    summaries: list[ConfigSummary] = []
    rows: list[dict] = []
    for p in pipelines:
        h = calibrate(null_calib, p, alpha)
        far = float(np.mean([sustained_max(_series(r, p), B, hold) > h for r in null_held])) if n_heldout else float("nan")
        res = [evaluate_run(r, p, h) for r in pert]
        leads = np.array([r.lead_s for r in res if r.detected], dtype=float)
        errs = np.array([r.loc_err for r in res if r.detected and r.loc_err is not None], dtype=float)
        summaries.append(ConfigSummary(
            n_sensors=cfg.n_sensors, topology=cfg.topology, noise=cfg.noise, pipeline=p.name, threshold=h,
            far_heldout=far, detect_prob=float(np.mean([r.detected for r in res])),
            lead_mean=float(leads.mean()) if leads.size else float("nan"),
            lead_sd=float(leads.std(ddof=1)) if leads.size > 1 else float("nan"),
            lead_median=float(np.median(leads)) if leads.size else float("nan"),
            loc_err_mean=float(errs.mean()) if errs.size else float("nan"),
            n_eval=n_eval, t_symptom_mean=float(np.mean([r.t_symptom for r in pert])),
        ))
        for r, rr in zip(pert, res):
            rows.append({"n_sensors": cfg.n_sensors, "topology": cfg.topology, "noise": cfg.noise, "pipeline": p.name,
                         "seed": r.seed, "threshold": h, "t_symptom": r.t_symptom, "t_alarm": rr.t_alarm,
                         "detected": int(rr.detected), "lead_s": rr.lead_s, "loc_err": rr.loc_err,
                         "origin_x": r.perturbation.origin[0], "origin_y": r.perturbation.origin[1]})
    return summaries, rows


@dataclass
class SweepSpec:
    n_sensors: tuple[int, ...] = (4, 9, 16, 36, 64, 100)
    topologies: tuple[str, ...] = ("grid", "random", "clustered")
    noises: tuple[float, ...] = (0.0, 0.15, 0.3)
    n_calib: int = 40
    n_heldout: int = 20
    n_eval: int = 20
    alpha: float = 0.05
    seed0: int = 1
    base: Config = Config()

    @staticmethod
    def quick() -> "SweepSpec":
        return SweepSpec(n_sensors=(9, 36), topologies=("grid", "random"), noises=(0.15,),
                         n_calib=12, n_heldout=6, n_eval=6)


def sweep(spec: SweepSpec, pipelines: list[Pipeline] | None = None, out_dir: Path = OUT_DIR,
          log=print) -> list[ConfigSummary]:
    pipelines = pipelines or all_pipelines()
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[ConfigSummary] = []
    rows: list[dict] = []
    combos = list(itertools.product(spec.n_sensors, spec.topologies, spec.noises))
    t0 = time.time()
    for i, (n, topo, noise) in enumerate(combos, 1):
        cfg = replace(spec.base, n_sensors=n, topology=topo, noise=noise)
        s, r = run_config(cfg, pipelines, spec.n_calib, spec.n_heldout, spec.n_eval, spec.alpha, spec.seed0)
        summaries += s
        rows += r
        best = max(s, key=lambda x: (x.detect_prob, x.lead_mean if not math.isnan(x.lead_mean) else -1))
        log(f"[{i:2d}/{len(combos)}] n={n:3d} {topo:9s} noise={noise:.2f}  best {best.pipeline:13s} "
            f"P(det)={best.detect_prob:.2f} lead={best.lead_mean:5.1f}s  ({time.time() - t0:5.0f}s)")
    write_csv(out_dir / "summary.csv", [asdict(s) for s in summaries])
    write_csv(out_dir / "runs.csv", rows)
    (out_dir / "sweep_spec.txt").write_text(repr(spec) + "\n")
    return summaries


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------ reports --
def markdown_report(summaries: list[ConfigSummary], spec: SweepSpec) -> str:
    def fmt(x: float, d: int = 1) -> str:
        return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{d}f}"

    lines = ["# netsim results", "", "Generated by `netsim/cli.py sweep`. Do not edit by hand.", "",
             f"Matched per-run false-alarm probability alpha = {spec.alpha}; thresholds from {spec.n_calib} null runs, "
             f"realised FAR on {spec.n_heldout} held-out null runs; {spec.n_eval} perturbed runs per cell. "
             f"Domain {spec.base.domain[0]:.0f}×{spec.base.domain[1]:.0f}, front speed {spec.base.speed} u/s, "
             f"decay tau {spec.base.tau_s} s, baseline {spec.base.baseline_s:.0f} s, onset {spec.base.onset_s:.0f} s, "
             f"hold {spec.base.hold} bins.", ""]
    noises = sorted({s.noise for s in summaries})
    for noise in noises:
        lines += [f"## noise = {noise:.2f}", ""]
        for topo in sorted({s.topology for s in summaries}):
            lines += [f"### {topo}", "", "| n | pipeline | P(detect) | lead s (mean ± sd) | median | loc err | FAR held-out | h |",
                      "|---|---|---|---|---|---|---|---|"]
            for s in sorted([s for s in summaries if s.noise == noise and s.topology == topo],
                            key=lambda s: (s.n_sensors, s.pipeline)):
                lines.append(f"| {s.n_sensors} | {s.pipeline} | {s.detect_prob:.2f} | {fmt(s.lead_mean)} ± {fmt(s.lead_sd)} | "
                             f"{fmt(s.lead_median)} | {fmt(s.loc_err_mean, 3)} | {fmt(s.far_heldout, 2)} | {fmt(s.threshold, 2)} |")
            lines.append("")
    lines += ["## Best pipeline per cell (by detection probability, then mean lead)", "",
              "| noise | topology | n | pipeline | P(detect) | lead s | loc err |", "|---|---|---|---|---|---|---|"]
    for (noise, topo, n), grp in itertools.groupby(
            sorted(summaries, key=lambda s: (s.noise, s.topology, s.n_sensors)),
            key=lambda s: (s.noise, s.topology, s.n_sensors)):
        best = max(grp, key=lambda x: (x.detect_prob, x.lead_mean if not math.isnan(x.lead_mean) else -1))
        lines.append(f"| {noise:.2f} | {topo} | {n} | {best.pipeline} | {best.detect_prob:.2f} | {fmt(best.lead_mean)} | {fmt(best.loc_err_mean, 3)} |")
    lines += ["", "## What this is not", "",
              "- Ground truth (front geometry, decay, collapse endpoint) is simulated; only the single-node ISI statistics are empirical.",
              "- Lead time is measured against the simulated first collapse at the origin, a network-design quantity, not a biological prodrome.",
              "- Rate is the only feature; ISI-shape features and learned detectors are out of scope here (see ml/ for the GRU).", ""]
    return "\n".join(lines)


def plot_design_curves(summaries: list[ConfigSummary], out_dir: Path = OUT_DIR, noise: float | None = None) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    noises = [noise] if noise is not None else sorted({s.noise for s in summaries})
    topos = sorted({s.topology for s in summaries})
    pipes = sorted({s.pipeline for s in summaries})
    for nz in noises:
        fig, axes = plt.subplots(2, len(topos), figsize=(4.2 * len(topos), 6.4), sharex=True, squeeze=False)
        for j, topo in enumerate(topos):
            for p in pipes:
                pts = sorted([s for s in summaries if s.noise == nz and s.topology == topo and s.pipeline == p], key=lambda s: s.n_sensors)
                if not pts:
                    continue
                xs = [s.n_sensors for s in pts]
                ls = "-" if p.endswith("+pool") else ("--" if p.endswith("+max") else ":")
                axes[0, j].plot(xs, [s.lead_mean for s in pts], ls, marker="o", ms=3, label=p)
                axes[1, j].plot(xs, [s.detect_prob for s in pts], ls, marker="o", ms=3, label=p)
            axes[0, j].set_title(f"{topo}")
            axes[1, j].set_xlabel("sensors")
            axes[0, j].grid(alpha=0.3)
            axes[1, j].grid(alpha=0.3)
            axes[1, j].set_ylim(0, 1.02)
        axes[0, 0].set_ylabel("lead time vs first collapse (s)")
        axes[1, 0].set_ylabel("P(alarm before collapse)")
        axes[0, -1].legend(fontsize=7, loc="lower right")
        fig.suptitle(f"Sensor-network design curves · measurement noise sd {nz:.2f} · matched FAR")
        fig.tight_layout()
        path = out_dir / f"design_curves_noise{nz:.2f}.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        paths.append(path)
    return paths
