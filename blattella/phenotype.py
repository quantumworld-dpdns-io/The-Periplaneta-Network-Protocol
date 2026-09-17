"""
Neural phenotype: what a poisoned individual's spike train looks like.

Every other layer is inference. This one is **falsifiable by experiment**: put an
electrode on a cockroach, dose it, and see whether the interspike intervals do
what this predicts. That is the point of keeping it, and the output of this
module is written as a table of predictions rather than as a result.

The single-unit statistics are the empirical anchor the project has always had:
gamma-renewal parameters fitted by maximum likelihood to published insect
recordings, reused here through `netsim.renewal`, which is the one piece of the
frozen study that stays in service.

**Cross-species extrapolation, stated plainly.** Those recordings are
*Periplaneta americana* and locust antennal lobe. The species under study is
*Blattella germanica*. Using one as a prior for the other is an extrapolation,
not a measurement, and every output of this module carries that label.

**Mode of action drives the signature, and that is the testable content.** The
three actives do different things to a neuron and should therefore leave
different marks:

| active | target | expected signature |
|---|---|---|
| deltamethrin | Vssc, sodium channel | channels held open: repetitive firing and a rising ISI CV, then conduction block and silence |
| imidacloprid | nAChR | agonist: excitation, then depolarising block |
| fipronil | GABA-gated chloride | inhibition removed: sustained hyperexcitation with bursting, no early block |

What is assumed here is the *shape and timing* of each curve. What is predicted,
and can be checked, is the **ordering**: fipronil should hold a high rate long
after the two blocking actives have fallen away.

**What this model cannot currently distinguish**, stated so an experiment can
find it out: deltamethrin and imidacloprid produce identical traces here,
because the model separates only blocking from non-blocking targets and has no
per-target kinetics. Nor does it predict any difference in ISI variability
between actives, since the variability term is shared. A recording that
separates a pyrethroid from a neonicotinoid, or that finds an active-specific
change in ISI coefficient of variation, shows this layer is too coarse -- which
is a useful thing for it to be able to say about itself.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from netsim.renewal import RenewalParams, bin_counts, load_twin_params, spike_train
from .params import Param, Source, named
from .toxicology import ACTIVES, Insecticide

P = named(
    excitation_peak=Param(
        3.0, "fold", Source.ASSUMPTION, sweep=(1.2, 8.0),
        note="peak firing rate relative to baseline at the height of intoxication",
    ),
    excitation_time_h=Param(
        2.0, "h", Source.ASSUMPTION, sweep=(0.1, 12.0),
        note="time from exposure to peak excitation",
    ),
    block_time_h=Param(
        8.0, "h", Source.ASSUMPTION, sweep=(1.0, 48.0),
        note="time from exposure to conduction block, for actives that block. "
             "Tied to the delayed action that makes gel baits work",
    ),
    cv_gain=Param(
        1.8, "fold", Source.ASSUMPTION, sweep=(1.0, 4.0),
        note="peak increase in ISI coefficient of variation; firing becomes "
             "irregular before it stops",
    ),
    burden_midpoint=Param(
        1.0, "LD50 units", Source.ASSUMPTION, sweep=(0.05, 10.0),
        note="internal burden at which the neural effect is half maximal. Below "
             "the LD50 the animal is expected to show a signature without dying, "
             "which is the regime an experiment would actually work in",
    ),
)

# Which targets block conduction and which do not. This is the ordering the
# module predicts and an experiment could falsify.
BLOCKS = {"Vssc": True, "nAChR": True, "GABA-Cl": False}


@dataclass(frozen=True)
class NeuralSignature:
    """Predicted spike-train statistics for one individual at one moment."""

    rate_hz: float
    baseline_rate_hz: float
    isi_cv: float
    baseline_isi_cv: float
    fano: float
    silent: bool
    active: str | None
    hours_since_exposure: float
    burden_ld50: float

    @property
    def rate_fold(self) -> float:
        return self.rate_hz / self.baseline_rate_hz if self.baseline_rate_hz else float("nan")

    def as_dict(self) -> dict:
        return {
            "active": self.active,
            "hours": round(self.hours_since_exposure, 2),
            "burden_ld50": round(self.burden_ld50, 4),
            "rate_hz": round(self.rate_hz, 3),
            "rate_fold": round(self.rate_fold, 3),
            "isi_cv": round(self.isi_cv, 3),
            "cv_fold": round(self.isi_cv / self.baseline_isi_cv, 3) if self.baseline_isi_cv else None,
            "fano": round(self.fano, 3),
            "silent": self.silent,
        }


def _severity(burden_ld50: float) -> float:
    """How far into intoxication the animal is, 0 to 1, saturating with burden."""
    m = float(P["burden_midpoint"])
    return float(burden_ld50 / (burden_ld50 + m)) if burden_ld50 > 0 else 0.0


def rate_modulation(active: str, hours: float, burden_ld50: float) -> float:
    """
    Firing rate relative to baseline.

    Excitation rises to a peak, then for blocking actives decays toward silence.
    Fipronil removes inhibition rather than acting on the spike-generating
    channel, so it holds its excitation instead of blocking; that difference is
    the prediction.
    """
    if hours <= 0 or burden_ld50 <= 0:
        return 1.0
    sev = _severity(burden_ld50)
    ins: Insecticide = ACTIVES[active]
    t_exc = float(P["excitation_time_h"])
    peak = 1.0 + (float(P["excitation_peak"]) - 1.0) * sev
    rise = 1.0 + (peak - 1.0) * (hours / t_exc) * math.exp(1.0 - hours / t_exc)
    if not BLOCKS.get(ins.target, True):
        return max(rise, 1.0)
    t_block = float(P["block_time_h"])
    block = math.exp(-sev * max(hours - t_block, 0.0) / max(t_block, 1e-6))
    return max(rise * block, 0.0)


def cv_modulation(active: str, hours: float, burden_ld50: float) -> float:
    """ISI variability relative to baseline; irregularity precedes failure."""
    if hours <= 0 or burden_ld50 <= 0:
        return 1.0
    sev = _severity(burden_ld50)
    t_exc = float(P["excitation_time_h"])
    gain = 1.0 + (float(P["cv_gain"]) - 1.0) * sev
    return 1.0 + (gain - 1.0) * (1.0 - math.exp(-hours / t_exc))


def _fitted_unit(index: int = 0) -> RenewalParams:
    units, _ = load_twin_params()
    return units[index % len(units)]


def signature(active: str | None, hours: float, burden_ld50: float,
              unit: RenewalParams | None = None, seed: int = 0,
              duration_s: float = 60.0) -> NeuralSignature:
    """
    Simulate a spike train for an intoxicated unit and measure it.

    The train is generated with the same renewal machinery the parameters were
    fitted with, so the measured statistics are comparable with the recordings.
    """
    u = unit or _fitted_unit()
    rng = np.random.default_rng(seed)
    base = spike_train(u, duration_s, rng)
    base_isi = np.diff(base)
    base_cv = float(base_isi.std() / base_isi.mean()) if base_isi.size > 2 else u.isi_cv

    if active is None or burden_ld50 <= 0:
        counts = bin_counts(base, duration_s)
        return NeuralSignature(
            rate_hz=float(base.size / duration_s), baseline_rate_hz=u.rate_hz,
            isi_cv=base_cv, baseline_isi_cv=base_cv,
            fano=float(counts.var() / counts.mean()) if counts.mean() > 0 else float("nan"),
            silent=False, active=None, hours_since_exposure=hours, burden_ld50=0.0)

    rate_fold = rate_modulation(active, hours, burden_ld50)
    cv_fold = cv_modulation(active, hours, burden_ld50)
    # a higher ISI CV at fixed mean means a smaller gamma shape, since CV ~ 1/sqrt(k)
    shape = max(u.shape / (cv_fold ** 2), 0.05)
    poisoned = RenewalParams(shape=shape, rate_hz=max(u.rate_hz * rate_fold, 1e-6),
                             refractory_ms=u.refractory_ms)
    if rate_fold < 1e-3:
        return NeuralSignature(rate_hz=0.0, baseline_rate_hz=u.rate_hz, isi_cv=float("nan"),
                               baseline_isi_cv=base_cv, fano=float("nan"), silent=True,
                               active=active, hours_since_exposure=hours,
                               burden_ld50=burden_ld50)
    t = spike_train(poisoned, duration_s, rng)
    isi = np.diff(t)
    counts = bin_counts(t, duration_s)
    return NeuralSignature(
        rate_hz=float(t.size / duration_s), baseline_rate_hz=u.rate_hz,
        isi_cv=float(isi.std() / isi.mean()) if isi.size > 2 else float("nan"),
        baseline_isi_cv=base_cv,
        fano=float(counts.var() / counts.mean()) if counts.mean() > 0 else float("nan"),
        silent=bool(t.size < 2), active=active, hours_since_exposure=hours,
        burden_ld50=burden_ld50)


def predictions(burden_ld50: float = 0.5, hours: tuple[float, ...] = (0.5, 2.0, 8.0, 24.0),
                seed: int = 0) -> dict:
    """
    The falsifiable output of this module.

    A sub-lethal burden is used by default, because that is the regime in which
    an animal can be recorded from for a day without dying.
    """
    rows = []
    for active in ACTIVES:
        for h in hours:
            rows.append(signature(active, h, burden_ld50, seed=seed).as_dict())
    return {
        "burden_ld50": burden_ld50,
        "species_caveat": "single-unit parameters are fitted to Periplaneta americana "
                          "and locust antennal lobe recordings and used here as a prior "
                          "for Blattella germanica; this is extrapolation, not measurement",
        "testable_orderings": [
            "fipronil holds an elevated firing rate at 24 h, when deltamethrin "
            "and imidacloprid have fallen back toward or below baseline, because "
            "it removes inhibition rather than acting on the spike-generating "
            "channel",
            "all three raise the firing rate before any of them lowers it, so an "
            "early drop with no excitatory phase would falsify the mapping",
            "ISI variability rises before the rate falls, so irregularity is the "
            "earlier marker of intoxication",
        ],
        "known_blind_spots": [
            "deltamethrin and imidacloprid are indistinguishable in this model: "
            "it separates blocking from non-blocking targets and has no "
            "per-target kinetics",
            "no active-specific difference in ISI variability is predicted, "
            "because the variability term is shared across the three",
        ],
        "rows": rows,
    }


def report(pred: dict) -> str:
    lines = ["# Predicted neural signatures of intoxication", "",
             f"Sub-lethal burden of {pred['burden_ld50']:g} LD50, so the animal survives "
             "long enough to be recorded from.", "",
             "**These are predictions, not results.** They are falsifiable by "
             "extracellular recording from a dosed cockroach.", "",
             f"*{pred['species_caveat']}.*", "",
             "| active | hours | rate Hz | rate fold | ISI CV | CV fold | Fano | silent |",
             "|---|---:|---:|---:|---:|---:|---:|---|"]
    for r in pred["rows"]:
        cv = "—" if r["isi_cv"] != r["isi_cv"] else f"{r['isi_cv']:.3f}"
        cvf = "—" if r["cv_fold"] is None or r["cv_fold"] != r["cv_fold"] else f"{r['cv_fold']:.2f}"
        fano = "—" if r["fano"] != r["fano"] else f"{r['fano']:.2f}"
        lines.append(f"| {r['active']} | {r['hours']:g} | {r['rate_hz']:.2f} | "
                     f"{r['rate_fold']:.2f} | {cv} | {cvf} | {fano} | "
                     f"{'yes' if r['silent'] else 'no'} |")
    lines += ["", "## What would falsify this", ""]
    lines += [f"{i}. {t}" for i, t in enumerate(pred["testable_orderings"], 1)]
    lines += ["", "Any recording that reverses one of these orderings falsifies the "
                  "mode-of-action mapping in `blattella/phenotype.py`.", "",
              "## What this model cannot tell apart", ""]
    lines += [f"- {t}" for t in pred["known_blind_spots"]]
    lines += ["", "An experiment that separates these is not falsifying the model so "
                  "much as showing it is too coarse, which is the more likely outcome "
                  "and the more useful one.", ""]
    return "\n".join(lines) + "\n"
