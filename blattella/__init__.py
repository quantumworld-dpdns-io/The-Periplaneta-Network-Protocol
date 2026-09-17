"""
blattella -- agent-based simulation of *Blattella germanica* colonies under
insecticide pressure.

The question this package is built to answer:

    Which deployment strategy for a set of insecticides -- rotation, mixture, or
    single-product use -- best delays the evolution of resistance in a colony
    whose individuals interact?

Layers, built in this order (see .claude/PLAN.md):

    1. behaviour + contact   individuals move, aggregate and touch; the contact
                             network emerges from behaviour rather than being
                             prescribed
    2. toxicology            dose-response, internal burden, horizontal transfer
                             along the contact network
    3. chem                  binding affinity ΔΔG for wild-type vs resistant
                             genotypes, with classical and quantum backends
    4. genome + population   multi-locus inheritance, recombination, selection,
                             allele-frequency trajectories
    5. phenotype             neural spike-train readout of a poisoned individual

Every parameter carries its provenance (see `blattella.params`): a literature
citation, a computation, or an explicit assumption with a sweep range.
"""

__version__ = "0.1.0"
