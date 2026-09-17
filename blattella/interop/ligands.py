"""
The three actives, with identifiers a chemist can resolve.

Until now an insecticide in this project was a name, a class, an LD50 and a
target string. None of that is resolvable: a recipient could not tell which of
several compounds sold as "deltamethrin" we meant, or check that our LD50 is for
the compound they think it is. Attaching a PubChem CID and an InChIKey fixes
that, and an InChIKey has the useful property of being a checksum of the
structure rather than a pointer to a record that might change.

The table degrades rather than failing when nothing has been fetched: the LD50s,
targets, genes and resistance loci are this project's own and are the
scientifically load-bearing part. The identifier columns come back empty with a
per-row `identifier_source` saying so.
"""
from __future__ import annotations

import csv
import io
import json

from .provenance import comment_block, stamp
from .spec import FormatSpec, register
from . import sources

CLI = "python -m blattella.cli interop --format {}"

WHAT_IT_IS = (
    "The three insecticides the model uses: class, molecular target and the gene "
    "behind it, the resistance locus that defends against each, the topical LD50 "
    "for a susceptible strain with its provenance, and PubChem identifiers."
)
WHAT_IT_IS_NOT = (
    "NOT a screening library and NOT lead compounds. These are registered "
    "insecticides chosen because rotation and mixture strategies need something "
    "to rotate, not candidates for development. The LD50s are published values "
    "for Blattella germanica except imidacloprid's, which is a declared "
    "assumption: no susceptible-strain topical LD50 was located for this species."
)


def _identifiers() -> dict[str, dict]:
    """PubChem properties, keyed by our own name for the compound."""
    path = sources.RAW / "pubchem-properties.csv"
    if not path.is_file():
        return {}
    by_cid = {}
    for row in csv.DictReader(io.StringIO(path.read_text())):
        by_cid[int(row["CID"])] = row
    out = {}
    for name, cid in sources.PUBCHEM_CIDS.items():
        row = by_cid.get(cid)
        if row:
            # PubChem's Title is deliberately not used as the name: CID 86418's
            # title is not "Imidacloprid" at all, and an external database must
            # not be able to rename this project's own entities.
            out[name] = {
                "pubchem_cid": cid,
                "inchikey": row.get("InChIKey", ""),
                "smiles": row.get("SMILES") or row.get("CanonicalSMILES", ""),
                "connectivity_smiles": row.get("ConnectivitySMILES", ""),
                "molecular_formula": row.get("MolecularFormula", ""),
                "molecular_weight": row.get("MolecularWeight", ""),
                "pubchem_iupac_name": row.get("IUPACName", ""),
            }
    return out


def _rows() -> tuple[list[dict], bool]:
    from ..chem import TARGETS
    from ..genome import default_loci
    from ..toxicology import ACTIVES

    ident = _identifiers()
    loci = {loc.name: loc for loc in default_loci()}
    out = []
    for name in sorted(ACTIVES):
        ins = ACTIVES[name]
        target = TARGETS.get(ins.target)
        locus = loci.get(ins.resistance_locus)
        ids = ident.get(name, {})
        out.append({
            "name": name,
            "class": ins.iclass,
            "target": ins.target,
            "target_gene": target.gene if target else "",
            "target_description": target.description if target else "",
            "resistance_locus": ins.resistance_locus,
            "resistance_kind": locus.kind if locus else "",
            "ld50_ug_per_insect": ins.ld50,
            "ld50_source": ins.ld50_ug.source.value,
            "ld50_cite": ins.ld50_ug.cite,
            "ld50_sweep_low": ins.ld50_ug.sweep[0] if ins.ld50_ug.sweep else "",
            "ld50_sweep_high": ins.ld50_ug.sweep[1] if ins.ld50_ug.sweep else "",
            "pubchem_cid": ids.get("pubchem_cid", ""),
            "inchikey": ids.get("inchikey", ""),
            "smiles": ids.get("smiles", ""),
            "molecular_formula": ids.get("molecular_formula", ""),
            "molecular_weight": ids.get("molecular_weight", ""),
            "pubchem_iupac_name": ids.get("pubchem_iupac_name", ""),
            "identifier_source": "pubchem" if ids else "not-fetched",
        })
    return out, bool(ident)


def _stamp(fetched: bool, generated: str | None = None) -> dict:
    return stamp(
        "ligands", WHAT_IT_IS, WHAT_IT_IS_NOT, cli=CLI.format("ligands.csv"),
        generated=generated,
        extra={
            "identifiers": "PubChem, resolved by name and verified by InChIKey"
                           if fetched else
                           "NOT FETCHED -- identifier columns are empty. Run "
                           "./data/fetch.sh pubchem. Every other column is "
                           "unaffected: the LD50s, targets and loci are this "
                           "project's own.",
            "name_column": "always this project's name for the compound, never "
                           "PubChem's Title",
        })


def ligand_table(fmt: str = "csv", *, generated: str | None = None) -> bytes:
    rows, fetched = _rows()
    if fmt == "json":
        return json.dumps({"_provenance": _stamp(fetched, generated), "ligands": rows},
                          indent=1).encode()
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    return (comment_block(_stamp(fetched, generated)) + buf.getvalue()).encode()


def ligand_sdf(*, generated: str | None = None) -> bytes:
    """
    The three structures, concatenated, each with the project's own data fields.

    Unlike the table this cannot degrade: an SDF with no structure in it is not
    an SDF.
    """
    sources.require(
        "pubchem", "ligands.sdf",
        "PubChem structures are not committed; data/README.md records the CID and "
        "the InChIKey that verifies each one.",
        "ligands.csv still carries the LD50s, targets and loci, which is the part "
        "that comes from this project rather than from PubChem.")

    from .provenance import prose_block

    rows, fetched = _rows()
    rows = {r["name"]: r for r in rows}
    # the full block on every record: an SDF is routinely split back into single
    # molecules, and a stamp on the first record only would not survive that
    provenance = prose_block(_stamp(fetched, generated))
    out: list[str] = []
    for name, cid in sorted(sources.PUBCHEM_CIDS.items()):
        text = (sources.RAW / f"pubchem-{cid}.sdf").read_text().rstrip()
        body = text[: text.rindex("$$$$")] if "$$$$" in text else text
        r = rows[name]
        fields = {
            "blattella_name": name,
            "blattella_class": r["class"],
            "blattella_target": f'{r["target"]} ({r["target_gene"]})',
            "blattella_resistance_locus": r["resistance_locus"],
            "blattella_ld50_ug_per_insect": r["ld50_ug_per_insect"],
            "blattella_ld50_source": r["ld50_source"],
            "blattella_ld50_cite": r["ld50_cite"] or r["ld50_sweep_low"] and
            f'assumption, swept {r["ld50_sweep_low"]} to {r["ld50_sweep_high"]}',
            "blattella_what_this_is_not": WHAT_IT_IS_NOT,
            "blattella_coordinates": "2D only. No conformer was generated, and this "
                                     "project has no structure of the target to dock "
                                     "into.",
            "blattella_provenance": provenance,
        }
        block = "".join(f"> <{k}>\n{v}\n\n" for k, v in fields.items())
        out.append(f"{body.rstrip()}\n{block}$$$$\n")
    return "".join(out).encode()


register(FormatSpec(
    id="ligands.csv", title="The three actives, with identifiers",
    spec="RFC 4180 CSV", spec_url="https://www.rfc-editor.org/rfc/rfc4180",
    media_type="text/csv", filename="blattella_ligands.csv",
    method="GET", path="/api/interop/ligands.csv",
    stage="chemistry",
    consumers=("RDKit", "OpenBabel", "KNIME", "pandas", "Excel"),
    audience=("resistance-management", "computational-chemistry"),
    what_it_is=WHAT_IT_IS, what_it_is_not=WHAT_IT_IS_NOT,
    cli=CLI.format("ligands.csv"), render=lambda generated=None, **_: ligand_table("csv", generated=generated),
    requires=("pubchem",),
))

register(FormatSpec(
    id="ligands.json", title="The three actives, as JSON",
    spec="JSON", spec_url="https://www.json.org/",
    media_type="application/json", filename="blattella_ligands.json",
    method="GET", path="/api/interop/ligands.json",
    stage="chemistry",
    consumers=("any pipeline", "RDKit", "KNIME"),
    audience=("resistance-management", "computational-chemistry"),
    what_it_is=WHAT_IT_IS, what_it_is_not=WHAT_IT_IS_NOT,
    cli=CLI.format("ligands.json"), render=lambda generated=None, **_: ligand_table("json", generated=generated),
    requires=("pubchem",),
))

register(FormatSpec(
    id="ligands.sdf", title="Structures with the project's own annotations",
    spec="MDL SDfile (V2000)",
    spec_url="https://discover.3ds.com/ctfile-documentation-request-form",
    media_type="chemical/x-mdl-sdfile", filename="blattella_ligands.sdf",
    method="GET", path="/api/interop/ligands.sdf",
    stage="chemistry",
    consumers=("RDKit", "OpenBabel", "AutoDock", "Schrodinger Maestro", "PyMOL"),
    audience=("resistance-management", "computational-chemistry"),
    what_it_is="PubChem 2D structures for the three actives, each annotated with "
               "this project's LD50, target, resistance locus and their provenance.",
    what_it_is_not="2D ONLY. No conformer was generated and none should be inferred: "
                   "there is no structure of the molecular target anywhere in this "
                   "project, so there is nothing here to dock into. " + WHAT_IT_IS_NOT,
    cli=CLI.format("ligands.sdf"), render=lambda generated=None, **_: ligand_sdf(generated=generated),
    requires=("pubchem",),
))
