# Handoff Guide: The Periplaneta Protocol

## Audiences

### For entomologists and resistance management
This project delivers a mechanistic German cockroach model under insecticide selection. Every observable outcome — colony-wide allele frequency change, field-observed resistance, treatment failure timelines — flows through:

- Two target-site mutations (kdr at Vssc L1014F, rdl at GABA-R A302S)
- Metabolic resistance at cyp6 (CYP6K1), with clearance enhancement
- Esterase background (est, gst)
- Five dose levels covering laboratory assay to field application ranges
- Non-genetic phenotypic modification via RNAi

The model is **deterministic on parameters but stochastic on individuals** — each animal is tracked through contact, ingestion, and mortality. Resistance alleles are inherited. Resistance loci are linked (kdr and cyp6, 5 centiMorgans apart). The network topology (contact pattern) is emergent from behavior rules, not imposed.

**Do not use this for real-world pest control decisions without validation in your local context.** The project was built to understand mechanism, not to forecast field outcomes. Every parameter is an assumption or a measurement in insects kept in the lab. Your insects, your product, your climate are different.

### For CADD / computational chemistry teams
This project is **not** a drug development system. It is a population-level insect toxicology model. It maximizes mortality; human medicine minimizes it. It has no blood, no organs, no clearance rates in L/h/kg, no PK/PD split. A human pharmaceutical PBPK/PD model (GastroPlus, Simcyp) or FEP+ software chain will not be able to ingest most of it.

The **one format meant for CADD integration is `fep_job.json`**, and it is a **request**, not a result: it asks for a calculation this project cannot run (binding free energy of three ligands to a protein target we do not have a structure for). It carries measured reference values and what two backends predicted, so your computational method can be scored against them. The reference value is an upper bound (whole-organism, includes all resistance mechanisms); your binding energy estimate will be lower (protein only).

### For nucleic acid therapeutics
dsRNA and FASTA formats are provided. Both carry **explicit warnings**:
- No delivery mechanism is specified. Where and how to get the construct into insect cells is the user's problem.
- No specificity analysis was performed. There is no BLAST, no off-target screen, no Bowtie alignment. Selection of CYP6K1 is the project's editorial choice, not data-driven.

**Do not treat this as a validated siRNA design.** It is a sequence hand-off only.

---

## The formats

### Sixteen exports, one discovery endpoint

Every format described at `GET /api/interop/formats` (JSON response with machine and human-readable descriptions). The formats carry provenance inside them — in SBML `<notes>` blocks, CSV headers, SDF data fields, GenBank COMMENT, JSON `caveats` arrays, OMEX RDF.

### Parameters and model configuration

| Format | Stage | How to use |
|---|---|---|
| `parameters.csv` | Setup | Literature values and assumptions, with units and references |
| `dose_response.csv` | Setup | LD50 and slope for each active and locus combination |
| `genetic_map.csv` | Setup | Recombination rates between kdr and cyp6 (5 cM) |

### Output tables from a simulated colony run

All require a `POST` with simulation parameters (population size, generations, strategy, dose, etc).

| Format | Stage | What it contains |
|---|---|---|
| `survival.csv` | Live tissue | One row per animal, censored survivors, dose at death (or end of run if alive) |
| `contact_nodes.csv` | Network | One row per animal: ID, alive/dead, degree, harborage, contacts/hour |
| `contact_edges.csv` | Network | One weighted edge per pair of animals; weight = seconds in contact |

### Toxicokinetic model

| Format | Content |
|---|---|
| `toxicokinetics.xml` | SBML L3V2 reduced ODE: ingestion, clearance, mortality vs. dose. **Does not include**: agent-based contact, spatial terms, stochastic effects, or pharmacodynamics (those are in `dose_response.csv`). **Does include**: explicit warnings in `<notes>` and `<constraint>` blocks. |

### Ligands (toxicants)

| Format | Content |
|---|---|
| `ligands.csv` | Name, CAS, SMILES, PubChem CID, InChIKey, measured LD50 for lab strain |
| `ligands.json` | Same data, structured; CI/CD ensures the two are identical |
| `ligands.sdf` | 2D structures from PubChem; **needs** `data/fetch.sh ligands` to be run first; degrades to CSV/JSON if not present |

### Reference sequences

| Format | Content |
|---|---|
| `rnai_target.fasta` | 400 bp CYP6K1 window where paralogues diverge; for primer design or probe synthesis; explicitly states "no specificity analysis" |
| `dsrna_construct.gb` | Same window with T7 flanks for in-vitro transcription; GenBank format for submission to synthesis vendors |

### Free energy perturbation request

| Format | Meaning |
|---|---|
| `fep_job.json` | Request: target = Vssc, mutation = L1014F, ligand = deltamethrin. Carries measured reference value (whole-organism assay, upper bound) and what two quantum/classical backends predicted. Your FEP calculation should outcompete both or improve on the reference. |

### Archive

| Format | Content |
|---|---|
| `bundle.omex` | COMBINE archive (ZIP) of everything above, bundled for sharing. Manifest lists entries; RDF metadata per file; README enumerates parameters with sources |

---

## How to consume each format

### For parameters and dose_response tables (CSV)

```bash
curl localhost:8000/api/interop/parameters.csv > parameters.csv
curl localhost:8000/api/interop/dose_response.csv > dose_response.csv
curl localhost:8000/api/interop/genetic_map.csv > genetic_map.csv
```

Import into your PK simulator or spreadsheet. The `unit` and `source` columns tell you whether the value is measured or assumed.

### For SBML toxicokinetics model

```bash
curl localhost:8000/api/interop/toxicokinetics.xml > model.xml
# Import into COPASI or libsbml
# Read the <notes> block first
```

The model is a reduced system: ingestion → clearance → mortality. All parameters are threaded through `assignment` rules so they reflect the current repository state, not a snapshot. Compartment volumes are set such that amounts (micrograms per insect) are preserved under `hasOnlySubstanceUnits="true"`.

**What is not in the model:**
- No contact network (the ODE is colony-average, not individual)
- No spatial terms (arena geometry, harborage location)
- No error function for mortality (SBML has no erf; use `dose_response.csv` for dose-response curves instead)
- No stochasticity (all rates are deterministic)

### For survival and contact tables (after a POST run)

```bash
curl -X POST http://localhost:8000/api/interop/survival.csv \
  -H "Content-Type: application/json" \
  -d '{"population": 100, "generations": 10, "strategy": "rotation"}' \
  > survival.csv

# Same for contact_edges.csv and contact_nodes.csv
```

Survival table columns:
- `insect_id`: Individual animal ID
- `generation`: When the animal died (or final generation if censored)
- `alive`: 1 = censored (alive at end), 0 = dead
- `dose_at_event`: LD50 multiples at which mortality occurred (or last generation dose if alive)
- Genotype columns: `kdr`, `rdl`, `cyp6`, `est`, `gst` (0/1/2 copies of derived allele)

Use these for validating your own population dynamics model or for plotting empirical survival curves.

### For ligand tables (CSV/JSON/SDF)

```bash
# Fetch reference structures from PubChem first
cd /path/to/repo && ./data/fetch.sh ligands

# Then:
curl localhost:8000/api/interop/ligands.json > ligands.json
curl localhost:8000/api/interop/ligands.sdf > ligands.sdf
```

SDF is hard-fail if not fetched (structure is authoritative). CSV and JSON degrade gracefully and report "fetched: false" if PubChem is unavailable.

### For FEP request (fep_job.json)

```bash
curl localhost:8000/api/interop/fep_job.json > fep_job.json
```

This is a JSON request for a calculation, not a result:

```json
{
  "target": "Vssc",
  "mutation": "L1014F",
  "ligand": "deltamethrin",
  "reference": {
    "ddg_kcal_per_mol": 2.15,
    "uncertainty": 0.3,
    "note": "whole-organism resistance ratio measured in lab strain; upper bound"
  },
  "backends": [
    { "name": "classical-mm", "ddg": 1.67 },
    { "name": "quantum-vqe", "ddg": 3.05 }
  ]
}
```

Your FEP method should predict a ΔΔG for that mutation-ligand pair at that target. The reference is whole-organism (includes all resistance mechanisms, metabolic included); your protein-only calculation will be lower. That gap is biology, not error.

### For sequences (FASTA / GenBank)

```bash
curl localhost:8000/api/interop/rnai_target.fasta > target.fasta
curl localhost:8000/api/interop/dsrna_construct.gb > construct.gb
```

FASTA: 400 bp window of CYP6K1 CDS. Use for primer or probe design.

GenBank: same window with opposing T7 promoter flanks for PCR product transcription. GenBank is suitable for submission to synthesis vendors.

**Both formats explicitly state:** no specificity analysis; no BLAST; no off-target screening performed; selection is editorial.

### For bundle archive (OMEX)

```bash
curl localhost:8000/api/interop/bundle.omex > periplaneta.omex
# Or with a run:
curl -X POST http://localhost:8000/api/interop/bundle.omex \
  -H "Content-Type: application/json" \
  -d '{"population": 100, "generations": 5}' \
  > periplaneta_with_run.omex

unzip periplaneta.omex
# Contents:
#   metadata.rdf         - COMBINE spec URIs and per-file descriptions
#   manifest.xml         - Entry list
#   README.txt           - Parameters with sources and caveats
#   parameters.csv, dose_response.csv, ..., [survival.csv, contact_*.csv if run backed]
```

The archive is byte-deterministic: same build → same hash. Timestamps are fixed; entries are sorted. You can cite it by hash or by the manifest's generated timestamp.

---

## Honest limitations

### Provenance is required

**52 of 63 parameters are assumptions.** The model is sensitive to every one. The PHASE_LOG documents which and why. Before claiming the model predicts anything, you must:

1. Read the `what_it_is_not` statement for each format you use.
2. Check the source of each parameter (`literature` vs. `assumption`).
3. Run a sensitivity sweep on assumptions you care about.

### Nine of the 11 toxicokinetic rate constants are assumptions

The SBML file itself warns you in `<notes>`. They are embedded in `<assignmentRule>` so they update when the source code changes, but that does not make them measured. The nine constants that are assumptions are:

- `k_clear` (clearance half-life: assumed from field observations, not measured in vitro)
- `k_ingest` (ingestion rate: assumed)
- Every LD50 value (dose-response slope: measured in one lab strain, does not transfer)

### No protein structure

There is no X-ray structure, no AlphaFold prediction, no docking. FEP calculations that depend on a structure will fail.

### dsRNA has no delivery

The sequence is provided. Where it goes, how it gets there, whether it reaches the relevant cells — that is outside the scope of this project.

### No clinical relevance

This is an insect model. Do not extrapolate human pharmacokinetics or toxicology from it.

---

## External identifiers

Every reference entity in this project is linked to stable, publicly queryable identifiers:

| Entity | ID | Verifiable at |
|---|---|---|
| Vssc (voltage-sensitive sodium channel) | UniProt O01306 | https://www.uniprot.org/uniprotkb/O01306 |
| CYP6K1 (P450 cluster member) | GenBank AF281328.1 | https://www.ncbi.nlm.nih.gov/nuccore/AF281328.1 |
| Deltamethrin | PubChem CID 40585 | https://pubchem.ncbi.nlm.nih.gov/compound/40585 |
| Fipronil | PubChem CID 3352 | https://pubchem.ncbi.nlm.nih.gov/compound/3352 |
| Imidacloprid | PubChem CID 86287518 | https://pubchem.ncbi.nlm.nih.gov/compound/86287518 |

These identifiers are used in CSV headers, JSON payloads, and SBML `<annotation>` blocks. When you see a reference to kdr, deltamethrin, or Vssc in the output, you can trace it back to authoritative external data.

---

## Questions and feedback

This handoff is a living document. If a format is unclear, if the provenance is insufficient, or if you need something the model does not currently export, open an issue at the repository.

The model is correct but **not complete**: it captures insecticide toxicity and genetic selection, but not spatial heterogeneity, temporal variation in environment, or population-level epidemiology beyond mortality. Every simplification is documented.

---

**Last updated:** 2026-09-12  
**Version:** Phase 18-19
