# Predicted neural signatures of intoxication

Sub-lethal burden of 0.5 LD50, so the animal survives long enough to be recorded from.

**These are predictions, not results.** They are falsifiable by extracellular recording from a dosed cockroach.

*single-unit parameters are fitted to Periplaneta americana and locust antennal lobe recordings and used here as a prior for Blattella germanica; this is extrapolation, not measurement.*

| active | hours | rate Hz | rate fold | ISI CV | CV fold | Fano | silent |
|---|---:|---:|---:|---:|---:|---:|---|
| deltamethrin | 0.5 | 8.47 | 1.44 | 1.286 | 1.06 | 1.52 | no |
| deltamethrin | 2 | 10.25 | 1.74 | 1.390 | 1.14 | 2.23 | no |
| deltamethrin | 8 | 7.33 | 1.24 | 1.610 | 1.32 | 2.88 | no |
| deltamethrin | 24 | 2.83 | 0.48 | 1.407 | 1.16 | 2.52 | no |
| imidacloprid | 0.5 | 8.47 | 1.44 | 1.286 | 1.06 | 1.52 | no |
| imidacloprid | 2 | 10.25 | 1.74 | 1.390 | 1.14 | 2.23 | no |
| imidacloprid | 8 | 7.33 | 1.24 | 1.610 | 1.32 | 2.88 | no |
| imidacloprid | 24 | 2.83 | 0.48 | 1.407 | 1.16 | 2.52 | no |
| fipronil | 0.5 | 8.47 | 1.44 | 1.286 | 1.06 | 1.52 | no |
| fipronil | 2 | 10.25 | 1.74 | 1.390 | 1.14 | 2.23 | no |
| fipronil | 8 | 7.33 | 1.24 | 1.610 | 1.32 | 2.88 | no |
| fipronil | 24 | 6.33 | 1.07 | 1.642 | 1.35 | 2.93 | no |

## What would falsify this

1. fipronil holds an elevated firing rate at 24 h, when deltamethrin and imidacloprid have fallen back toward or below baseline, because it removes inhibition rather than acting on the spike-generating channel
2. all three raise the firing rate before any of them lowers it, so an early drop with no excitatory phase would falsify the mapping
3. ISI variability rises before the rate falls, so irregularity is the earlier marker of intoxication

Any recording that reverses one of these orderings falsifies the mode-of-action mapping in `blattella/phenotype.py`.

## What this model cannot tell apart

- deltamethrin and imidacloprid are indistinguishable in this model: it separates blocking from non-blocking targets and has no per-target kinetics
- no active-specific difference in ISI variability is predicted, because the variability term is shared across the three

An experiment that separates these is not falsifying the model so much as showing it is too coarse, which is the more likely outcome and the more useful one.

