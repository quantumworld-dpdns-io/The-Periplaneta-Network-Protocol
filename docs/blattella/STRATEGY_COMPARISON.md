# Strategy comparison

5 arms, 12 seeds each, 40 generations, 60% of the colony exposed at a median of 50 susceptible LD50s.

Arms are matched on total insecticide per generation: a mixture of three actives applies each at a third of a dose. Resistance means any target-site allele above 0.5.

| strategy | resistant runs | median gen | peak target-site | mean survival | kdr | rdl | cyp6 |
|---|---|---:|---:|---:|---:|---:|---:|
| single:deltamethrin | 12/12 | 12 | 1.000 | 0.759 | 1.000 | 0.014 | 0.061 |
| rotation:delt-imid-fipr | 3/12 | 28 | 0.351 | 0.417 | 0.312 | 0.009 | 0.001 |
| rotation:delt-imid-fipr/3 | 5/12 | 31 | 0.496 | 0.423 | 0.468 | 0.014 | 0.025 |
| mixture:delt-imid-fipr | 11/12 | 8 | 0.924 | 0.174 | 0.917 | 0.650 | 0.364 |
| untreated | 0/12 | never | 0.067 | 1.000 | 0.007 | 0.011 | 0.032 |

`median gen` is taken over the runs that reached the threshold only; `resistant runs` says how many those were, so a censored arm is not quietly credited with a number it never produced.

`mean survival` is the fraction living through each treatment, so lower is better control. Adult numbers are not reported as a control metric: every arm sits at carrying capacity because the colony rebounds within a generation, which is itself the reason resistance management rather than knockdown is the question worth asking.

