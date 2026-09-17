#!/usr/bin/env bash
# Cockroach Internet — WS-C data fetcher.
#
#   ./data/fetch.sh            # fetch everything that needs no login
#   ./data/fetch.sh zenodo     # only the open Zenodo spike recordings
#   ./data/fetch.sh dataverse  # only the Harvard Dataverse *metadata*
#   ./data/fetch.sh crcns      # print the manual steps for CRCNS ia-1
#
# Raw downloads land in data/raw/ (gitignored). Nothing here requires a
# login; CRCNS ia-1 does, so we only print instructions for it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW="$ROOT/data/raw"
META="$ROOT/data/raw/meta"
INTEROP="$ROOT/data/raw/interop"
mkdir -p "$RAW" "$META" "$INTEROP"

have() { command -v "$1" >/dev/null 2>&1; }
dl() { # dl <url> <dest>
  local url="$1" dest="$2"
  if [ -s "$dest" ]; then echo "  [skip] $(basename "$dest") already present"; return 0; fi
  echo "  [get ] $(basename "$dest")"
  if have curl; then curl -fSL --retry 3 --connect-timeout 30 -o "$dest.part" "$url"
  elif have wget; then wget -q -O "$dest.part" "$url"
  else echo "need curl or wget" >&2; return 1; fi
  mv "$dest.part" "$dest"
}
check_md5() { # check_md5 <file> <expected>
  local f="$1" want="$2" got=""
  if   have md5sum;    then got="$(md5sum "$f" | awk '{print $1}')"
  elif have md5;       then got="$(md5 -q "$f")"
  else echo "  [md5 ] no md5 tool, skipping"; return 0; fi
  if [ "$got" = "$want" ]; then echo "  [md5 ] ok $(basename "$f")"
  else echo "  [md5 ] MISMATCH for $(basename "$f"): got $got want $want" >&2; return 1; fi
}

# ------------------------------------------------------------- InChIKey -----
# An InChIKey IS a structural checksum, which makes it the right integrity check
# for a chemical structure and the only workable one here: PubChem stamps the
# fetch time into line 2 of every SDF it serves, so the MD5 of the same record
# differs every time you ask for it. Checking MD5 would report a mismatch daily
# and teach everyone to ignore the checker.
check_inchikey() { # check_inchikey <sdf> <expected-key>
  local f="$1" want="$2" got=""
  got="$(awk '/^> <PUBCHEM_IUPAC_INCHIKEY>/{getline; print; exit}' "$f")"
  if [ -z "$got" ]; then echo "  [key ] no InChIKey field in $(basename "$f")" >&2; return 1; fi
  if [ "$got" = "$want" ]; then echo "  [key ] ok $(basename "$f") $got"
  else echo "  [key ] MISMATCH for $(basename "$f"): got $got want $want" >&2; return 1; fi
}

# ---------------------------------------------------------------- PubChem ----
# Structures and identifiers for the three actives the model uses. Public domain
# (PubChem data are not copyrighted; individual depositor records may carry their
# own terms, and these three are from PubChem's own computed collections).
#
# CIDs were resolved by name against PUG REST rather than copied from a search
# result: a web search reports 86418 for imidacloprid, but PubChem's own
# name lookup returns 86287518, and 86418's title is not "Imidacloprid" at all.
#
# 2D, not 3D. PubChem regenerates conformers, any docking tool generates its own,
# and this project has no structure of the target to dock into -- shipping a 3D
# conformer would imply a pose that cannot exist here.
fetch_pubchem() {
  echo "PubChem (PUG REST, no login)"
  mkdir -p "$INTEROP"
  local base="https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound"
  dl "$base/cid/3352,40585,86287518/property/MolecularFormula,SMILES,ConnectivitySMILES,InChIKey,MolecularWeight,IUPACName/CSV" \
     "$INTEROP/pubchem-properties.csv"
  fetch_sdf 3352      "ZOCSXAVNDGMNBV-UHFFFAOYSA-N" fipronil
  fetch_sdf 40585     "OWZREIFADZCYQD-NSHGMRRFSA-N" deltamethrin
  fetch_sdf 86287518  "YWTYJOPNNQFBPC-UHFFFAOYSA-N" imidacloprid
  echo "  -> data/raw/interop/ ready"
}

fetch_sdf() { # fetch_sdf <cid> <inchikey> <name>
  local cid="$1" key="$2" name="$3"
  dl "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/$cid/SDF?record_type=2d" \
     "$INTEROP/pubchem-$cid.sdf"
  check_inchikey "$INTEROP/pubchem-$cid.sdf" "$key" || {
    echo "  [key ] $name did not verify; refusing to keep it" >&2
    rm -f "$INTEROP/pubchem-$cid.sdf"; return 1; }
}

# ------------------------------------------------------------------- NCBI ----
# AF281328.1 -- Blattella germanica cytochrome P450 CYP6K1 mRNA, complete cds,
# 2035 bp, CDS 63..1637, protein AAK57914.1. Wen & Scott (2001) Insect Mol Biol
# 10:131-137, PMID 11422508. GenBank records are public domain.
#
# This is the RNAi target region the interop layer hands over. Unlike a PubChem
# SDF the flat file carries no fetch timestamp, so MD5 is stable and applies.
fetch_ncbi() {
  echo "NCBI GenBank (E-utilities, no login)"
  mkdir -p "$INTEROP"
  dl "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id=AF281328.1&rettype=gb&retmode=text" \
     "$INTEROP/AF281328.1.gb"
  grep -q "CYP6K1" "$INTEROP/AF281328.1.gb" || {
    echo "  [ncbi] the record does not mention CYP6K1; refusing to keep it" >&2
    rm -f "$INTEROP/AF281328.1.gb"; return 1; }
  echo "  -> $INTEROP/AF281328.1.gb"
}

# ---------------------------------------------------------------- UniProt ----
# O01306 -- Blattella germanica sodium channel protein, 2031 aa. The target of
# the pyrethroids and the protein carrying the kdr substitution the chemistry
# layer computes a binding free energy for. CC-BY-4.0.
#
# The test suite asserts that residue 993 of this sequence is a leucine. L993F is
# the Blattella numbering and L1014F the Musca domestica numbering for the
# equivalent site; this project has already been bitten by that confusion once,
# and the check turns it into something a machine verifies.
fetch_uniprot() {
  echo "UniProt (REST, no login)"
  mkdir -p "$INTEROP"
  dl "https://rest.uniprot.org/uniprotkb/O01306.fasta" "$INTEROP/O01306.fasta"
  echo "  -> $INTEROP/O01306.fasta"
}

fetch_interop() { fetch_pubchem; echo; fetch_ncbi; echo; fetch_uniprot; }

# ---------------------------------------------------------------- Zenodo ----
# (a) PRIMARY: locust (Schistocerca americana) antennal lobe tetrode recording.
#     Zenodo 14607 — DOI 10.5281/zenodo.14607 — CC0-1.0 — Pouzat, C. (2015).
#     20 s, 4 channels, 15 kHz, band-passed 300-5000 Hz, int16. 2.3 MB.
# (b) COMPANION: cockroach (Periplaneta americana) first olfactory relay,
#     Zenodo 14281 — DOI 10.5281/zenodo.14281 — CC-BY-4.0 —
#     Pouzat & Chaffiol (2009) J Neurosci Methods 181:119. 1.2 MB.
# Both are raw extracellular traces; WS-C runs its own threshold+refractory
# detector (services/feature-worker/src/features.jl) over them, so no
# spike-sorting toolchain is required.
fetch_zenodo() {
  echo "Zenodo (open access, no login)"
  dl "https://zenodo.org/api/records/14607/files/LocustDemoData.hdf5/content" \
     "$RAW/LocustDemoData.hdf5"
  check_md5 "$RAW/LocustDemoData.hdf5" "4777d507db431f9749cb6e835944b32b"
  dl "https://zenodo.org/api/records/14281/files/CockroachDataJNM_2009_181_119.h5/content" \
     "$RAW/CockroachDataJNM_2009_181_119.h5"
  check_md5 "$RAW/CockroachDataJNM_2009_181_119.h5" "66da60c4821eccb59437f1a82c9381b4"
  dl "https://zenodo.org/api/records/14607" "$META/zenodo-14607.json"
  dl "https://zenodo.org/api/records/14281" "$META/zenodo-14281.json"
  echo "  -> data/raw/*.h5 ready; run: just prepare"
}

# ------------------------------------------------------------- Dataverse ----
# Harvard Dataverse doi:10.7910/DVN/DFHXMM — CC0-1.0 — Erickson, J.C.,
# "Effective stimulus parameters for directed locomotion in Madagascar
# hissing cockroach biobot". METADATA ONLY: the payload is a 665 MB zip of
# motion-tracking runs which we do not need. We keep the JSON record so
# docs/METHODS.md can cite the real antenna micro-stimulation parameters.
fetch_dataverse() {
  echo "Harvard Dataverse (metadata only)"
  dl "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/DFHXMM" \
     "$META/dataverse-DFHXMM.json"
  echo "  -> $META/dataverse-DFHXMM.json (no bulk download; 665 MB zip skipped on purpose)"
}

# ------------------------------------------------------------------ CRCNS ----
# CRCNS ia-1 requires a free account. We deliberately do NOT automate login.
fetch_crcns() {
  cat <<'TXT'
CRCNS ia-1 — grasshopper (Locusta migratoria) auditory receptor cells
  DOI      : 10.6080/K0BG2KWB
  Citation : A. Rokem, S. Watzl, T. Gollisch, M. Stemmler, A.V.M. Herz and
             I. Samengo (2009). Recordings from grasshopper (Locusta
             Migratoria) auditory receptor cells. CRCNS.org.
  Size     : ~46 MB compressed / ~159 MB uncompressed, ASCII spike + stimulus files
  License  : CRCNS data-sharing terms, see https://crcns.org/data-sets/ia/ia-1/conditions

  MANUAL STEPS (a free account is required; do not script the login):
    1. Register at https://crcns.org/register and confirm the email.
    2. Read and accept https://crcns.org/data-sets/ia/ia-1/conditions
    3. Sign in, open https://crcns.org/data-sets/ia/ia-1/ and download the
       ia-1 archive plus crcns-ia1-README.txt.
    4. Unpack into  data/raw/crcns-ia1/   so the README sits at
       data/raw/crcns-ia1/crcns-ia1-README.txt
    5. Re-run `just prepare` — ml/scripts/prepare.jl picks the directory up
       automatically and adds those units to twin_params.json.
  Skipping this step is fine: the pipeline runs on the Zenodo recordings
  (and, failing those, on the labelled synthetic fallback).
TXT
}

for arg in "${@:-all}"; do
  case "$arg" in
    zenodo)    fetch_zenodo ;;
    dataverse) fetch_dataverse ;;
    crcns)     fetch_crcns ;;
    pubchem)   fetch_pubchem ;;
    ncbi)      fetch_ncbi ;;
    uniprot)   fetch_uniprot ;;
    interop)   fetch_interop ;;
    all)       fetch_zenodo; echo; fetch_dataverse; echo; fetch_interop; echo; fetch_crcns ;;
    *) echo "usage: $0 [all|zenodo|dataverse|crcns|interop|pubchem|ncbi|uniprot]" >&2; exit 2 ;;
  esac
done
