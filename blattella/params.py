"""
Parameter provenance.

The project standard is that **every parameter either has a literature source or
is computed**; anything else must be declared an assumption and swept in a
sensitivity analysis. This module makes that standard mechanical rather than a
matter of discipline: a parameter cannot be used without declaring where it came
from, and `provenance_report()` dumps the whole table so any result can be
audited.

    RESTING_FRACTION = Param(
        0.75, "dimensionless",
        source=Source.LITERATURE,
        cite="Blattella germanica are nocturnal and spend the majority of the "
             "photophase inside harborages",
        note="placeholder citation string until the exact reference is pinned",
    )

Rules enforced here:

* `Source.LITERATURE` and `Source.COMPUTED` require a non-empty `cite`.
* `Source.ASSUMPTION` requires a `sweep` range, so the sensitivity analysis has
  something to vary. This is the rule that reconciles "genome-scale model" with
  "every parameter sourced": the parameters that genuinely have no published
  value are not hidden behind a single number, they are declared and scanned.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import ClassVar


class Source(enum.Enum):
    LITERATURE = "literature"   # published measurement; `cite` says where
    COMPUTED = "computed"       # derived in this project (e.g. ΔΔG from chem backend)
    DERIVED = "derived"         # algebra on other Params; `cite` names them
    ASSUMPTION = "assumption"   # no published value; MUST carry a sweep range


@dataclass(frozen=True)
class Param:
    value: float
    unit: str
    source: Source
    cite: str = ""
    note: str = ""
    sweep: tuple[float, float] | None = None
    name: str = ""

    _registry: ClassVar[list["Param"]] = []

    def __post_init__(self) -> None:
        if self.source in (Source.LITERATURE, Source.COMPUTED, Source.DERIVED) and not self.cite:
            raise ValueError(f"{self.source.value} parameter needs a `cite`: {self!r}")
        if self.source is Source.ASSUMPTION:
            if self.sweep is None:
                raise ValueError(
                    f"assumption parameter needs a `sweep` range for sensitivity analysis: {self!r}"
                )
            lo, hi = self.sweep
            if not (lo <= self.value <= hi):
                raise ValueError(f"value {self.value} outside sweep range {self.sweep}: {self!r}")
        Param._registry.append(self)

    def __float__(self) -> float:
        return float(self.value)

    def __int__(self) -> int:
        return int(self.value)

    def __repr__(self) -> str:  # keep registry dumps readable
        n = self.name or "?"
        return f"Param({n}={self.value}{self.unit and ' ' + self.unit}, {self.source.value})"


def named(**kwargs: Param) -> dict[str, Param]:
    """Attach names to a block of parameters declared together."""
    out = {}
    for k, p in kwargs.items():
        out[k] = Param(p.value, p.unit, p.source, p.cite, p.note, p.sweep, name=k)
    return out


def registry(named_only: bool = True) -> list[Param]:
    """
    Declared parameters. `named()` re-wraps each literal to attach its name, so
    the raw registry holds both copies; only the named ones are real parameters.
    """
    return [p for p in Param._registry if p.name or not named_only]


def assumptions(named_only: bool = True) -> list[Param]:
    """Every parameter that must appear in the sensitivity analysis."""
    return [p for p in registry(named_only) if p.source is Source.ASSUMPTION]


def provenance_report() -> str:
    rows = [p for p in Param._registry if p.name]
    by_source: dict[Source, list[Param]] = {}
    for p in rows:
        by_source.setdefault(p.source, []).append(p)
    lines = ["# Parameter provenance", "",
             f"{len(rows)} named parameters: " +
             ", ".join(f"{len(v)} {k.value}" for k, v in sorted(by_source.items(), key=lambda kv: kv[0].value)),
             "", "| parameter | value | unit | source | sweep | citation / note |",
             "|---|---:|---|---|---|---|"]
    for p in sorted(rows, key=lambda p: (p.source.value, p.name)):
        sweep = f"{p.sweep[0]}–{p.sweep[1]}" if p.sweep else ""
        lines.append(f"| `{p.name}` | {p.value:g} | {p.unit} | {p.source.value} | {sweep} | {p.cite or p.note} |")
    a = assumptions()
    if a:
        lines += ["", "## Assumptions requiring sensitivity analysis", ""]
        lines += [f"- `{p.name}` = {p.value:g} {p.unit}, sweep {p.sweep[0]}–{p.sweep[1]}"
                  f"{' — ' + p.note if p.note else ''}" for p in a if p.name]
    return "\n".join(lines) + "\n"
