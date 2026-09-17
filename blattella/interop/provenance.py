"""
Provenance that travels inside the file.

A caveat in a README is a caveat that gets stripped. Every format this package
emits carries its provenance in that format's own metadata channel -- CSV
comment lines, JSON keys, SBML notes, a GenBank COMMENT, SDF data fields -- so
the statement cannot be separated from the numbers by forwarding the file.

`stamp()` is the common block. It answers the four questions a recipient needs
before they can use anything here: what produced it, from what commit, what it
claims, and what it does not.
"""
from __future__ import annotations

import csv
import io
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from ..params import Source, registry

_ROOT = Path(__file__).resolve().parents[2]

LICENCE = "MIT"
PROJECT = "The Periplaneta Protocol"

# The facts a recipient most needs and is least likely to ask for. Every one of
# these is a result the project recorded rather than a disclaimer it invented.
HEADLINE_CAVEATS = (
    "Ground truth is simulated. The empirical inputs are the LD50s, the published "
    "resistance ratio behind the reference binding free energy, and the spike-train "
    "statistics. Everything else is a declared assumption with a sensitivity range.",
    "This is a pest-control model. It maximises mortality in the organism it "
    "describes. There is no human pharmacokinetics and no clinical pharmacodynamics.",
    "Both binding-free-energy backends fail against the measured reference; the "
    "quantum one also disagrees on sign. The reference is used for the science.",
    "Metabolic resistance is inert at gel-bait doses in this model: a twofold "
    "clearance boost buys nothing against a median dose of 50 LD50s.",
    "Secondary kill is small here -- horizontal transfer moves insecticide, but "
    "rarely enough to kill an animal that never fed.",
)


def git_sha() -> str:
    try:
        out = subprocess.run(["git", "-C", str(_ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_model() -> None:
    """
    Import every module that declares parameters.

    `Param` registers itself on construction, so the registry is only complete
    once the modules holding the declarations have been imported. Reading it
    without this returns an empty table and reports zero assumptions, which is
    the most flattering possible lie about this model.
    """
    from .. import arena, behaviour, contact, genome, phenotype, population, toxicology  # noqa: F401
    from ..chem import classical, interface, quantum  # noqa: F401


def counts() -> dict[str, int]:
    load_model()
    params = registry()
    return {s.value: sum(1 for p in params if p.source is s) for s in Source} | {
        "total": len(params)}


def stamp(format_id: str, what_it_is: str, what_it_is_not: str, *,
          cli: str = "", extra: dict | None = None,
          generated: str | None = None) -> dict:
    """The provenance block, as data. Each writer renders it in its own syntax."""
    c = counts()
    out = {
        "project": PROJECT,
        "format": format_id,
        "version": __version__,
        "git_commit": git_sha(),
        "generated": generated or utc_now(),
        "licence": LICENCE,
        "what_it_is": what_it_is,
        "what_it_is_not": what_it_is_not,
        "parameter_provenance": {"literature": c.get("literature", 0),
                                 "computed": c.get("computed", 0),
                                 "derived": c.get("derived", 0),
                                 "assumption": c.get("assumption", 0),
                                 "total": c["total"]},
        "caveats": list(HEADLINE_CAVEATS),
    }
    if cli:
        out["reproduce"] = cli
    if extra:
        out.update(extra)
    return out


def comment_block(block: dict, prefix: str = "# ") -> str:
    """The stamp as commented lines, for CSV and other line-oriented formats."""
    lines: list[str] = []

    def emit(key: str, value) -> None:
        if isinstance(value, dict):
            lines.append(f"{key}:")
            for k, v in value.items():
                lines.append(f"  {k}: {v}")
        elif isinstance(value, list):
            lines.append(f"{key}:")
            for v in value:
                lines.append(f"  - {v}")
        else:
            lines.append(f"{key}: {value}")

    for k, v in block.items():
        emit(k, v)
    return "".join(f"{prefix}{ln}\n" for ln in lines)


_FIXED = ("project", "format", "version", "git_commit", "generated", "licence",
          "what_it_is", "what_it_is_not", "parameter_provenance", "caveats", "reproduce")


def prose_block(block: dict) -> str:
    """
    The stamp as plain paragraphs, for a GenBank COMMENT or SBML notes.

    Anything a caller put in `extra` is rendered too. An earlier version listed
    only the fixed keys and silently dropped the rest, which quietly lost the one
    instruction that mattered most on the dsRNA record: screen for off-targets
    before synthesising anything.
    """
    p = block["parameter_provenance"]
    parts = [
        f"{block['project']} -- {block['format']}",
        f"Generated {block['generated']} from commit {block['git_commit']} "
        f"(version {block['version']}, {block['licence']} licence).",
        f"WHAT THIS IS: {block['what_it_is']}",
        f"WHAT THIS IS NOT: {block['what_it_is_not']}",
    ]
    for key, value in block.items():
        if key in _FIXED:
            continue
        label = key.replace("_", " ").upper()
        if isinstance(value, dict):
            value = "; ".join(f"{k}={v}" for k, v in value.items())
        parts.append(f"{label}: {value}")
    parts.append(
        f"Parameter provenance: {p['literature']} from literature, {p['computed']} computed, "
        f"{p['derived']} derived, {p['assumption']} declared assumptions, {p['total']} total.")
    if block.get("reproduce"):
        parts.append(f"Reproduce: {block['reproduce']}")
    parts.append("Caveats: " + " ".join(block["caveats"]))
    return "\n\n".join(parts)


def params_csv(*, generated: str | None = None) -> bytes:
    """
    The provenance table, machine-readable.

    `provenance_report()` renders the same information for people. Until now
    there was no version a script could read, which made every other artifact
    unauditable without a human in the loop.
    """
    head = comment_block(stamp(
        "parameters.csv",
        "Every declared parameter in the model with its source, citation and, "
        "for assumptions, the range over which it is swept.",
        "NOT a calibrated parameter set. The assumption rows have no published "
        "value; their sweep range is the honest statement of what is unknown.",
        cli="python -m blattella.cli interop --format parameters.csv",
        generated=generated,
    ))
    load_model()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "value", "unit", "source", "cite", "sweep_low", "sweep_high", "note"])
    for p in sorted(registry(), key=lambda p: (p.source.value, p.name)):
        lo, hi = p.sweep if p.sweep else ("", "")
        w.writerow([p.name, p.value, p.unit, p.source.value, p.cite, lo, hi, p.note])
    return (head + buf.getvalue()).encode()
