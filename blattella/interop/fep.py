"""
A free-energy perturbation job specification: the one thing here that is a
request rather than a result.

This project computes a binding free energy shift for the kdr substitution two
ways, and reports honestly that both fail against the measurement. What it
cannot do is the calculation that would settle it -- an alchemical free-energy
run needs a structure, a force field and compute this project does not have.

So the honest move is to hand a group that *can* run it everything they need and
a number to beat: the target and its mutation with real accessions, the ligand
with real identifiers, the thermodynamic cycle, the temperature, the sign
convention, and the literature-derived reference with its uncertainty as the
acceptance criterion.

Four things it must carry or it does harm rather than good, each of them a
mistake this project has either made or nearly made:

* **No structure identifier, because there is not one.** No experimental
  structure of the Blattella germanica sodium channel exists. The field says so
  rather than offering a plausible-looking accession.
* **The numbering.** L993F is the Blattella position; L1014F is the Musca
  domestica numbering for the equivalent site. The plan for this project got
  that wrong once and it is recorded in the phase log.
* **The reference is an upper bound.** It derives from a whole-organism
  resistance ratio, which carries metabolic resistance as well as the
  target-site change. Handed over bare, a group would tune a calculation to a
  target that is too large by the metabolic contribution.
* **The sign convention.** Positive means weaker binding. A sign error in this
  convention invalidated a published conclusion in phase 5 of this project.
"""
from __future__ import annotations

import json

from .provenance import stamp
from .spec import FormatSpec, register
from . import sources

WHAT_IT_IS = (
    "Everything needed to set up an alchemical free-energy calculation for the "
    "kdr substitution against a pyrethroid, together with the measured value it "
    "should reproduce and what this project's own two backends produced."
)
WHAT_IT_IS_NOT = (
    "NOT a result. This project cannot run this calculation: there is no protein "
    "structure in it, no force field for the ligand and no simulation of the "
    "complex. It is a request. The two backend numbers it carries are reported "
    "precisely because both fail against the measurement, one of them on sign."
)


def fep_job_spec(*, mutation: str = "L993F", ligand: str = "deltamethrin",
                 use_vqe: bool = False, generated: str | None = None) -> bytes:
    from ..chem import MUTATIONS, TARGETS
    from ..chem.classical import ClassicalBackend
    from ..chem.compare import compare as chem_compare
    from ..chem.interface import RT_KCAL, TEMPERATURE_K
    from ..chem.quantum import QuantumBackend
    from ..toxicology import ACTIVES

    if mutation not in MUTATIONS:
        raise KeyError(f"unknown mutation {mutation!r}; have {sorted(MUTATIONS)}")
    if ligand not in ACTIVES:
        raise KeyError(f"unknown ligand {ligand!r}; have {sorted(ACTIVES)}")

    mut = MUTATIONS[mutation]
    target = TARGETS[mut.target]
    ins = ACTIVES[ligand]
    result = chem_compare([ClassicalBackend(), QuantumBackend(use_vqe=use_vqe)],
                          mutation=mut, ligand=ligand)
    ref = result["reference"]

    ids = {}
    try:
        from .ligands import _identifiers
        ids = _identifiers().get(ligand, {})
    except Exception:                                  # identifiers are optional here
        ids = {}

    spec = {
        "_provenance": stamp("fep_job.json", WHAT_IT_IS, WHAT_IT_IS_NOT,
                             cli=f"python -m blattella.cli chem --mutation {mutation} "
                                 f"--ligand {ligand}",
                             generated=generated),
        "ask": (
            f"Run an alchemical free-energy calculation of the {mutation} "
            f"substitution in {target.name} with {ligand} bound, and tell us "
            f"whether the target-site contribution to the resistance ratio is as "
            f"large as the whole-organism measurement implies. If it is much "
            f"smaller, the difference is metabolic, and that changes which "
            f"deployment strategy this project should recommend."
        ),
        "target": {
            "name": target.name,
            "gene": target.gene,
            "description": target.description,
            "organism": "Blattella germanica",
            "ncbi_taxon": 6973,
            "uniprot": sources.UNIPROT_ACCESSION,
            "uniprot_length_aa": 2031,
            "structure": None,
            "structure_note": (
                "There is no experimental structure of this channel for this "
                "species. A homology model is required. Deposited structures of "
                "other voltage-gated sodium channels are the usual starting point; "
                "choosing one is part of the work being asked for, which is why no "
                "accession is asserted here."
            ),
        },
        "mutation": {
            "name": mut.name,
            "wild_type": mut.wild,
            "position": mut.position,
            "mutant": mut.mutant,
            "numbering": (
                f"Position {mut.position} is in the Blattella germanica numbering "
                f"of {sources.UNIPROT_ACCESSION}, where residue {mut.position} is "
                f"{mut.wild}. The same site is L1014F under the Musca domestica "
                f"numbering used throughout much of the literature. Confusing the "
                f"two is the most likely way to set this calculation up wrongly."
            ),
            "structural_context": mut.note,
        },
        "ligand": {
            "name": ligand,
            "class": ins.iclass,
            "pubchem_cid": ids.get("pubchem_cid"),
            "inchikey": ids.get("inchikey"),
            "smiles": ids.get("smiles"),
            "molecular_formula": ids.get("molecular_formula"),
            "note": "Identifiers are absent unless ./data/fetch.sh pubchem has run.",
        },
        "calculation": {
            "type": "relative binding free energy (alchemical: FEP, TI or "
                    "non-equilibrium switching)",
            "thermodynamic_cycle": "mutate the residue in the bound complex and in "
                                   "the free protein; the difference of those two "
                                   "legs is the shift in binding free energy",
            "temperature_K": TEMPERATURE_K,
            "RT_kcal_per_mol": round(RT_KCAL, 6),
            "units": "kcal/mol",
            "sign_convention": (
                "POSITIVE means the mutant binds the ligand more weakly, which is "
                "resistance. State this explicitly in anything you send back: a "
                "sign error in this convention invalidated a conclusion in this "
                "project once already."
            ),
            "conversion": "resistance ratio = exp(ddG / RT); ddG = RT * ln(ratio)",
        },
        "acceptance_criterion": {
            "reference_ddg_kcal_per_mol": ref["ddg_kcal_per_mol"],
            "uncertainty": ref["uncertainty"],
            "implied_resistance_ratio": ref["implied_resistance_ratio"],
            "derivation": ref["note"],
            "this_is_an_upper_bound": (
                "This reference is an UPPER BOUND, not a target to hit. It comes "
                "from a whole-organism resistance ratio, so it carries metabolic "
                "resistance (P450, esterase, GST) as well as the target-site "
                "change. The target-site-only shift is therefore SMALLER than this "
                "number. Do not tune a calculation to reproduce "
                "it exactly; reproducing a value below it is the expected outcome, "
                "and how far below is the quantity of interest."
            ),
            "ligand_substitution": (
                "The published ratio is for cypermethrin; this project applies it "
                "to deltamethrin. Both are type II pyrethroids acting on the same "
                "channel, but they are not the same molecule and the substitution "
                "is an assumption."
            ),
        },
        "what_this_project_produced": {
            "backends": result["backends"],
            "honest_summary": (
                "Both backends fail against the reference. The classical one gets "
                "the sign right and the magnitude wrong; the quantum one, a VQE on "
                "a two-site extended Hubbard model, disagrees on sign and is "
                "labelled a demonstration rather than a production path. Neither is "
                "fit to drive the science, which is why the population-genetics "
                "layer uses the measured reference instead."
            ),
        },
        "why_it_matters": (
            "The effect size this number sets is what drives selection at the kdr "
            "locus over a hundred generations, and therefore which deployment "
            "strategy the model recommends. It is the single point where chemistry "
            "enters the population genetics."
        ),
    }
    return json.dumps(spec, indent=1).encode()


register(FormatSpec(
    id="fep_job.json", title="Free-energy calculation request",
    spec="JSON", spec_url="https://www.json.org/",
    media_type="application/json", filename="blattella_fep_job.json",
    method="GET", path="/api/interop/fep_job.json",
    stage="molecular / free energy",
    consumers=("GROMACS", "AMBER (TI)", "Schrodinger FEP+", "OpenMM", "NAMD"),
    audience=("computational-chemistry", "resistance-management"),
    what_it_is=WHAT_IT_IS, what_it_is_not=WHAT_IT_IS_NOT,
    cli="python -m blattella.cli interop --format fep_job.json",
    render=fep_job_spec,
    caveats=("There is no structure of the target in this project; a homology "
             "model is required and choosing one is part of the ask.",
             "The reference is an upper bound: it carries metabolic resistance "
             "as well as the target-site change."),
))
