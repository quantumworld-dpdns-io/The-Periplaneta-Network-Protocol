# Parameter provenance

63 named parameters: 53 assumption, 10 literature

| parameter | value | unit | source | sweep | citation / note |
|---|---:|---|---|---|---|
| `aromatic_stacking` | -0.8 | kcal/mol | assumption | -2.0–0.0 | stabilisation when an aromatic residue faces a polarisable ligand |
| `block_time_h` | 8 | h | assumption | 1.0–48.0 | time from exposure to conduction block, for actives that block. Tied to the delayed action that makes gel baits work |
| `burden_midpoint` | 1 | LD50 units | assumption | 0.05–10.0 | internal burden at which the neural effect is half maximal. Below the LD50 the animal is expected to show a signature without dying, which is the regime an experiment would actually work in |
| `carrying_capacity` | 400 | adults | assumption | 100.0–5000.0 | adults the harborage network supports; density regulation acts here |
| `clearance_half_life_h` | 24 | h | assumption | 4.0–96.0 | half-life of the internal burden in a susceptible insect through metabolism and excretion; strongly active-dependent and not pinned |
| `conspecific_radius` | 6 | cm | assumption | 2.0–15.0 | range over which a neighbour is attractive |
| `conspecific_weight` | 0.6 | dimensionless | assumption | 0.0–3.0 | strength of short-range attraction to neighbours |
| `contact_gap` | 0.3 | angstrom | assumption | -0.3–2.0 | separation of the mutated residue and the ligand beyond van der Waals contact in the bound pose. Without a structure this cannot be measured, and the result is sensitive to it |
| `contact_radius` | 2 | cm | assumption | 0.5–5.0 | separation below which two adults count as in contact; of the order of one body length (an adult Blattella germanica is ~1.5 cm) |
| `corpse_persistence_h` | 48 | h | assumption | 6.0–168.0 | how long a corpse remains available to scavengers |
| `corpse_scavenge_rate` | 0.01 | 1/s | assumption | 0.0–0.1 | fraction of a corpse's remaining burden acquired per second by a scavenger in contact with it; necrophagy is a documented route |
| `cuticular_transfer_rate` | 0.002 | 1/s | assumption | 0.0–0.02 | fraction of a donor's burden passed to a recipient per second of contact; horizontal transfer of gel baits is well documented, the per-second efficiency is not |
| `cv_gain` | 1.8 | fold | assumption | 1.0–4.0 | peak increase in ISI coefficient of variation; firing becomes irregular before it stops |
| `dark_emergence_rate` | 0.85 | 1/min | assumption | 0.1–2.0 | hazard rate at which a hungry individual leaves the harborage during scotophase; nocturnality is well documented, the rate is not |
| `ddg_scale` | 0.15 | dimensionless | assumption | 0.02–0.5 | fraction of the model's electronic interaction energy that survives into a binding free energy, standing in for solvation, entropy and the rest of the pocket. Unpinnable without a structure, and the single largest reason this backend is a demonstration |
| `dielectric` | 20 | dimensionless | assumption | 4.0–80.0 | effective dielectric inside the binding pocket; between the protein interior and bulk water |
| `excitation_peak` | 3 | fold | assumption | 1.2–8.0 | peak firing rate relative to baseline at the height of intoxication |
| `excitation_time_h` | 2 | h | assumption | 0.1–12.0 | time from exposure to peak excitation |
| `faecal_shed_fraction` | 0.000277778 | 1/s | assumption | 1.1574074074074073e-05–0.0016666666666666668 | fraction of burden excreted per second, available for coprophagy |
| `faecal_uptake_rate` | 0.05 | 1/s | assumption | 0.0–0.5 | fraction of the local faecal residue ingested per second while resting |
| `feed_rate` | 0.00833333 | 1/s | assumption | 0.0016666666666666668–0.03333333333333333 | rate at which satiety is restored while at a resource |
| `fitness_cost_metabolic` | 0.05 | fraction | assumption | 0.0–0.25 | fitness cost of a metabolic resistance homozygote; generally reported as smaller than target-site costs |
| `fitness_cost_target_site` | 0.1 | fraction | assumption | 0.0–0.35 | reduction in fitness of a target-site homozygote without insecticide. This parameter decides whether rotation can work at all: with no cost, resistance never declines and rotation buys nothing, so the headline result is sensitive to it by construction |
| `forage_speed` | 2.5 | cm/s | assumption | 1.0–6.0 | mean walking speed of a foraging adult; within the range reported for unstartled Blattella germanica locomotion |
| `gel_ingestion_rate` | 0.03 | mg/s | assumption | 0.003–0.3 | gel ingested per second while feeding; a bout of roughly a minute takes up a few milligrams |
| `goal_weight` | 1.5 | dimensionless | assumption | 0.5–4.0 | strength of the bias toward the current goal (resource or harborage) |
| `harborage_radius` | 3 | cm | assumption | 1.0–8.0 | effective radius of a crevice refuge; real harborages are slits, modelled here as a disc of equivalent occupancy area |
| `harborage_switch_prob` | 0.15 | per trip | assumption | 0.0–0.6 | probability that a returning individual settles in the nearest harborage rather than the one it came from. Harborage fidelity is real but imperfect; with fidelity pinned at 1 the groups become disjoint components, insecticide equilibrates inside a group and never crosses to another, and bait coverage rather than horizontal transfer decides the outcome |
| `heading_persistence` | 0.85 | dimensionless | assumption | 0.5–0.97 | autocorrelation of heading between 1 s steps (correlated random walk) |
| `hopping_scale` | 0.55 | eV | assumption | 0.1–1.5 | hopping integral between residue and ligand frontier orbitals at van der Waals contact, before the orbital-spread factor. Sets how much charge transfer the model allows |
| `hunger_rate` | 0.000138889 | 1/s | assumption | 1.1574074074074073e-05–0.0005555555555555556 | rate at which satiety decays; sets how often an animal must forage |
| `hydrophobic_coefficient` | 0.025 | kcal/(mol A^2) | assumption | 0.005–0.05 | free energy per unit buried non-polar surface; the usual range for surface-area models of the hydrophobic effect |
| `intersite_coulomb` | 2.2 | eV | assumption | 0.5–5.0 | nearest-neighbour Coulomb repulsion V between the two sites |
| `juvenile_survival` | 0.25 | fraction | assumption | 0.05–0.6 | fraction of eggs reaching adulthood in the absence of insecticide, absorbing density-independent mortality. Chosen with the carrying capacity so an untreated population is stable rather than exploding |
| `kdr_dominance` | 0.5 | dimensionless | assumption | 0.0–1.0 | dominance of the kdr allele: 0 fully recessive, 1 fully dominant. Reported as incompletely dominant for pyrethroid target-site resistance, but no single value is established |
| `ld50_imidacloprid` | 0.01 | ug/insect | assumption | 0.001–0.1 | no susceptible-strain topical LD50 located for this species; field resistance to imidacloprid baits is documented but the baseline is not pinned. Placeholder of the same order as the other actives, swept until a published value is substituted |
| `light_emergence_rate` | 0.05 | 1/min | assumption | 0.0–0.3 | residual daytime emergence hazard |
| `lj_well_depth` | 0.15 | kcal/mol | assumption | 0.05–0.5 | Lennard-Jones well depth for a residue-ligand contact in the reduced model; of the order of a united-atom carbon parameter |
| `map_length_cM` | 100 | cM | assumption | 50.0–200.0 | genetic length of a chromosome; no linkage map is published for this species, so a typical insect value is used and swept |
| `metabolic_clearance_boost` | 2 | fold per allele copy | assumption | 1.1–6.0 | how much faster a homozygote clears the active. Elevated P450 and esterase activity are well documented; the fold change in whole-organism clearance is not pinned |
| `metabolic_dominance` | 0.5 | dimensionless | assumption | 0.0–1.0 | dominance of a metabolic resistance allele |
| `min_time_to_death_h` | 8 | h | assumption | 1.0–48.0 | floor on time to death however large the dose. Gel baits are deliberately formulated for delayed action so that a poisoned individual returns to the harborage before dying, where its corpse and faeces reach the rest of the colony. Without this floor an overdosed animal dies where it fed and horizontal transfer largely fails -- the toxicodynamic cascade takes time regardless of dose |
| `pheromone_decay` | 0.000277778 | 1/s | assumption | 1.1574074074074073e-05–0.0016666666666666668 | first-order loss of the deposited mark |
| `pheromone_deposit` | 1 | arb/s | assumption | 0.2–5.0 | faecal aggregation-pheromone deposition rate while resting; arbitrary units, only the ratio to decay matters |
| `pheromone_weight` | 1.2 | dimensionless | assumption | 0.0–4.0 | strength of attraction up the pheromone gradient; 0 disables the stigmergic coupling and is used as a control in tests |
| `probit_slope` | 3 | probit/log10(dose) | assumption | 1.5–6.0 | slope of the log-dose probit line; bioassay slopes for this species typically fall in this range, the exact value varies by active and strain |
| `probit_slope` | 3 | probit/log10(dose) | assumption | 1.5–6.0 | as in blattella.toxicology; repeated here so the generational survival step does not silently depend on the fine-scale module |
| `rdl_resistance_ratio` | 10 | fold | assumption | 2.0–200.0 | resistance ratio conferred by the Rdl target-site allele against phenylpyrazoles. No published Blattella germanica value was located, so unlike kdr this effect size is not derived from a measurement and must be swept |
| `repulsion_radius` | 1 | cm | assumption | 0.3–2.0 | volume exclusion: below this separation neighbours push apart. Roughly a body width; without it the colony collapses to a point and every pair is permanently in contact |
| `repulsion_weight` | 2.5 | dimensionless | assumption | 0.5–6.0 | strength of volume exclusion; must exceed attraction at short range or aggregation has no equilibrium spacing |
| `residue_decay` | 1.65344e-06 | 1/s | assumption | 3.8580246913580245e-07–1.1574074074074073e-05 | environmental degradation of deposited residue |
| `resource_radius` | 2 | cm | assumption | 1.0–5.0 | radius within which an individual counts as feeding at a resource |
| `sex_ratio` | 0.5 | fraction female | assumption | 0.4–0.6 | assumed even |
| `bait_concentration` | 0.0215 | mass fraction | literature |  | commercial gel bait formulated at 2.15 % imidacloprid (Maxforce Fusion), used as a representative field concentration |
| `chromosome_pairs` | 21 | pairs | literature |  | Blattella germanica karyotype, 2n = 42 |
| `eggs_per_ootheca` | 30 | eggs | literature |  | an ootheca of Blattella germanica carries about 30 embryos, range 25-43 |
| `generation_days` | 100 | days | literature |  | egg to adult in about 100 days for Blattella germanica |
| `ld50_deltamethrin` | 0.004 | ug/insect | literature |  | topical LD50 for a susceptible Blattella germanica strain, ~0.004 ug/insect |
| `ld50_fipronil` | 0.00133 | ug/insect | literature |  | topical LD50 for a susceptible Blattella germanica strain, 1.33 ng/insect |
| `oothecae_per_female` | 6 | oothecae | literature |  | a female produces 4-8 oothecae in her lifetime; midpoint used |
| `photophase_hours` | 12 | h | literature |  | standard 12:12 LD regime used in Blattella germanica husbandry and assays |
| `pyrethroid_resistance_ratio` | 202 | fold | literature |  | topical LD50 ratio of a field-collected Blattella germanica strain to a susceptible laboratory population for cypermethrin, 202 +/- 33 |
| `resistance_ratio_sd` | 33 | fold | literature |  | standard deviation reported alongside the cypermethrin resistance ratio |

## Assumptions requiring sensitivity analysis

- `harborage_radius` = 3 cm, sweep 1.0–8.0 — effective radius of a crevice refuge; real harborages are slits, modelled here as a disc of equivalent occupancy area
- `resource_radius` = 2 cm, sweep 1.0–5.0 — radius within which an individual counts as feeding at a resource
- `forage_speed` = 2.5 cm/s, sweep 1.0–6.0 — mean walking speed of a foraging adult; within the range reported for unstartled Blattella germanica locomotion
- `heading_persistence` = 0.85 dimensionless, sweep 0.5–0.97 — autocorrelation of heading between 1 s steps (correlated random walk)
- `dark_emergence_rate` = 0.85 1/min, sweep 0.1–2.0 — hazard rate at which a hungry individual leaves the harborage during scotophase; nocturnality is well documented, the rate is not
- `light_emergence_rate` = 0.05 1/min, sweep 0.0–0.3 — residual daytime emergence hazard
- `pheromone_deposit` = 1 arb/s, sweep 0.2–5.0 — faecal aggregation-pheromone deposition rate while resting; arbitrary units, only the ratio to decay matters
- `pheromone_decay` = 0.000277778 1/s, sweep 1.1574074074074073e-05–0.0016666666666666668 — first-order loss of the deposited mark
- `pheromone_weight` = 1.2 dimensionless, sweep 0.0–4.0 — strength of attraction up the pheromone gradient; 0 disables the stigmergic coupling and is used as a control in tests
- `conspecific_weight` = 0.6 dimensionless, sweep 0.0–3.0 — strength of short-range attraction to neighbours
- `conspecific_radius` = 6 cm, sweep 2.0–15.0 — range over which a neighbour is attractive
- `repulsion_radius` = 1 cm, sweep 0.3–2.0 — volume exclusion: below this separation neighbours push apart. Roughly a body width; without it the colony collapses to a point and every pair is permanently in contact
- `repulsion_weight` = 2.5 dimensionless, sweep 0.5–6.0 — strength of volume exclusion; must exceed attraction at short range or aggregation has no equilibrium spacing
- `hunger_rate` = 0.000138889 1/s, sweep 1.1574074074074073e-05–0.0005555555555555556 — rate at which satiety decays; sets how often an animal must forage
- `feed_rate` = 0.00833333 1/s, sweep 0.0016666666666666668–0.03333333333333333 — rate at which satiety is restored while at a resource
- `goal_weight` = 1.5 dimensionless, sweep 0.5–4.0 — strength of the bias toward the current goal (resource or harborage)
- `harborage_switch_prob` = 0.15 per trip, sweep 0.0–0.6 — probability that a returning individual settles in the nearest harborage rather than the one it came from. Harborage fidelity is real but imperfect; with fidelity pinned at 1 the groups become disjoint components, insecticide equilibrates inside a group and never crosses to another, and bait coverage rather than horizontal transfer decides the outcome
- `contact_radius` = 2 cm, sweep 0.5–5.0 — separation below which two adults count as in contact; of the order of one body length (an adult Blattella germanica is ~1.5 cm)
- `probit_slope` = 3 probit/log10(dose), sweep 1.5–6.0 — slope of the log-dose probit line; bioassay slopes for this species typically fall in this range, the exact value varies by active and strain
- `clearance_half_life_h` = 24 h, sweep 4.0–96.0 — half-life of the internal burden in a susceptible insect through metabolism and excretion; strongly active-dependent and not pinned
- `gel_ingestion_rate` = 0.03 mg/s, sweep 0.003–0.3 — gel ingested per second while feeding; a bout of roughly a minute takes up a few milligrams
- `cuticular_transfer_rate` = 0.002 1/s, sweep 0.0–0.02 — fraction of a donor's burden passed to a recipient per second of contact; horizontal transfer of gel baits is well documented, the per-second efficiency is not
- `faecal_shed_fraction` = 0.000277778 1/s, sweep 1.1574074074074073e-05–0.0016666666666666668 — fraction of burden excreted per second, available for coprophagy
- `faecal_uptake_rate` = 0.05 1/s, sweep 0.0–0.5 — fraction of the local faecal residue ingested per second while resting
- `residue_decay` = 1.65344e-06 1/s, sweep 3.8580246913580245e-07–1.1574074074074073e-05 — environmental degradation of deposited residue
- `corpse_scavenge_rate` = 0.01 1/s, sweep 0.0–0.1 — fraction of a corpse's remaining burden acquired per second by a scavenger in contact with it; necrophagy is a documented route
- `corpse_persistence_h` = 48 h, sweep 6.0–168.0 — how long a corpse remains available to scavengers
- `min_time_to_death_h` = 8 h, sweep 1.0–48.0 — floor on time to death however large the dose. Gel baits are deliberately formulated for delayed action so that a poisoned individual returns to the harborage before dying, where its corpse and faeces reach the rest of the colony. Without this floor an overdosed animal dies where it fed and horizontal transfer largely fails -- the toxicodynamic cascade takes time regardless of dose
- `ld50_imidacloprid` = 0.01 ug/insect, sweep 0.001–0.1 — no susceptible-strain topical LD50 located for this species; field resistance to imidacloprid baits is documented but the baseline is not pinned. Placeholder of the same order as the other actives, swept until a published value is substituted
- `lj_well_depth` = 0.15 kcal/mol, sweep 0.05–0.5 — Lennard-Jones well depth for a residue-ligand contact in the reduced model; of the order of a united-atom carbon parameter
- `contact_gap` = 0.3 angstrom, sweep -0.3–2.0 — separation of the mutated residue and the ligand beyond van der Waals contact in the bound pose. Without a structure this cannot be measured, and the result is sensitive to it
- `dielectric` = 20 dimensionless, sweep 4.0–80.0 — effective dielectric inside the binding pocket; between the protein interior and bulk water
- `hydrophobic_coefficient` = 0.025 kcal/(mol A^2), sweep 0.005–0.05 — free energy per unit buried non-polar surface; the usual range for surface-area models of the hydrophobic effect
- `aromatic_stacking` = -0.8 kcal/mol, sweep -2.0–0.0 — stabilisation when an aromatic residue faces a polarisable ligand
- `hopping_scale` = 0.55 eV, sweep 0.1–1.5 — hopping integral between residue and ligand frontier orbitals at van der Waals contact, before the orbital-spread factor. Sets how much charge transfer the model allows
- `intersite_coulomb` = 2.2 eV, sweep 0.5–5.0 — nearest-neighbour Coulomb repulsion V between the two sites
- `ddg_scale` = 0.15 dimensionless, sweep 0.02–0.5 — fraction of the model's electronic interaction energy that survives into a binding free energy, standing in for solvation, entropy and the rest of the pocket. Unpinnable without a structure, and the single largest reason this backend is a demonstration
- `map_length_cM` = 100 cM, sweep 50.0–200.0 — genetic length of a chromosome; no linkage map is published for this species, so a typical insect value is used and swept
- `kdr_dominance` = 0.5 dimensionless, sweep 0.0–1.0 — dominance of the kdr allele: 0 fully recessive, 1 fully dominant. Reported as incompletely dominant for pyrethroid target-site resistance, but no single value is established
- `rdl_resistance_ratio` = 10 fold, sweep 2.0–200.0 — resistance ratio conferred by the Rdl target-site allele against phenylpyrazoles. No published Blattella germanica value was located, so unlike kdr this effect size is not derived from a measurement and must be swept
- `metabolic_dominance` = 0.5 dimensionless, sweep 0.0–1.0 — dominance of a metabolic resistance allele
- `metabolic_clearance_boost` = 2 fold per allele copy, sweep 1.1–6.0 — how much faster a homozygote clears the active. Elevated P450 and esterase activity are well documented; the fold change in whole-organism clearance is not pinned
- `fitness_cost_target_site` = 0.1 fraction, sweep 0.0–0.35 — reduction in fitness of a target-site homozygote without insecticide. This parameter decides whether rotation can work at all: with no cost, resistance never declines and rotation buys nothing, so the headline result is sensitive to it by construction
- `fitness_cost_metabolic` = 0.05 fraction, sweep 0.0–0.25 — fitness cost of a metabolic resistance homozygote; generally reported as smaller than target-site costs
- `juvenile_survival` = 0.25 fraction, sweep 0.05–0.6 — fraction of eggs reaching adulthood in the absence of insecticide, absorbing density-independent mortality. Chosen with the carrying capacity so an untreated population is stable rather than exploding
- `carrying_capacity` = 400 adults, sweep 100.0–5000.0 — adults the harborage network supports; density regulation acts here
- `sex_ratio` = 0.5 fraction female, sweep 0.4–0.6 — assumed even
- `probit_slope` = 3 probit/log10(dose), sweep 1.5–6.0 — as in blattella.toxicology; repeated here so the generational survival step does not silently depend on the fine-scale module
- `excitation_peak` = 3 fold, sweep 1.2–8.0 — peak firing rate relative to baseline at the height of intoxication
- `excitation_time_h` = 2 h, sweep 0.1–12.0 — time from exposure to peak excitation
- `block_time_h` = 8 h, sweep 1.0–48.0 — time from exposure to conduction block, for actives that block. Tied to the delayed action that makes gel baits work
- `cv_gain` = 1.8 fold, sweep 1.0–4.0 — peak increase in ISI coefficient of variation; firing becomes irregular before it stops
- `burden_midpoint` = 1 LD50 units, sweep 0.05–10.0 — internal burden at which the neural effect is half maximal. Below the LD50 the animal is expected to show a signature without dying, which is the regime an experiment would actually work in
