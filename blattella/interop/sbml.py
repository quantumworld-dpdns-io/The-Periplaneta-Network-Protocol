"""
The toxicokinetic core as SBML, for COPASI, SimBiology and PK-Sim.

**What this is.** The agent-based model moves insecticide around by a set of
first-order rates: gel is ingested at a treated station, dosed animals shed
residue where they rest and others eat it, corpses are scavenged, the
environment degrades what is left, and the insect clears its own burden with a
fixed half-life. Those terms really are an ODE system, and this file is that
system written out so another tool can integrate it.

**What this is not, and why it matters more than usual.** SBML's conventions
assert a well-posed deterministic model. Three things the simulation does are
*not* in here, and a reader who does not know that will draw the wrong
conclusion:

* **Direct contact transfer is gone.** In the colony, insecticide rubs between
  individuals during contact. That term is symmetric, so reducing the colony to
  one representative individual makes give and take cancel exactly. The
  mechanism that makes gel baits work -- horizontal transfer along a contact
  network -- cannot survive a mean-field reduction, and dropping it silently
  would misrepresent the whole project.
* **Mortality is not here.** SBML's MathML has no error function, so the probit
  that turns an internal dose into a probability of death cannot be written as a
  kinetic law. The toxicokinetics are complete; the toxicodynamics ship
  separately as `dose_response.csv`. Without this note a COPASI user concludes
  the model is broken.
* **Nothing is stochastic and nothing is spatial.** No arena, no harborages, no
  network.

**Units.** Amounts are micrograms *per insect*, not concentrations. Every
species therefore sets `hasOnlySubstanceUnits`, and the compartments are
declared with zero spatial dimensions: inventing a volume to satisfy the schema
would put a fabricated number inside a machine-readable file, where a tool would
silently compute with it.
"""
from __future__ import annotations

import math

from ..params import Param, Source
from .provenance import prose_block, stamp
from .spec import FormatSpec, FormatUnsupported, register

MODEL_ID = "blattella_wholebody_burden_reduced"     # never "PK" or "PBPK"

WHAT_IT_IS = (
    "The mean-field toxicokinetic core of the agent-based model: ingestion at a "
    "treated bait, coprophagy through a shared residue pool, necrophagy, "
    "environmental decay and first-order metabolic clearance, as an ODE system in "
    "micrograms per insect."
)
WHAT_IT_IS_NOT = (
    "NOT the agent-based model. It has no contact network, no behaviour, no "
    "spatial structure and nothing stochastic. Direct cuticular transfer between "
    "individuals is absent because that term is symmetric and cancels under a "
    "mean-field reduction -- which means the mechanism that makes gel baits work "
    "is precisely what this file cannot show you. NOT a pharmacokinetic or PBPK "
    "model: there is no plasma, no organ, no volume of distribution and no "
    "species scaling; the amounts are micrograms in one insect. Mortality is NOT "
    "included, because SBML has no error function and the dose-response is a "
    "probit; it ships as dose_response.csv."
)


def _need_libsbml():
    try:
        import libsbml
    except ImportError as e:  # pragma: no cover - exercised only without the wheel
        raise FormatUnsupported("toxicokinetics.xml", "python-libsbml") from e
    return libsbml


def _annotate(obj, p: Param, libsbml) -> None:
    """Put a parameter's provenance where it cannot be separated from its value."""
    bits = [f"source: {p.source.value}"]
    if p.cite:
        bits.append(f"citation: {p.cite}")
    if p.sweep:
        bits.append(f"sensitivity sweep: {p.sweep[0]:g} to {p.sweep[1]:g}")
    if p.note:
        bits.append(f"note: {p.note}")
    if p.source is Source.ASSUMPTION:
        bits.append("This value has no published measurement. Substitute your own "
                    "before drawing a quantitative conclusion from it.")
    obj.setNotes(f"<body xmlns='http://www.w3.org/1999/xhtml'><p>"
                 f"{'</p><p>'.join(_escape(b) for b in bits)}</p></body>")


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _notes(obj, text: str) -> None:
    paragraphs = "".join(f"<p>{_escape(p)}</p>" for p in text.split("\n\n") if p.strip())
    obj.setNotes(f"<body xmlns='http://www.w3.org/1999/xhtml'>{paragraphs}</body>")


def toxicokinetic_sbml(*, actives: tuple[str, ...] | None = None,
                       generated: str | None = None) -> bytes:
    libsbml = _need_libsbml()
    from ..toxicology import ACTIVES, P

    names = tuple(actives) if actives else tuple(sorted(ACTIVES))
    for n in names:
        if n not in ACTIVES:
            raise KeyError(f"unknown active {n!r}; have {sorted(ACTIVES)}")

    doc = libsbml.SBMLDocument(3, 2)
    m = doc.createModel()
    m.setId(MODEL_ID)
    m.setName("Blattella germanica whole-body insecticide burden (reduced)")
    m.setTimeUnits("second")
    m.setSubstanceUnits("microgram")
    m.setExtentUnits("microgram")

    block = stamp("toxicokinetics.xml", WHAT_IT_IS, WHAT_IT_IS_NOT,
                  cli="python -m blattella.cli interop --format toxicokinetics.xml",
                  generated=generated)
    _notes(m, prose_block(block))

    # --- units ---------------------------------------------------------------
    ug = m.createUnitDefinition(); ug.setId("microgram")
    u = ug.createUnit(); u.setKind(libsbml.UNIT_KIND_GRAM)
    u.setExponent(1); u.setScale(-6); u.setMultiplier(1.0)
    # `second` and `dimensionless` are predefined in SBML and must not be redeclared
    per_s = m.createUnitDefinition(); per_s.setId("per_second")
    u = per_s.createUnit(); u.setKind(libsbml.UNIT_KIND_SECOND)
    u.setExponent(-1); u.setScale(0); u.setMultiplier(1.0)
    ug_per_s = m.createUnitDefinition(); ug_per_s.setId("microgram_per_second")
    u = ug_per_s.createUnit(); u.setKind(libsbml.UNIT_KIND_GRAM)
    u.setExponent(1); u.setScale(-6); u.setMultiplier(1.0)
    u = ug_per_s.createUnit(); u.setKind(libsbml.UNIT_KIND_SECOND)
    u.setExponent(-1); u.setScale(0); u.setMultiplier(1.0)

    # --- compartments: bookkeeping, not volumes ------------------------------
    for cid, label, note in [
        ("insect", "one representative insect",
         "A single animal, standing for the colony. Zero spatial dimensions: the "
         "amounts inside are micrograms per insect, and no volume exists to divide "
         "by. Inventing one to satisfy the schema would put a fabricated number "
         "into a file a tool computes with."),
        ("environment", "the shared floor and harborages",
         "Where shed faecal residue accumulates and is picked up again. In the "
         "agent-based model this is a spatial grid; here it is one well-mixed pool."),
        ("corpses", "the pool held in dead animals",
         "Corpses keep their burden and are scavenged. Necrophagy is one of the "
         "three horizontal-transfer routes and is the one that survives a "
         "mean-field reduction intact."),
    ]:
        c = m.createCompartment()
        c.setId(cid); c.setName(label)
        c.setConstant(True); c.setSpatialDimensions(0); c.setSize(1.0)
        _notes(c, note)

    def species(sid: str, comp: str, initial: float, note: str):
        s = m.createSpecies()
        s.setId(sid); s.setCompartment(comp)
        s.setInitialAmount(initial); s.setSubstanceUnits("microgram")
        # amounts, never concentrations: there is no volume in this model
        s.setHasOnlySubstanceUnits(True)
        s.setBoundaryCondition(False); s.setConstant(False)
        _notes(s, note)
        return s

    def parameter(pid: str, value: float, units: str, p: Param | None = None,
                  note: str = ""):
        q = m.createParameter()
        q.setId(pid); q.setValue(value); q.setUnits(units); q.setConstant(True)
        if p is not None:
            _annotate(q, p, libsbml)
        elif note:
            _notes(q, note)
        return q

    def reaction(rid: str, reactants, products, formula: str, note: str):
        r = m.createReaction()
        r.setId(rid); r.setReversible(False)
        for sid in reactants:
            ref = r.createReactant(); ref.setSpecies(sid); ref.setStoichiometry(1.0)
            ref.setConstant(True)
        for sid in products:
            ref = r.createProduct(); ref.setSpecies(sid); ref.setStoichiometry(1.0)
            ref.setConstant(True)
        kl = r.createKineticLaw()
        kl.setMath(libsbml.parseL3Formula(formula))
        _notes(r, note)
        return r

    # --- shared rate constants ----------------------------------------------
    parameter("k_ingest", float(P["gel_ingestion_rate"]) * 1000.0 * float(P["bait_concentration"]),
              "microgram_per_second",
              note=f"Active ingested per second while feeding at a treated station: "
                   f"gel_ingestion_rate ({float(P['gel_ingestion_rate']):g} mg/s) x 1000 x "
                   f"bait_concentration ({float(P['bait_concentration']):g} mass fraction). "
                   f"The concentration is a published commercial formulation; the "
                   f"ingestion rate is an assumption swept from "
                   f"{P['gel_ingestion_rate'].sweep[0]:g} to {P['gel_ingestion_rate'].sweep[1]:g} mg/s.")
    parameter("k_shed", float(P["faecal_shed_fraction"]), "per_second", P["faecal_shed_fraction"])
    parameter("k_uptake", float(P["faecal_uptake_rate"]), "per_second", P["faecal_uptake_rate"])
    parameter("k_decay", float(P["residue_decay"]), "per_second", P["residue_decay"])
    parameter("k_scavenge", float(P["corpse_scavenge_rate"]), "per_second", P["corpse_scavenge_rate"])
    parameter("k_clear", math.log(2.0) / (float(P["clearance_half_life_h"]) * 3600.0),
              "per_second",
              note=f"ln(2) / clearance_half_life_h, with the half-life at "
                   f"{float(P['clearance_half_life_h']):g} h. That half-life is an assumption "
                   f"swept from {P['clearance_half_life_h'].sweep[0]:g} to "
                   f"{P['clearance_half_life_h'].sweep[1]:g} h and is strongly active-dependent; "
                   f"the model applies one value to all three.")

    # Behavioural occupancies. In the colony these emerge from movement and the
    # day/night cycle; here they are inputs, and saying so is the point.
    parameter("f_feeding", 0.0, "dimensionless",
              note="Fraction of time the representative insect spends feeding at a "
                   "TREATED station. This is the treatment you are applying: it is 0 "
                   "by default, so an untouched model does nothing. In the agent-based "
                   "model this is not a parameter at all -- it emerges from where the "
                   "animals walk, and a station no harborage is nearest to is never "
                   "visited however much poison is in it.")
    parameter("f_resting", 0.75, "dimensionless",
              note="Fraction of time spent in a harborage, where faeces are shed and "
                   "eaten. Blattella germanica are nocturnal and rest through the "
                   "photophase; the agent-based model produces this rather than "
                   "assuming it.")
    parameter("f_contact", 0.0, "dimensionless",
              note="Fraction of time in contact with a corpse. Zero by default. In the "
                   "colony this follows from the contact network, which is exactly the "
                   "structure a mean-field model discards.")

    # --- per-active species and reactions ------------------------------------
    terms = []
    for name in names:
        ins = ACTIVES[name]
        b, res, corpse = f"burden_{name}", f"residue_{name}", f"corpse_{name}"
        species(b, "insect", 0.0,
                f"Internal burden of {name} in one insect, micrograms. Death occurs in "
                f"the agent-based model when this crosses a probit threshold; that step "
                f"is not representable here.")
        species(res, "environment", 0.0,
                f"{name.capitalize()} lying in the environment as shed faecal residue, "
                f"micrograms.")
        species(corpse, "corpses", 0.0,
                f"{name.capitalize()} still held in corpses and available to scavengers.")
        parameter(f"LD50_{name}", ins.ld50, "microgram", ins.ld50_ug)

        reaction(f"ingestion_{name}", [], [b], f"k_ingest * f_feeding",
                 f"Gel containing {name} ingested at a treated station. Zero-order in "
                 f"burden: what is eaten does not depend on what is already inside.")
        reaction(f"faecal_shed_{name}", [b], [res], f"k_shed * f_resting * {b}",
                 "Residue excreted where the animal rests, becoming available to others.")
        reaction(f"coprophagy_{name}", [res], [b], f"k_uptake * f_resting * {res}",
                 "Faecal residue eaten. One of the three horizontal-transfer routes, and "
                 "one of the two that survive a mean-field reduction.")
        reaction(f"residue_decay_{name}", [res], [], f"k_decay * {res}",
                 "Environmental degradation of deposited residue.")
        reaction(f"necrophagy_{name}", [corpse], [b], f"k_scavenge * f_contact * {corpse}",
                 "A corpse scavenged by a living animal. The other horizontal-transfer "
                 "route that survives the reduction.")
        reaction(f"clearance_{name}", [b], [], f"k_clear * {b}",
                 "Metabolic clearance and excretion, first order with a fixed half-life. "
                 "Resistant genotypes clear faster; genotype is not represented here.")
        terms.append(f"{b} / LD50_{name}")

    # --- the one derived quantity that is pure arithmetic --------------------
    lf = m.createParameter()
    lf.setId("lethal_fraction"); lf.setConstant(False); lf.setUnits("dimensionless")
    _notes(lf, "Internal dose in multiples of the LD50, summed across actives -- the "
               "same quantity the simulation calls lethal_fraction. It is reported "
               "rather than acted on: converting it to mortality needs the normal "
               "distribution function, which SBML's MathML does not provide. Use "
               "dose_response.csv for that step.")
    rule = m.createAssignmentRule()
    rule.setVariable("lethal_fraction")
    rule.setMath(libsbml.parseL3Formula(" + ".join(terms)))

    if doc.checkConsistency() and doc.getNumErrors(libsbml.LIBSBML_SEV_ERROR):
        msgs = [doc.getError(i).getMessage() for i in range(doc.getNumErrors())]
        raise ValueError("refusing to emit inconsistent SBML: " + "; ".join(msgs))
    return libsbml.writeSBMLToString(doc).encode()


register(FormatSpec(
    id="toxicokinetics.xml", title="Toxicokinetic core as SBML",
    spec="SBML Level 3 Version 2 Core",
    spec_url="https://sbml.org/documents/specifications/",
    media_type="application/sbml+xml", filename="blattella_toxicokinetics.xml",
    method="GET", path="/api/interop/toxicokinetics.xml",
    stage="toxicokinetics / systems biology",
    consumers=("COPASI", "MATLAB SimBiology", "PK-Sim / MoBi", "libRoadRunner", "Tellurium"),
    audience=("resistance-management",),
    what_it_is=WHAT_IT_IS,
    what_it_is_not=WHAT_IT_IS_NOT,
    cli="python -m blattella.cli interop --format toxicokinetics.xml",
    render=toxicokinetic_sbml,
    caveats=("Nine of the eleven rate constants are declared assumptions.",
             "Set f_feeding above zero or the model does nothing: it starts untreated."),
))
