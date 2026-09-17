"""
Reference data fetched from public databases.

Nothing under `data/raw/interop/` is committed, following the convention already
used for the spike recordings: `data/fetch.sh` downloads, verifies and records
provenance in `data/README.md`, and the repository stays free of other people's
data.

Fetching happens once, from a script. It never happens while serving a request:
that would make the test suite, the CI run and the container healthcheck depend
on NCBI being reachable and on not being rate-limited, and would make the same
URL return different bytes on different days.
"""
from __future__ import annotations

from pathlib import Path

RAW = Path(__file__).resolve().parents[2] / "data" / "raw" / "interop"

# Accessions verified against the live databases on 2026-09-12. The residue
# check in the test suite is what keeps the UniProt entry honest: L993F is the
# Blattella numbering and L1014F the Musca domestica numbering for the
# equivalent site, and this project has already been bitten by that once.
PUBCHEM_CIDS = {"fipronil": 3352, "deltamethrin": 40585, "imidacloprid": 86287518}
INCHIKEYS = {
    "fipronil": "ZOCSXAVNDGMNBV-UHFFFAOYSA-N",
    "deltamethrin": "OWZREIFADZCYQD-NSHGMRRFSA-N",
    "imidacloprid": "YWTYJOPNNQFBPC-UHFFFAOYSA-N",
}
NCBI_ACCESSION = "AF281328.1"          # Blattella germanica CYP6K1 mRNA, complete cds
NCBI_CDS = (63, 1637)                  # 1-based inclusive, from the record's own FEATURES
UNIPROT_ACCESSION = "O01306"           # Blattella germanica sodium channel, 2031 aa

FETCH = {
    "pubchem": "./data/fetch.sh pubchem",
    "ncbi": "./data/fetch.sh ncbi",
    "uniprot": "./data/fetch.sh uniprot",
}

_EXPECTED = {
    "pubchem": lambda: [RAW / f"pubchem-{cid}.sdf" for cid in PUBCHEM_CIDS.values()]
                       + [RAW / "pubchem-properties.csv"],
    "ncbi": lambda: [RAW / f"{NCBI_ACCESSION}.gb"],
    "uniprot": lambda: [RAW / f"{UNIPROT_ACCESSION}.fasta"],
}


def expected(source: str) -> list[Path]:
    return _EXPECTED[source]()


def missing(source: str) -> list[str]:
    root = RAW.parents[2]
    return [str(p.relative_to(root)) for p in expected(source) if not p.is_file()]


def have(source: str) -> bool:
    return not missing(source)


def require(source: str, format_id: str, why: str, note: str = "") -> None:
    from .spec import DataUnavailable
    gone = missing(source)
    if gone:
        raise DataUnavailable(format_id, gone, FETCH[source], why, note)


def status() -> dict:
    return {
        "fetched": {s: have(s) for s in _EXPECTED},
        "fetch_command": "./data/fetch.sh interop",
        "accessions": {"pubchem": PUBCHEM_CIDS, "ncbi": NCBI_ACCESSION,
                       "uniprot": UNIPROT_ACCESSION},
    }


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")
