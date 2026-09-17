"""
One file that carries everything, as a COMBINE archive.

OMEX is a zip with a manifest naming every entry's format and an RDF sidecar
carrying the metadata. It is the systems-biology convention for "here is a whole
modelling project", and using it means a recipient's tooling already knows how
to open this.

**What it deliberately does not claim.** A COMBINE archive normally bundles SBML
with SED-ML so the simulation can be re-executed by a standards-compliant
engine. There is no SED-ML here and there will not be: the thing this project
actually runs is a stochastic, spatial, agent-based model that SED-ML cannot
describe. What the archive carries instead is the reduced ODE core, the tables
cut from real runs, and -- more useful than either -- the exact command line that
reproduces each file, because this project is seed-deterministic and its own
`reproduce` lines are a better reproducibility story than a standard that would
have to be stretched to fit.

The archive is byte-for-byte reproducible: entries are written in sorted order
with a fixed timestamp, so two builds hash identically and the file can be cited
by checksum. The real generation time lives in the metadata, where it belongs.
"""
from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

from .provenance import HEADLINE_CAVEATS, LICENCE, PROJECT, git_sha, utc_now
from .spec import (Artifact, DataUnavailable, FORMATS, FormatSpec, FormatUnsupported,
                   register, render)
from .. import __version__

OMEX_NS = "http://identifiers.org/combine.specifications/omex-manifest"
FORMAT_URI = {
    "application/sbml+xml": "http://identifiers.org/combine.specifications/sbml.level-3.version-2",
    "text/csv": "http://purl.org/NET/mediatypes/text/csv",
    "application/json": "http://purl.org/NET/mediatypes/application/json",
    "chemical/x-mdl-sdfile": "http://purl.org/NET/mediatypes/chemical/x-mdl-sdfile",
    "text/x-fasta": "http://purl.org/NET/mediatypes/text/x-fasta",
    "chemical/seq-na-genbank": "http://purl.org/NET/mediatypes/chemical/seq-na-genbank",
    "text/markdown": "http://purl.org/NET/mediatypes/text/markdown",
}
# where each format sits inside the archive
FOLDER = {
    "toxicokinetics.xml": "model", "parameters.csv": "model",
    "ligands.csv": "chem", "ligands.json": "chem", "ligands.sdf": "chem",
    "fep_job.json": "chem",
    "genetic_map.csv": "genetics", "rnai_target.fasta": "genetics",
    "dsrna_construct.gb": "genetics",
    "dose_response.csv": "data", "survival.csv": "data",
    "contact_edges.csv": "data", "contact_nodes.csv": "data",
}
MASTER = "toxicokinetics.xml"

WHAT_IT_IS = (
    "Every file this project hands out, in one COMBINE archive, with a manifest, "
    "RDF metadata, the full parameter provenance table and a README saying per "
    "file what it is and what it is not."
)
WHAT_IT_IS_NOT = (
    "NOT a re-executable simulation experiment. A COMBINE archive usually carries "
    "SED-ML so a standards-compliant engine can rerun the model; there is none "
    "here, because what this project runs is a stochastic spatial agent-based "
    "model that SED-ML cannot describe. The SBML inside is a reduced ODE core, "
    "not that model. Each file carries the command line that reproduces it "
    "instead, which is the honest version of the same promise."
)


def _entry(path: str, media_type: str, master: bool = False) -> str:
    # the archive's own three entries are named by their COMBINE specification
    # URI rather than by a media type, so pass a URI straight through
    if media_type.startswith("http"):
        fmt = media_type
    else:
        fmt = FORMAT_URI.get(media_type, f"http://purl.org/NET/mediatypes/{media_type}")
    extra = ' master="true"' if master else ""
    return f'  <content location="{escape(path)}" format="{escape(fmt)}"{extra}/>\n'


def _manifest(parts: list[tuple[str, Artifact]]) -> bytes:
    body = [f'<?xml version="1.0" encoding="UTF-8"?>\n<omexManifest xmlns="{OMEX_NS}">\n']
    body.append(_entry(".", "http://identifiers.org/combine.specifications/omex"))
    body.append(_entry("./manifest.xml",
                       "http://identifiers.org/combine.specifications/omex-manifest"))
    body.append(_entry("./metadata.rdf",
                       "http://identifiers.org/combine.specifications/omex-metadata"))
    body.append(_entry("./README.md", "text/markdown"))
    for path, art in parts:
        body.append(_entry(f"./{path}", art.media_type, master=art.id == MASTER))
    body.append("</omexManifest>\n")
    return "".join(body).encode()


def _metadata(parts: list[tuple[str, Artifact]], generated: str, omitted: dict) -> bytes:
    def desc(about: str, inner: str) -> str:
        return f'  <rdf:Description rdf:about="{escape(about)}">\n{inner}  </rdf:Description>\n'

    def dc(tag: str, text: str) -> str:
        return f"    <dcterms:{tag}>{escape(str(text))}</dcterms:{tag}>\n"

    out = ['<?xml version="1.0" encoding="UTF-8"?>\n<rdf:RDF\n'
           '  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"\n'
           '  xmlns:dcterms="http://purl.org/dc/terms/"\n'
           '  xmlns:blattella="https://github.com/quantumworld-dpdns-io/'
           'The-Periplaneta-Protocol#">\n']
    out.append(desc(".", dc("title", f"{PROJECT} interoperability archive")
                    + f"    <blattella:git_commit>{escape(git_sha())}"
                      f"</blattella:git_commit>\n"
                    + dc("description", WHAT_IT_IS)
                    + dc("abstract", WHAT_IT_IS_NOT)
                    + dc("created", generated)
                    + dc("license", LICENCE)
                    + dc("hasVersion", __version__)))
    for path, art in parts:
        spec = FORMATS[art.id]
        inner = (dc("title", spec.title) + dc("description", spec.what_it_is)
                 + dc("abstract", spec.what_it_is_not)
                 + f"    <blattella:consumers>{escape(', '.join(spec.consumers))}"
                   f"</blattella:consumers>\n"
                 + f"    <blattella:reproduce>{escape(spec.cli)}</blattella:reproduce>\n")
        for c in spec.caveats:
            inner += f"    <blattella:caveat>{escape(c)}</blattella:caveat>\n"
        out.append(desc(f"./{path}", inner))
    for fid, why in sorted(omitted.items()):
        out.append(desc(f"#omitted-{fid}",
                        dc("title", f"{fid} is absent from this archive")
                        + dc("description", why)))
    out.append("</rdf:RDF>\n")
    return "".join(out).encode()


def _readme(parts: list[tuple[str, Artifact]], generated: str, omitted: dict) -> bytes:
    from ..params import provenance_report

    lines = [f"# {PROJECT} — interoperability archive", "",
             f"Generated {generated} from git_commit {git_sha()}, "
             f"version {__version__}, {LICENCE} licence.", "",
             WHAT_IT_IS, "", f"**{WHAT_IT_IS_NOT}**", "",
             "## Who can use what", "",
             "This is a **pest-control and resistance-management** model. It "
             "maximises mortality in the organism it describes; there is no human "
             "pharmacokinetics in it and no clinical pharmacodynamics. A group "
             "working on nucleic-acid therapeutics will find only `chem/fep_job.json` "
             "transferable, and that one is a request rather than a result. A group "
             "working on agricultural or public-health entomology, or on resistance "
             "management, can use all of it.", "",
             "## What to be sceptical of", "",
             "These are results this project recorded, not disclaimers it invented. "
             "They are the facts a recipient is least likely to ask for and most "
             "needs.", ""]
    lines += [f"- {c}" for c in HEADLINE_CAVEATS]
    lines += ["", "## What is in here", ""]
    for path, art in parts:
        spec = FORMATS[art.id]
        lines += [f"### `{path}`", "",
                  f"*{spec.title}* — {spec.spec}. Read by {', '.join(spec.consumers)}.", "",
                  f"**What it is.** {spec.what_it_is}", "",
                  f"**What it is not.** {spec.what_it_is_not}", "",
                  f"Reproduce: `{spec.cli}`", ""]
        if spec.caveats:
            lines += ["Caveats:"] + [f"- {c}" for c in spec.caveats] + [""]
    if omitted:
        lines += ["## What is missing, and why", ""]
        lines += [f"- `{fid}`: {why}" for fid, why in sorted(omitted.items())] + [""]
    lines += ["## Parameter provenance", "",
              "Every number in every file above traces back to this table.", "",
              provenance_report()]
    return "\n".join(lines).encode()


def combine_archive(*, run=None, include: list[str] | None = None,
                    generated: str | None = None) -> bytes:
    generated = generated or utc_now()
    # the archive cannot contain itself; `in_bundle` says which formats are
    # contents rather than containers
    wanted = include if include is not None else [f for f, s in FORMATS.items()
                                                  if s.in_bundle]

    parts: list[tuple[str, Artifact]] = []
    omitted: dict[str, str] = {}
    for fid in wanted:
        if fid not in FORMATS:
            raise KeyError(f"unknown format {fid!r}; have {sorted(FORMATS)}")
        spec = FORMATS[fid]
        if spec.needs_run and run is None:
            omitted[fid] = ("needs a simulated colony; POST to this endpoint with a "
                            "colony request body to include it")
            continue
        try:
            # the same timestamp for every member, so the archive is reproducible
            art = (render(fid, run=run, generated=generated) if spec.needs_run
                   else render(fid, generated=generated))
        except DataUnavailable as e:
            # an archive of everything else is still useful; an explicit ask is not
            if include is not None:
                raise
            omitted[fid] = f"reference data not fetched. Run: {e.fetch}"
            continue
        except FormatUnsupported as e:
            if include is not None:
                raise
            omitted[fid] = f"needs the optional library {e.library}, which is absent"
            continue
        parts.append((f"{FOLDER.get(fid, 'data')}/{art.filename}", art))

    parts.sort(key=lambda p: p[0])
    entries = [("manifest.xml", _manifest(parts)),
               ("metadata.rdf", _metadata(parts, generated, omitted)),
               ("README.md", _readme(parts, generated, omitted))]
    entries += [(path, art.data) for path, art in parts]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # fixed timestamps and sorted entries: two builds of the same content hash
        # identically, so the archive can be cited by checksum. The real time is in
        # the metadata, which is where it means something.
        for name, data in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    return buf.getvalue()


register(FormatSpec(
    id="bundle.omex", title="Everything, in one COMBINE archive",
    spec="COMBINE archive (OMEX) version 1",
    spec_url="https://co.mbine.org/standards/omex",
    media_type="application/zip", filename="blattella_interop.omex",
    method="GET", path="/api/interop/bundle.omex",
    stage="the whole handoff",
    consumers=("COPASI", "Tellurium", "any COMBINE-aware tool", "unzip"),
    audience=("resistance-management",),
    what_it_is=WHAT_IT_IS, what_it_is_not=WHAT_IT_IS_NOT,
    cli="python -m blattella.cli interop --bundle --out .",
    render=combine_archive, in_bundle=False,
    caveats=("Contains no SED-ML, so it is not re-executable by a standards "
             "engine; each file carries its own reproduce command instead.",),
))
