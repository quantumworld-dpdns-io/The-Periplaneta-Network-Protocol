"""
The format registry: one description of every file this project hands out.

Four surfaces need to agree about what can be exported -- the API, the CLI, the
dashboard, and the documentation. Each of them reads this registry rather than
carrying its own list, so they cannot drift apart.

The field that matters most is `what_it_is_not`. A file leaves this project and
lands in software that was written for a different purpose, where the format's
own conventions will assert things the model has not earned: SBML implies a
well-posed ODE system, a GenBank feature table implies a designed construct, a
3D SDF implies a conformer someone can dock. Declaring the negative is how the
provenance discipline in `blattella.params` survives the trip. It is required,
non-empty, and a test enforces it -- the same way `Param.__post_init__` refuses
a literature parameter with no citation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

Availability = Literal["ready", "degraded", "needs_data", "unsupported"]


@dataclass(frozen=True)
class Artifact:
    """One rendered file, ready to be written to disk or put on the wire."""

    id: str
    filename: str
    media_type: str
    data: bytes

    @property
    def text(self) -> str:
        return self.data.decode()

    def __len__(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class FormatSpec:
    id: str                             # "dose_response.csv"
    title: str
    spec: str                           # the standard it conforms to
    spec_url: str
    media_type: str
    filename: str
    method: Literal["GET", "POST"]
    path: str
    stage: str                          # which part of the toolchain it serves
    consumers: tuple[str, ...]          # software that actually reads this
    what_it_is: str
    what_it_is_not: str                 # required; see the module docstring
    cli: str                            # the command that produces the same bytes
    render: Callable[..., bytes]
    requires: tuple[str, ...] = ()      # external data that must be fetched first
    needs_run: bool = False             # render() takes a simulated ColonyRun
    in_bundle: bool = True              # goes inside the archive (the archive does not)
    caveats: tuple[str, ...] = ()
    audience: tuple[str, ...] = ()      # who can honestly use it

    def __post_init__(self) -> None:
        if not self.what_it_is_not.strip():
            raise ValueError(
                f"{self.id}: a format must declare what it is NOT. The receiving "
                f"tool's conventions will claim more than the model earned unless "
                f"the file says otherwise."
            )
        if not self.consumers:
            raise ValueError(f"{self.id}: name at least one tool that would read this")


FORMATS: dict[str, FormatSpec] = {}


def register(spec: FormatSpec) -> FormatSpec:
    if spec.id in FORMATS:
        raise ValueError(f"duplicate format id {spec.id!r}")
    FORMATS[spec.id] = spec
    return spec


class DataUnavailable(RuntimeError):
    """
    Raised when a format needs reference data that has not been fetched.

    Carries the command that fixes it, because an error that does not say how to
    proceed just moves the problem to the reader.
    """

    def __init__(self, format_id: str, missing: list[str], fetch: str, why: str,
                 note: str = "") -> None:
        super().__init__(f"{format_id}: missing {', '.join(missing)}; run: {fetch}")
        self.format_id, self.missing, self.fetch, self.why, self.note = (
            format_id, missing, fetch, why, note)

    def as_detail(self) -> dict:
        return {"error": "reference_data_not_fetched", "format": self.format_id,
                "missing": self.missing, "fetch": self.fetch, "why": self.why,
                "note": self.note}


class FormatUnsupported(RuntimeError):
    """The optional library this format is written with is not installed."""

    def __init__(self, format_id: str, library: str) -> None:
        super().__init__(f"{format_id}: needs {library}, which is not installed")
        self.format_id, self.library = format_id, library


def availability(format_id: str) -> Availability:
    """Whether this format can be rendered right now, and if not, why not."""
    spec = FORMATS[format_id]
    from . import sources

    if spec.requires:
        missing = [r for r in spec.requires if not sources.have(r)]
        if missing:
            # ligand tables still carry the project's own LD50s and loci, which is
            # the scientifically load-bearing part; identifiers are the extra
            return "degraded" if spec.id.startswith("ligands.") and spec.id != "ligands.sdf" \
                else "needs_data"
    return "ready"


def render(format_id: str, **params) -> Artifact:
    spec = FORMATS[format_id]
    return Artifact(id=spec.id, filename=spec.filename, media_type=spec.media_type,
                    data=spec.render(**params))


def discovery() -> dict:
    """The body of `GET /api/interop/formats`: machine-readable and human-readable at once."""
    from .. import __version__
    from ..params import Source, registry
    from . import provenance, sources

    provenance.load_model()
    params = registry()
    return {
        "version": __version__,
        "generated": _utc_now(),
        "audiences": {
            "resistance-management": "Agricultural and public-health entomology, and "
                                     "resistance-management groups. Every format below applies.",
            "computational-chemistry": "Only fep.json, and it is a request rather than a "
                                       "result: it asks for a calculation this project cannot run.",
            "nucleic-acid-therapeutics": "Little of this transfers. The model maximises "
                                         "mortality in the organism it describes; there is no "
                                         "human pharmacokinetics and no clinical pharmacodynamics "
                                         "anywhere in it.",
        },
        "provenance": {
            "report": "/api/export/parameters.md",
            "table": "/api/interop/parameters.csv",
            "literature": sum(1 for p in params if p.source is Source.LITERATURE),
            "assumption": sum(1 for p in params if p.source is Source.ASSUMPTION),
            "total": len(params),
        },
        "reference_data": sources.status(),
        "formats": [
            {
                "id": s.id, "title": s.title, "spec": s.spec, "spec_url": s.spec_url,
                "media_type": s.media_type, "filename": s.filename,
                "method": s.method, "path": s.path, "stage": s.stage,
                "consumers": list(s.consumers), "audience": list(s.audience),
                "what_it_is": s.what_it_is, "what_it_is_not": s.what_it_is_not,
                "caveats": list(s.caveats), "cli": s.cli,
                "requires": list(s.requires), "availability": availability(s.id),
                "needs_run": s.needs_run, "in_bundle": s.in_bundle,
            }
            for s in FORMATS.values()
        ],
    }


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
