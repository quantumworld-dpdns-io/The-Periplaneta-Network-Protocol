# ΔΔG comparison: L993F in Vssc (para) vs deltamethrin

knockdown resistance; domain II S6 helix of the para-type sodium channel

Positive ΔΔG means weaker binding, i.e. resistance. Energies in kcal/mol.

| backend | maturity | ΔΔG | ± | implied RR | error vs ref | fold error |
|---|---|---:|---:|---:|---:|---:|
| **literature** | reference | 3.1451 | 0.0968 | 202.0 | — | — |
| classical-mm | demonstration | 0.2009 | 0.0005 | 1.4 | -2.9441 | 0.007x |
| quantum-vqe | demonstration | -1.7474 | 2.7959 | 0.05 | -4.8925 | 0.0x |

**Sign disagreement:** quantum-vqe disagree with the reference on whether L993F confers resistance at all. A backend that cannot get the sign right cannot drive the selection model, whatever its error bar says.

Reference: RT ln(202) at 298.15 K; whole-organism ratio, so this is an upper bound on the target-site contribution

- **classical-mm** (demonstration): reduced molecular mechanics on tabulated residue properties; no protein structure is used, so this is a stand-in for docking or FEP rather than a substitute for it
- **quantum-vqe** (demonstration): VQE on a two-site extended Hubbard model of the residue and ligand frontier orbitals; not an ab initio calculation of the binding site, and scaled to a free energy by an unpinnable factor
