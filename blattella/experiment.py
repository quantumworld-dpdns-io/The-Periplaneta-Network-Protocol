"""
The strategy comparison: the experiment the project was rebuilt to run.

    Which insecticide deployment strategy -- rotation, mixture, or single-product
    use -- best delays the evolution of resistance in an interacting
    *Blattella germanica* colony?

Arms are matched on **total insecticide applied per generation**, which is the
only way the comparison means anything: a mixture of three actives gives each a
third of a dose, so it is not simply three times the treatment.

Three outcomes are reported, because a strategy can win on one and lose on
another and reporting only the flattering one is how this kind of study goes
wrong:

* **time to resistance** -- first generation at which any target-site allele
  passes 0.5. Censored at the horizon when it never does.
* **control** -- mean fraction surviving each treatment. A strategy that
  preserves susceptibility by barely killing anything is not a win.

  Note that adult *numbers* are useless as a control metric here, and the run
  shows why: a female produces around 180 eggs in a lifetime, so the colony
  returns to carrying capacity within one generation whatever the kill. Every
  arm, including untreated, sits at 400 adults. That is a real property of the
  species rather than an artefact, and it is exactly why resistance management
  rather than knockdown is the interesting question.
* **final allele frequencies** -- which mechanisms were selected, and whether the
  cost was paid in target-site or metabolic resistance.

Every cell is run over multiple seeds and reported with a spread.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .genome import Genome, TARGET_SITE, default_loci, linked_loci
from .population import Deployment, ExposureProfile, Population
from .strategy import Strategy, standard_arms

OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "blattella"
RESISTANCE_THRESHOLD = 0.5


@dataclass
class RunResult:
    strategy: str
    seed: int
    generations: int
    time_to_resistance: int | None      # None means it never happened
    mean_population: float
    mean_survival: float
    final_population: int
    extinct: bool
    final_freqs: dict[str, float]
    peak_target_site: float


def run_once(strategy: Strategy, seed: int, generations: int, profile: ExposureProfile,
             n: int = 400, genome: Genome | None = None) -> RunResult:
    g = genome or Genome()
    pop = Population(genome=g, n=n, rng=np.random.default_rng(seed))
    profiles = {a: profile for a in ("deltamethrin", "imidacloprid", "fipronil")}
    pop.run(Deployment.from_strategy(strategy), generations, profiles)

    ts_loci = [l.name for l in g.loci if l.kind == TARGET_SITE]
    ttr: int | None = None
    peak = 0.0
    for row in pop.history:
        m = max(row[f"f_{k}"] for k in ts_loci)
        peak = max(peak, m)
        if ttr is None and m > RESISTANCE_THRESHOLD:
            ttr = int(row["generation"])
    sizes = [row["n"] for row in pop.history[1:]] or [pop.hap.shape[0]]
    survs = [row["survival"] for row in pop.history[1:] if row["survival"] is not None]
    return RunResult(
        strategy=strategy.name, seed=seed, generations=pop.generation,
        time_to_resistance=ttr, mean_population=float(np.mean(sizes)),
        mean_survival=float(np.mean(survs)) if survs else float("nan"),
        final_population=int(pop.hap.shape[0]),
        extinct=bool(pop.history[-1]["extinct"]),
        final_freqs={k: round(v, 4) for k, v in pop.allele_frequencies().items()},
        peak_target_site=round(peak, 4),
    )


@dataclass
class Comparison:
    results: list[RunResult]
    generations: int
    profile: ExposureProfile
    linked: bool = False

    def by_strategy(self) -> dict[str, list[RunResult]]:
        out: dict[str, list[RunResult]] = {}
        for r in self.results:
            out.setdefault(r.strategy, []).append(r)
        return out

    def summary(self) -> list[dict]:
        rows = []
        untreated = [r for r in self.results if r.strategy == "untreated"]
        baseline = float(np.mean([r.mean_population for r in untreated])) if untreated else float("nan")
        for name, rs in self.by_strategy().items():
            ttrs = [r.time_to_resistance for r in rs]
            reached = [t for t in ttrs if t is not None]
            pops = np.array([r.mean_population for r in rs])
            survs = np.array([r.mean_survival for r in rs])
            rows.append({
                "strategy": name,
                "seeds": len(rs),
                "resistant_runs": len(reached),
                # censored: report the median only over runs that got there, and
                # say how many did, rather than pretending a number for the rest
                "median_time_to_resistance": float(np.median(reached)) if reached else None,
                "peak_target_site_mean": round(float(np.mean([r.peak_target_site for r in rs])), 4),
                "mean_population": round(float(pops.mean()), 1),
                "mean_survival": round(float(survs.mean()), 4),
                "population_vs_untreated": (round(float(pops.mean() / baseline), 3)
                                            if baseline == baseline and baseline > 0 else None),
                "extinctions": sum(r.extinct for r in rs),
                **{f"f_{k}": round(float(np.mean([r.final_freqs[k] for r in rs])), 4)
                   for k in rs[0].final_freqs},
            })
        order = {s: i for i, s in enumerate(self.by_strategy())}
        return sorted(rows, key=lambda r: order[r["strategy"]])


def compare_strategies(arms: tuple[Strategy, ...] | None = None, seeds: int = 12,
                       generations: int = 40, profile: ExposureProfile | None = None,
                       n: int = 400, linked: bool = False, log=print) -> Comparison:
    arms = arms or standard_arms()
    prof = profile or ExposureProfile(exposed_fraction=0.6, dose_ld50_median=50.0,
                                      dose_ld50_log_sd=1.0)
    genome = Genome(loci=linked_loci() if linked else default_loci())
    results: list[RunResult] = []
    for st in arms:
        for s in range(seeds):
            results.append(run_once(st, seed=1000 + s, generations=generations,
                                    profile=prof, n=n, genome=genome))
        done = [r for r in results if r.strategy == st.name]
        reached = [r.time_to_resistance for r in done if r.time_to_resistance is not None]
        log(f"  {st.name:28s} resistant in {len(reached)}/{seeds} runs"
            + (f", median gen {np.median(reached):.0f}" if reached else ", never")
            + f", mean survival {np.mean([r.mean_survival for r in done]):.3f}")
    return Comparison(results=results, generations=generations, profile=prof, linked=linked)


# ------------------------------------------------------------------- outputs --
def write_csv(comp: Comparison, path: Path) -> None:
    rows = [{"strategy": r.strategy, "seed": r.seed, "generations": r.generations,
             "time_to_resistance": r.time_to_resistance, "mean_population": r.mean_population,
             "mean_survival": r.mean_survival,
             "final_population": r.final_population, "extinct": int(r.extinct),
             "peak_target_site": r.peak_target_site, **r.final_freqs}
            for r in comp.results]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def report(comp: Comparison) -> str:
    s = comp.summary()
    lines = [
        "# Strategy comparison", "",
        f"{comp.results[0].strategy and len(comp.by_strategy())} arms, "
        f"{s[0]['seeds']} seeds each, {comp.generations} generations, "
        f"{comp.profile.exposed_fraction:.0%} of the colony exposed at a median of "
        f"{comp.profile.dose_ld50_median:g} susceptible LD50s"
        + (", kdr and cyp6 linked at 5 cM" if comp.linked else "") + ".", "",
        "Arms are matched on total insecticide per generation: a mixture of three "
        "actives applies each at a third of a dose. Resistance means any "
        f"target-site allele above {RESISTANCE_THRESHOLD}.", "",
        "| strategy | resistant runs | median gen | peak target-site | mean survival | kdr | rdl | cyp6 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in s:
        ttr = "never" if r["median_time_to_resistance"] is None else f"{r['median_time_to_resistance']:.0f}"
        lines.append(
            f"| {r['strategy']} | {r['resistant_runs']}/{r['seeds']} | {ttr} | "
            f"{r['peak_target_site_mean']:.3f} | {r['mean_survival']:.3f} | "
            f"{r['f_kdr']:.3f} | {r['f_rdl']:.3f} | {r['f_cyp6']:.3f} |")
    lines += ["",
              "`median gen` is taken over the runs that reached the threshold only; "
              "`resistant runs` says how many those were, so a censored arm is not "
              "quietly credited with a number it never produced.", "",
              "`mean survival` is the fraction living through each treatment, so lower "
              "is better control. Adult numbers are not reported as a control metric: "
              "every arm sits at carrying capacity because the colony rebounds within a "
              "generation, which is itself the reason resistance management rather than "
              "knockdown is the question worth asking.", ""]
    return "\n".join(lines) + "\n"


def plot(comp: Comparison, path: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by = comp.by_strategy()
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    names = list(by)
    peaks = [np.mean([r.peak_target_site for r in by[k]]) for k in names]
    pops = [np.mean([r.mean_survival for r in by[k]]) for k in names]
    err = [np.std([r.peak_target_site for r in by[k]]) for k in names]

    axes[0].barh(names, peaks, xerr=err, color="#a33", alpha=0.85)
    axes[0].axvline(RESISTANCE_THRESHOLD, ls="--", c="k", lw=1)
    axes[0].set_xlabel("peak target-site allele frequency")
    axes[0].set_title("resistance selected")
    axes[1].barh(names, pops, color="#357", alpha=0.85)
    axes[1].set_xlabel("mean survival per treatment (lower is better control)")
    axes[1].set_title("control achieved")
    for a in axes:
        a.grid(axis="x", alpha=0.3)
        a.invert_yaxis()
    fig.suptitle(f"Insecticide deployment strategies, matched on total dose "
                 f"({len(by[names[0]])} seeds, {comp.generations} generations)")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def main(seeds: int = 12, generations: int = 40, out_dir: Path = OUT_DIR) -> int:
    print(f"strategy comparison: {seeds} seeds x {generations} generations")
    comp = compare_strategies(seeds=seeds, generations=generations)
    write_csv(comp, out_dir / "STRATEGY_RUNS.csv")
    (out_dir / "STRATEGY_COMPARISON.md").write_text(report(comp))
    p = plot(comp, out_dir / "strategy_comparison.png")
    print(report(comp))
    print("wrote", out_dir / "STRATEGY_COMPARISON.md", "and", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
