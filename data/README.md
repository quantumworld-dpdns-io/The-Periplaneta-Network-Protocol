# Data

Nothing under `data/raw/` or `data/cache/` is committed. `./data/fetch.sh`
downloads the open datasets, verifies their MD5, and prints manual steps for
the one login-gated source. `ml/scripts/prepare.jl` then fits per-unit
gamma-renewal parameters into `ml/artifacts/twin_params.json`, which is the
only derived artifact kept in git.

## Datasets

| Dataset | DOI | Licence | File / MD5 | Used for |
|---|---|---|---|---|
| Cockroach (*Periplaneta americana*) first olfactory relay, extracellular tetrode. Pouzat & Chaffiol (2009) *J Neurosci Methods* 181:119 | 10.5281/zenodo.14281 | CC-BY-4.0 | `CockroachDataJNM_2009_181_119.h5` · `66da60c4821eccb59437f1a82c9381b4` | Spike-train statistics for the digital-twin fit; spike template for the HIL firmware |
| Locust (*Schistocerca americana*) antennal lobe tetrode, 20 s @ 15 kHz. Pouzat (2015) | 10.5281/zenodo.14607 | CC0-1.0 | `LocustDemoData.hdf5` · `4777d507db431f9749cb6e835944b32b` | Additional units for the twin fit |
| Madagascar hissing cockroach biobot stimulus parameters. Erickson, J.C. | 10.7910/DVN/DFHXMM | CC0-1.0 | metadata JSON only (665 MB payload skipped) | Cited in `docs/METHODS.md` for antenna micro-stimulation parameters; not used as training data |
| Grasshopper (*Locusta migratoria*) auditory receptor cells, CRCNS `ia-1`. Rokem et al. (2009) | 10.6080/K0BG2KWB | CRCNS data-sharing terms | manual download, free account required | Optional extra units; the pipeline runs without it |

Spike detection on the raw traces is our own threshold-plus-refractory
detector (`services/feature-worker/src/features.jl`); no spike-sorting
toolchain is required.

## Interop reference data

`./data/fetch.sh interop` pulls the records the handoff layer needs. None is
committed. Each is small, public and needs no login.

| Record | Accession | Licence | File | Integrity | Used for |
|---|---|---|---|---|---|
| *Blattella germanica* sodium channel protein, 2031 aa | UniProt `O01306` | CC-BY-4.0 | `interop/O01306.fasta` | residue 993 asserted to be `L` | The pyrethroid target. Grounds the `KDR_L993F` substitution in a record anyone can check |
| *Blattella germanica* cytochrome P450 CYP6K1 mRNA, complete cds, 2035 bp, CDS 63..1637 | GenBank `AF281328.1` (protein `AAK57914.1`) | public domain | `interop/AF281328.1.gb` | MD5, plus the record must name CYP6K1 | The RNAi target region handed to Benchling, SnapGene, ViennaRNA and BLAST |
| fipronil | PubChem CID `3352` | public domain | `interop/pubchem-3352.sdf` | InChIKey `ZOCSXAVNDGMNBV-UHFFFAOYSA-N` | Structure and identifiers for the ligand table |
| deltamethrin | PubChem CID `40585` | public domain | `interop/pubchem-40585.sdf` | InChIKey `OWZREIFADZCYQD-NSHGMRRFSA-N` | as above |
| imidacloprid | PubChem CID `86287518` | public domain | `interop/pubchem-86287518.sdf` | InChIKey `YWTYJOPNNQFBPC-UHFFFAOYSA-N` | as above |
| identifiers for all three | — | public domain | `interop/pubchem-properties.csv` | MD5 | SMILES, InChIKey, formula, molecular weight, IUPAC name |

Three things about these that are easy to get wrong:

**An InChIKey is the integrity check for a structure, not an MD5.** PubChem
stamps the fetch time into line 2 of every SDF it serves, so the same record
hashes differently every day. An InChIKey *is* a structural checksum, which is
both stable and the chemically meaningful thing to verify. `check_inchikey()`
sits alongside `check_md5()` in `fetch.sh`, and a structure that fails it is
deleted rather than kept.

**The CIDs were resolved by name against PubChem, not copied from a search.** A
web search reports `86418` for imidacloprid; PubChem's own name lookup returns
`86287518`, and `86418`'s title is not "Imidacloprid" at all. The `name` column
in every exported table is always this project's own name for the compound, with
PubChem's title kept separately, so an external database cannot silently rename
our entities.

**2D structures, not 3D.** PubChem regenerates conformers, any docking tool
generates its own, and this project has no structure of the target to dock into.
Shipping a 3D conformer would imply a pose that cannot exist here.

Verified on 2026-09-12. Nothing is fetched while serving a request: that would
make the test suite, CI and the container healthcheck depend on NCBI being
reachable and on not being rate-limited, and would make the same URL return
different bytes on different days.

## Provenance in derived artifacts

`ml/artifacts/twin_params.json` records `source`, the unit count per dataset
and `fitted_at`. Per unit it stores `shape`, `rate_hz` (the gamma **rate
parameter**, 1/θ), `refractory_ms` and `mean_rate_hz` (the **firing rate**); see
`docs/netsim/FIT_CHECK.md` for the independent Python refit that checks it.
Licences were re-verified on 2026-09-10 from the Zenodo API records
(`data/raw/meta/zenodo-*.json`): 14281 `cc-by-4.0`, 14607 `cc-zero`. The current committed fit reads
`zenodo:10.5281/zenodo.14281 (cockroach AL, n=12) + zenodo:10.5281/zenodo.14607 (locust AL, n=4)`.

If no real dataset is on disk, `ml/src/dataio.jl` writes and loads a
**clearly labelled synthetic** unit set (`data/cache/synthetic_units.csv`,
`source = "SYNTHETIC ..."`). Every downstream report prints that label; do not
present synthetic-sourced numbers as measured.

## Ethics

All sources are published, openly licensed invertebrate recordings. No live
animals are used anywhere in this repository; see `docs/FUTURE.md` item 6 for
the review a live-animal extension would require.

## Commands

```bash
./data/fetch.sh            # Zenodo files + Dataverse metadata + CRCNS instructions
./data/fetch.sh zenodo     # only the two open recordings
just train                 # needs Julia; writes ml/artifacts/gru.bson (gitignored)
just template              # needs Julia; regenerates firmware/hil-node/include/template.h from the recordings
```
