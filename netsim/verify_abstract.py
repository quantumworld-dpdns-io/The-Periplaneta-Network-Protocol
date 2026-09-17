"""
Check every number quoted in docs/netsim/ABSTRACT.md against netsim/out/summary.csv.

    python -m netsim.verify_abstract            # exit 1 on any mismatch

Each check recomputes a figure from the CSV the same way the abstract text
states it, then asserts the rounded value matches the quoted one. Add a check
here whenever a number is added to the abstract.
"""
from __future__ import annotations

import csv
import math
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "netsim" / "out" / "summary.csv"
ABSTRACT = ROOT / "docs" / "netsim" / "ABSTRACT.md"


def load():
    rows = list(csv.DictReader(SUMMARY.open()))
    for r in rows:
        for k in ("detect_prob", "lead_mean", "lead_sd", "far_heldout", "loc_err_mean", "threshold", "t_symptom_mean"):
            r[k] = float(r[k]) if r[k] not in ("", "nan") else math.nan
        r["n_sensors"] = int(r["n_sensors"])
        r["noise"] = float(r["noise"])
    return rows


def mean(xs):
    xs = [x for x in xs if not math.isnan(x)]
    return st.mean(xs) if xs else math.nan


def cell(rows, **kw):
    out = [r for r in rows if all(r[k] == v for k, v in kw.items())]
    assert len(out) == 1, (kw, len(out))
    return out[0]


def main() -> int:
    rows = load()
    text = ABSTRACT.read_text()
    checks: list[tuple[str, object, object]] = []  # (label, quoted, recomputed)

    def q(label, quoted_in_text: str, value):
        checks.append((label, quoted_in_text, value))
        if quoted_in_text not in text:
            print(f"  ! quoted string not found in abstract: {quoted_in_text!r}")

    n_cells = len({(r["n_sensors"], r["topology"], r["noise"]) for r in rows})
    n_pipes = len({r["pipeline"] for r in rows})
    q("cells", "54 cells × 9 pipelines", (n_cells, n_pipes))
    assert (n_cells, n_pipes) == (54, 9), (n_cells, n_pipes)

    cp = [r for r in rows if r["pipeline"] == "cusum+pool"]
    cm = [r for r in rows if r["pipeline"] == "cusum+max"]
    q("cusum+pool P(det)", "75 % of runs", round(100 * mean(r["detect_prob"] for r in cp)))
    assert round(100 * mean(r["detect_prob"] for r in cp)) == 75
    q("cusum+pool lead", "mean lead\nof 6.8 s", round(mean(r["lead_mean"] for r in cp), 1))
    assert round(mean(r["lead_mean"] for r in cp), 1) == 6.8
    q("cusum+pool loc err", "6.8 % of the domain diagonal", round(100 * mean(r["loc_err_mean"] for r in cp), 1))
    assert round(100 * mean(r["loc_err_mean"] for r in cp), 1) == 6.8
    q("cusum+max P(det)", "against 63 %", round(100 * mean(r["detect_prob"] for r in cm)))
    assert round(100 * mean(r["detect_prob"] for r in cm)) == 63
    q("cusum+max lead", "and 5.4 s", round(mean(r["lead_mean"] for r in cm), 1))
    assert round(mean(r["lead_mean"] for r in cm), 1) == 5.4
    far_lo = min(mean(r["far_heldout"] for r in rows if r["pipeline"] == p) for p in ("cusum+pool", "cusum+max"))
    far_hi = max(mean(r["far_heldout"] for r in rows if r["pipeline"] == p) for p in ("cusum+pool", "cusum+max"))
    q("realised FAR range", "(0.06–0.08)", (round(far_lo, 2), round(far_hi, 2)))
    assert (round(far_lo, 2), round(far_hi, 2)) == (0.06, 0.08), (far_lo, far_hi)

    g16 = cell(rows, n_sensors=16, topology="grid", noise=0.15, pipeline="cusum+pool")
    q("grid 16 detection", "16 sensors reach 95 % detection", g16["detect_prob"])
    assert g16["detect_prob"] == 0.95
    q("grid 16 lead ~7 s", "~7 s lead", round(g16["lead_mean"]))
    assert round(g16["lead_mean"]) == 7
    r36 = cell(rows, n_sensors=36, topology="random", noise=0.15, pipeline="cusum+pool")
    r16 = cell(rows, n_sensors=16, topology="random", noise=0.15, pipeline="cusum+pool")
    q("random needs 36", "placement needs 36", (r16["detect_prob"], r36["detect_prob"]))
    assert r16["detect_prob"] < 0.95 <= r36["detect_prob"]
    cl = {n: cell(rows, n_sensors=n, topology="clustered", noise=0.15, pipeline="cusum+pool")["detect_prob"] for n in (4, 9, 16, 36, 64, 100)}
    q("clustered reaches 80% only at 64", "reaches 80 % only at 64", cl)
    assert all(cl[n] < 0.8 for n in (4, 9, 16, 36)) and cl[64] >= 0.8 and cl[100] < 1.0

    g100 = [r for r in rows if r["n_sensors"] == 100 and r["topology"] == "grid" and r["pipeline"].endswith("+pool")]
    lo, hi = min(r["lead_mean"] for r in g100), max(r["lead_mean"] for r in g100)
    q("100 grid lead 7–13 s", "give 7–13 s", (round(lo), round(hi)))
    assert (round(lo), round(hi)) == (7, 13), (lo, hi)
    t_sym = mean(r["t_symptom_mean"] for r in rows)
    q("theoretical max lead 26 s", "theoretical maximum of 26 s", round(t_sym - 40))
    assert round(t_sym - 40) == 26

    b9 = cell(rows, n_sensors=9, topology="grid", noise=0.15, pipeline="bocpd+max")["detect_prob"]
    b100 = cell(rows, n_sensors=100, topology="grid", noise=0.15, pipeline="bocpd+max")["detect_prob"]
    q("bocpd+max 80% -> 10%", "falls from 80 % detection\nat 9 sensors to 10 % at 100", (b9, b100))
    assert (b9, b100) == (0.80, 0.10)
    bp100 = cell(rows, n_sensors=100, topology="grid", noise=0.15, pipeline="bocpd+pool")["detect_prob"]
    q("bocpd+pool stays 100%", "pooled BOCPD stays at\n100 %", bp100)
    assert bp100 == 1.0

    best16 = {nz: max((r for r in rows if r["n_sensors"] == 16 and r["topology"] == "grid" and r["noise"] == nz),
                      key=lambda r: (r["detect_prob"], r["lead_mean"]))["detect_prob"] for nz in (0.0, 0.15, 0.3)}
    q("grid 16 best >= 95% at all noise", "keeps ≥ 95 % detection at every\nnoise level tested, up to sd 0.30", best16)
    assert min(best16.values()) >= 0.95

    print(f"abstract numbers verified against {SUMMARY.relative_to(ROOT)}:")
    for label, quoted, value in checks:
        print(f"  ok  {label:32s} quoted {quoted.replace(chr(10), ' ')!r:48s} recomputed {value}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print("MISMATCH:", e)
        sys.exit(1)
