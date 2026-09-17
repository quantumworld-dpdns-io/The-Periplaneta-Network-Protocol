"""
Backend comparison.

The rule this module exists to enforce: a ΔΔG from any backend is reported
alongside the literature-derived reference and a statement of its maturity. The
comparison is the deliverable, not a footnote -- what is scarce in quantum life
science is not demonstrations but like-for-like comparisons against a measured
value on a real problem.
"""
from __future__ import annotations

from .interface import (DDGResult, KDR_L993F, Mutation, TARGETS, Target, reference_ddg,
                        resistance_ratio_from_ddg)


def compare(backends, target: Target | None = None, mutation: Mutation = KDR_L993F,
            ligand: str = "deltamethrin") -> dict:
    """Run every backend on the same case and score each against the reference."""
    tgt = target or TARGETS[mutation.target]
    ref = reference_ddg(mutation)
    rows = []
    for b in backends:
        r: DDGResult = b.ddg(tgt, mutation, ligand)
        rows.append({
            **r.as_dict(),
            "error_vs_reference_kcal": round(r.value - ref.value, 4),
            "within_reference_uncertainty": abs(r.value - ref.value) <= ref.uncertainty,
            "resistance_ratio_fold_error": round(
                resistance_ratio_from_ddg(r.value) / resistance_ratio_from_ddg(ref.value), 3),
            # the first thing to check about any backend: does it even agree on
            # whether the mutation confers resistance or sensitivity?
            "sign_agrees_with_reference": (r.value > 0) == (ref.value > 0),
        })
    return {
        "case": {"target": tgt.name, "gene": tgt.gene, "mutation": mutation.name,
                 "ligand": ligand, "note": mutation.note},
        "reference": ref.as_dict(),
        "backends": rows,
    }


def report(result: dict) -> str:
    """Human-readable comparison, for the phase log and the CLI."""
    c, ref = result["case"], result["reference"]
    lines = [f"# ΔΔG comparison: {c['mutation']} in {c['target']} ({c['gene']}) vs {c['ligand']}",
             "", f"{c['note']}", "",
             "Positive ΔΔG means weaker binding, i.e. resistance. "
             "Energies in kcal/mol.", "",
             "| backend | maturity | ΔΔG | ± | implied RR | error vs ref | fold error |",
             "|---|---|---:|---:|---:|---:|---:|",
             f"| **{ref['backend']}** | {ref['maturity']} | {ref['ddg_kcal_per_mol']} | "
             f"{ref['uncertainty']} | {ref['implied_resistance_ratio']} | — | — |"]
    for r in result["backends"]:
        lines.append(f"| {r['backend']} | {r['maturity']} | {r['ddg_kcal_per_mol']} | "
                     f"{r['uncertainty']} | {r['implied_resistance_ratio']} | "
                     f"{r['error_vs_reference_kcal']} | {r['resistance_ratio_fold_error']}x |")
    wrong_sign = [r["backend"] for r in result["backends"] if not r["sign_agrees_with_reference"]]
    lines += [""]
    if wrong_sign:
        lines += [f"**Sign disagreement:** {', '.join(wrong_sign)} disagree with the "
                  f"reference on whether {c['mutation']} confers resistance at all. "
                  f"A backend that cannot get the sign right cannot drive the "
                  f"selection model, whatever its error bar says.", ""]
    lines += [f"Reference: {ref['note']}", ""]
    for r in result["backends"]:
        lines.append(f"- **{r['backend']}** ({r['maturity']}): {r['note']}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    from .classical import ClassicalBackend
    from .quantum import QuantumBackend
    print(report(compare([ClassicalBackend(), QuantumBackend()])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
