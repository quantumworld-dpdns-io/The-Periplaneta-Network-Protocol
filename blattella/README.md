# blattella — colony, contact, toxicology, evolution

**Question.** Which insecticide deployment strategy — rotation, mixture, or
single-product use — best delays the evolution of resistance in a *Blattella
germanica* colony whose individuals interact?

This package is the rebuilt core of the project. The earlier prototype in
`services/`, `ml/`, `dashboard/` and `netsim/` answered a different question
(how many sensors are needed to detect a hazard) and its individuals were
statistically independent of one another. Those parts are frozen; see the repo
README.

## Status

| Phase | Layer | State |
|---|---|---|
| 1 | behaviour + contact network | **done** |
| 2 | toxicology, dose–response, horizontal transfer | **done** |
| 3 | chemistry interface + classical ΔΔG backend | **done** |
| 4 | genome + population genetics | **done** |
| 5 | quantum ΔΔG backend + classical/quantum comparison | **done** |
| 6 | strategy comparison experiment | **done** |
| 7 | neural phenotype readout | **done** |

## What phase 7 adds

The one layer that an experiment can check. `phenotype.py` predicts what the
spike train of a poisoned individual should look like, using the gamma-renewal
parameters fitted to published insect recordings, and states the result as
claims that a recording could falsify:

1. fipronil holds an elevated firing rate at 24 h, when the two blocking actives
   have fallen back toward or below baseline
2. every active raises the rate before any of them lowers it
3. ISI variability rises before the rate falls

It also declares what it cannot see: deltamethrin and imidacloprid are
indistinguishable in it, and no active-specific change in ISI variability is
predicted. An experiment that separates either would show this layer is too
coarse, which is the more likely outcome and the more useful one.

Reproduce with `python -m blattella.cli neural`. Cross-species caveat: the fitted
units are *Periplaneta americana* and locust, used as a prior for *Blattella
germanica*, and every output says so.

## The answer

Twelve seeds, forty generations, arms matched on **total insecticide per
generation**, so a three-way mixture applies each active at a third of a dose.

| strategy | resistant runs | median gen | peak target-site | mean survival |
|---|---|---:|---:|---:|
| single: deltamethrin | 12/12 | 12 | 1.000 | 0.759 |
| rotation, 1 generation each | **3/12** | **28** | **0.351** | 0.417 |
| rotation, 3 generations each | 5/12 | 31 | 0.496 | 0.423 |
| mixture at matched dose | 11/12 | 8 | 0.924 | **0.174** |
| untreated | 0/12 | never | 0.067 | 1.000 |

**Rotation.** It is the only arm that holds target-site resistance below the
threshold in most runs while still killing well.

There is a real trade-off and it is worth stating plainly. The mixture gives the
**best control of any arm**, 83 % killed per treatment, and the **worst
resistance outcome**: it selects kdr, rdl and the P450 locus simultaneously,
because at a third of a dose each active is strong enough to select against its
own susceptible genotypes but too weak to kill the resistant ones. Single-product
use loses on both counts: resistance fixes by generation 12, and the control it
achieves then collapses to worse than rotation's.

Reproduce with `python -m blattella.cli compare`. Full output in
`docs/blattella/STRATEGY_COMPARISON.md`.

## What phase 5 adds

A quantum backend behind the same interface: VQE on a two-site extended Hubbard
model of the residue and ligand frontier orbitals, run on Qiskit's statevector
simulator and validated against exact diagonalisation of the same Hamiltonian.

It is pointed at one specific gap. The classical backend cannot represent charge
transfer between an aromatic residue and a polarisable ligand, so that is what
the quantum model computes, rather than pretending to do the whole binding
problem.

Two claims are kept apart, because conflating them is how quantum results get
oversold:

| claim | verdict |
|---|---|
| the algorithm found the ground state of the Hamiltonian it was given | **yes**, VQE matches exact diagonalisation |
| that Hamiltonian describes the real system | **no**, it gets the sign of the kdr effect wrong |

| backend | maturity | ΔΔG kcal/mol | implied RR | sign |
|---|---|---:|---:|---|
| literature | reference | +3.145 ± 0.097 | 202 | — |
| classical-mm | demonstration | +0.201 | 1.4 | correct, 15× too small |
| quantum-vqe | demonstration | −1.747 ± 2.796 | 0.05 | **wrong** |

The population-genetics layer runs on the measurement. Both backends say so
themselves through their maturity labels.

## What phase 4 adds

Evolution. `genome.py` carries haplotypes across 21 linkage groups (2n = 42, the
published karyotype) with five resistance loci: two target-site (kdr on the
sodium channel, rdl on the GABA channel) and three metabolic (P450, esterase,
GST). Recombination follows Haldane's map function, so linkage is real rather
than assumed away.

`population.py` steps generations: exposure, selection, reproduction under a
fitness cost, density regulation. The target-site effect size is **not invented**
-- it is the ΔΔG from phase 3 read back as a resistance ratio.

Behaviour runs at dt = 1 s and a generation is about 100 days, so the two are
coupled in two stages: an agent-based run is summarised as an `ExposureProfile`,
and the generational layer samples from it.

Over 30 generations at a gel-bait dose:

| strategy | kdr | cyp6 |
|---|---:|---:|
| deltamethrin only | 1.000 | 0.159 |
| rotation of three actives | 0.021 | 0.000 |
| untreated | 0.000 | 0.029 |

## What phase 3 adds

The hinge of the project: `blattella/chem/` turns a resistance mutation into a
binding free energy change, ΔΔG, which shifts the dose-response curve, which
decides who survives, which is the selection pressure.

The reference value is **derived from a measurement, not modelled**. A published
resistance ratio is a measurement of ΔΔG, because a strain needing `RR` times
the dose has a binding free energy shifted by `RT ln(RR)`. For the cypermethrin
ratio of 202 that is 3.14 kcal/mol.

A classical backend sits behind the same interface, and its comparison against
that reference is the deliverable. The result is negative and is kept: the
structure-free model gets the **sign wrong**. See the phase log.

## What phase 2 adds

Three insecticides with different modes of action (deltamethrin on the sodium
channel, imidacloprid on the nicotinic receptor, fipronil on the GABA channel),
a probit dose-response on the internal burden, metabolic clearance, and three
horizontal-transfer routes: contact, coprophagy via a deposited residue field,
and necrophagy from corpses.

Two published topical LD50 values for susceptible strains are in as literature
parameters. No susceptible-strain value was found for imidacloprid, so it is an
explicit assumption with a sweep range rather than a number presented as fact.

The headline result of this phase is a **negative** one, recorded rather than
tuned away: bait coverage dominates, and secondary kill of animals that never
fed is small. See `docs/blattella/PHASE_LOG.md`.

## What phase 1 establishes

Individuals move in an arena of harborages and food sites, cycling
resting → foraging → returning → resting on a 12:12 light cycle. The contact
network is **not prescribed**: it emerges because animals seek the same refuges,
follow the same pheromone marks and feed at the same resources.

Three coupling channels, all documented behaviour:

- **Aggregation pheromone** deposited in harborages and followed up-gradient.
  This is stigmergy: an animal that has gone still steers the others through the
  mark it left.
- **Conspecific attraction** at short range.
- **Volume exclusion** below a body width, which gives the aggregation a finite
  density instead of collapsing to a point.

The property that distinguishes this from the old prototype is asserted
directly:

```
tests/test_behaviour.py::test_removing_an_individual_changes_the_others
```

Delete one animal and the rest end up somewhere else. The control test zeroes
all three coupling weights and shows the colony then decouples exactly, which
proves the divergence comes from interaction and not from random-number drift.

## Run

```bash
python -m blattella.cli describe
python -m blattella.cli contact --n 300 --hours 12 --seed 1
python -m blattella.cli bait --n 200 --hours 24 --bait fipronil --stations 1
python -m blattella.cli chem --out docs/blattella/DDG_COMPARISON.md
python -m blattella.cli evolve --strategy rotation --generations 30
python -m blattella.cli chem --exact          # skip VQE, diagonalise the model directly
python -m blattella.cli compare               # the headline experiment
python -m blattella.cli neural                # falsifiable neural predictions
python -m blattella.cli provenance --out docs/blattella/PARAMETERS.md
pytest -q blattella/tests
```

A 12 h run with 300 individuals takes about 5 minutes and yields a structured
network: density around 0.24, mean degree around 72, several components, and no
isolated individuals.

## Parameter provenance

Every parameter declares where it came from (`blattella/params.py`):

- `LITERATURE` — published measurement, with a citation
- `COMPUTED` — derived here, e.g. a binding energy from the chemistry backend
- `DERIVED` — algebra on other parameters
- `ASSUMPTION` — no published value; **must** carry a sweep range and appear in
  the sensitivity analysis

The class refuses to construct a literature parameter without a citation, or an
assumption without a sweep range. That is the mechanism which reconciles "model
the genome" with "every parameter sourced": parameters that genuinely have no
published value are declared and scanned rather than hidden behind a number.

After phase 4 the tally is 54 named parameters: 10 from literature, 44 assumptions. Reducing that
ratio is a deliverable of later phases, not an afterthought; the current table
is in `docs/blattella/PARAMETERS.md`.

## Known gaps

- Behaviour parameters are mostly assumptions; the calibration pass against
  published locomotion, aggregation and life-history measurements is outstanding.
- The arena is a single 3 m × 3 m room. Multi-room structure, which is what makes
  real infestations hard to clear, is not modelled yet.
- Nymphs, adults and sexes are not distinguished. Life stage matters for bait
  uptake and for horizontal transfer, and arrives with phase 4.
- Contact is proximity only. Trophallaxis, coprophagy and necrophagy are
  distinct transfer routes with different efficiencies and land in phase 2.
