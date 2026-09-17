"""
Genome: chromosomes, resistance loci, and inheritance.

*Blattella germanica* has 2n = 42, so 21 linkage groups, and a published
assembly (Bger_2.0, GCA_000762945.2, i5k). Loci are carried as **haplotypes**
rather than genotype counts, because linkage is the point: alleles on the same
chromosome do not assort independently, and whether two resistance loci are
linked changes how fast a rotation strategy loses ground.

Two kinds of resistance locus, because they act through different physiology and
the model must not conflate them:

* **target-site** -- the drug's binding site changes, so more drug is needed.
  The effect size comes from `blattella.chem` as a ΔΔG, converted to a
  resistance ratio. Locus-specific: kdr protects against pyrethroids only.
* **metabolic** -- the insect clears the drug faster (P450, esterase, GST).
  The effect is on clearance rather than binding, it is quantitative rather than
  all-or-nothing, and it is typically cross-resistant across chemistries. This
  is why metabolic resistance is the harder problem for a rotation strategy.

What is real here: the chromosome count, the identity and mechanism of the loci,
and the target-site effect size. What is assumed: map positions, dominance, and
fitness costs. Every one of those carries a sweep range.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .chem import KDR_L993F, reference_ddg, resistance_ratio_from_ddg
from .params import Param, Source, named

N_CHROMOSOME_PAIRS = 21          # 2n = 42; enforced in Genome.__post_init__

P = named(
    chromosome_pairs=Param(
        21, "pairs", Source.LITERATURE,
        cite="Blattella germanica karyotype, 2n = 42",
    ),
    map_length_cM=Param(
        100.0, "cM", Source.ASSUMPTION, sweep=(50.0, 200.0),
        note="genetic length of a chromosome; no linkage map is published for "
             "this species, so a typical insect value is used and swept",
    ),
    kdr_dominance=Param(
        0.5, "dimensionless", Source.ASSUMPTION, sweep=(0.0, 1.0),
        note="dominance of the kdr allele: 0 fully recessive, 1 fully dominant. "
             "Reported as incompletely dominant for pyrethroid target-site "
             "resistance, but no single value is established",
    ),
    rdl_resistance_ratio=Param(
        10.0, "fold", Source.ASSUMPTION, sweep=(2.0, 200.0),
        note="resistance ratio conferred by the Rdl target-site allele against "
             "phenylpyrazoles. No published Blattella germanica value was located, "
             "so unlike kdr this effect size is not derived from a measurement and "
             "must be swept",
    ),
    metabolic_dominance=Param(
        0.5, "dimensionless", Source.ASSUMPTION, sweep=(0.0, 1.0),
        note="dominance of a metabolic resistance allele",
    ),
    metabolic_clearance_boost=Param(
        2.0, "fold per allele copy", Source.ASSUMPTION, sweep=(1.1, 6.0),
        note="how much faster a homozygote clears the active. Elevated P450 and "
             "esterase activity are well documented; the fold change in whole-"
             "organism clearance is not pinned",
    ),
    fitness_cost_target_site=Param(
        0.10, "fraction", Source.ASSUMPTION, sweep=(0.0, 0.35),
        note="reduction in fitness of a target-site homozygote without "
             "insecticide. This parameter decides whether rotation can work at "
             "all: with no cost, resistance never declines and rotation buys "
             "nothing, so the headline result is sensitive to it by construction",
    ),
    fitness_cost_metabolic=Param(
        0.05, "fraction", Source.ASSUMPTION, sweep=(0.0, 0.25),
        note="fitness cost of a metabolic resistance homozygote; generally "
             "reported as smaller than target-site costs",
    ),
)

TARGET_SITE, METABOLIC = "target-site", "metabolic"


@dataclass(frozen=True)
class Locus:
    name: str
    chromosome: int
    position_cM: float
    kind: str
    # target-site loci protect against one target; metabolic loci act on actives
    target: str | None = None
    actives: tuple[str, ...] = ()
    note: str = ""


def default_loci() -> tuple[Locus, ...]:
    """
    The resistance loci modelled, and why each is here.

    Placed on separate chromosomes by default: no linkage map is published for
    this species, so asserting linkage would be inventing data. `linked_loci`
    builds a variant with two loci on one chromosome for the sensitivity check.
    """
    return (
        Locus("kdr", 1, 20.0, TARGET_SITE, target="Vssc",
              note="Vssc L993F, domain II S6; pyrethroid target-site resistance"),
        Locus("rdl", 2, 55.0, TARGET_SITE, target="GABA-Cl",
              note="GABA-gated chloride channel; phenylpyrazole target-site resistance"),
        Locus("cyp6", 3, 30.0, METABOLIC,
              actives=("deltamethrin", "imidacloprid", "fipronil"),
              note="cytochrome P450 cluster; the predominant field mechanism and "
                   "cross-resistant across chemistries"),
        Locus("est", 4, 70.0, METABOLIC, actives=("deltamethrin", "fipronil"),
              note="esterase-mediated sequestration and hydrolysis"),
        Locus("gst", 5, 40.0, METABOLIC, actives=("deltamethrin",),
              note="glutathione S-transferase"),
    )


def linked_loci() -> tuple[Locus, ...]:
    """kdr and cyp6 on the same chromosome, 5 cM apart: the sensitivity variant."""
    base = list(default_loci())
    base[2] = Locus("cyp6", 1, 25.0, METABOLIC,
                    actives=("deltamethrin", "imidacloprid", "fipronil"),
                    note="as default_loci, but placed 5 cM from kdr to test whether "
                         "linkage between target-site and metabolic resistance matters")
    return tuple(base)


@dataclass
class Genome:
    """The locus set plus the genetics that act on it."""

    loci: tuple[Locus, ...] = field(default_factory=default_loci)

    def __post_init__(self) -> None:
        """
        The karyotype is load-bearing, not decoration: a locus must sit on a real
        chromosome at a position inside the map. Declaring 2n = 42 and then never
        checking it would be exactly the kind of ornament this project removed
        from its predecessor.
        """
        n_chrom = int(P["chromosome_pairs"])
        span = float(P["map_length_cM"])
        for l in self.loci:
            if not 1 <= l.chromosome <= n_chrom:
                raise ValueError(
                    f"{l.name} is on chromosome {l.chromosome}; Blattella germanica "
                    f"has {n_chrom} linkage groups (2n = {2 * n_chrom})")
            if not 0.0 <= l.position_cM <= span:
                raise ValueError(
                    f"{l.name} sits at {l.position_cM} cM, outside the {span} cM map")
        names = [l.name for l in self.loci]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate locus names: {names}")

    @property
    def n_loci(self) -> int:
        return len(self.loci)

    def index(self, name: str) -> int:
        for i, l in enumerate(self.loci):
            if l.name == name:
                return i
        raise KeyError(name)

    # --------------------------------------------------------------- genotypes
    def founder_haplotypes(self, n: int, freqs: dict[str, float],
                           rng: np.random.Generator) -> np.ndarray:
        """(n, 2, n_loci) uint8 haplotypes drawn at the given allele frequencies."""
        f = np.array([freqs.get(l.name, 0.0) for l in self.loci])
        return (rng.random((n, 2, self.n_loci)) < f).astype(np.uint8)

    def dosage(self, hap: np.ndarray) -> np.ndarray:
        """(n, n_loci) count of resistance alleles, 0-2."""
        return hap.sum(axis=1).astype(np.int8)

    def allele_frequencies(self, hap: np.ndarray) -> dict[str, float]:
        if hap.shape[0] == 0:
            return {l.name: float("nan") for l in self.loci}
        f = hap.reshape(-1, self.n_loci).mean(axis=0)
        return {l.name: float(v) for l, v in zip(self.loci, f)}

    # ------------------------------------------------------------ inheritance
    def gametes(self, hap: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """
        One gamete per individual: (n, n_loci).

        Chromosomes assort independently. Within a chromosome, the probability of
        a switch between consecutive loci follows Haldane's map function,
        r = (1 - exp(-2d)) / 2 for map distance d in morgans.
        """
        n = hap.shape[0]
        out = np.empty((n, self.n_loci), dtype=np.uint8)
        by_chrom: dict[int, list[int]] = {}
        for i, l in enumerate(self.loci):
            by_chrom.setdefault(l.chromosome, []).append(i)
        for _chrom, idx in by_chrom.items():
            idx = sorted(idx, key=lambda i: self.loci[i].position_cM)
            which = rng.integers(0, 2, n)                    # which parental strand
            out[:, idx[0]] = hap[np.arange(n), which, idx[0]]
            for a, b in zip(idx, idx[1:]):
                d = abs(self.loci[b].position_cM - self.loci[a].position_cM) / 100.0
                r = (1.0 - np.exp(-2.0 * d)) / 2.0
                which = np.where(rng.random(n) < r, 1 - which, which)
                out[:, b] = hap[np.arange(n), which, b]
        return out

    def mate(self, hap: np.ndarray, mothers: np.ndarray, fathers: np.ndarray,
             rng: np.random.Generator) -> np.ndarray:
        """Offspring haplotypes from paired parents, one gamete each."""
        return np.stack([self.gametes(hap[mothers], rng),
                         self.gametes(hap[fathers], rng)], axis=1)

    # ------------------------------------------------------- genotype to trait
    def resistance_ratio(self, hap: np.ndarray, active: str, target: str | None) -> np.ndarray:
        """
        Fold increase in the dose needed, per individual, from target-site loci.

        The effect size is not invented: it is the ΔΔG carried by
        `blattella.chem` for the mutation, converted back to a resistance ratio.
        A heterozygote gets the effect scaled by the dominance coefficient, on a
        log scale, because free energies add.
        """
        dose = self.dosage(hap)
        out = np.ones(hap.shape[0])
        for i, l in enumerate(self.loci):
            if l.kind != TARGET_SITE or l.target != target:
                continue
            if l.name == "kdr":
                full = resistance_ratio_from_ddg(reference_ddg(KDR_L993F).value)
            else:
                # no published ratio for this locus; the assumed value is declared
                # as a parameter with a sweep rather than sitting inline
                full = float(P["rdl_resistance_ratio"])
            h = float(P["kdr_dominance"])
            scale = np.where(dose[:, i] == 2, 1.0, np.where(dose[:, i] == 1, h, 0.0))
            out *= full ** scale
        return out

    def clearance_multiplier(self, hap: np.ndarray, active: str,
                             only: str | None = None) -> np.ndarray:
        """
        Fold increase in metabolic clearance, per individual.

        `only` restricts the product to a single locus, which is what an
        intervention against one gene needs: knocking down a P450 must not be
        allowed to switch off the esterase and the GST as well.
        """
        dose = self.dosage(hap)
        out = np.ones(hap.shape[0])
        boost = float(P["metabolic_clearance_boost"])
        h = float(P["metabolic_dominance"])
        for i, l in enumerate(self.loci):
            if l.kind != METABOLIC or active not in l.actives:
                continue
            if only is not None and l.name != only:
                continue
            scale = np.where(dose[:, i] == 2, 1.0, np.where(dose[:, i] == 1, h, 0.0))
            out *= boost ** scale
        return out

    def fitness_cost(self, hap: np.ndarray) -> np.ndarray:
        """
        Relative fitness in the absence of insecticide, per individual.

        This is the parameter the whole rotation question turns on. With no cost,
        a resistance allele never declines and rotating actives buys nothing.
        """
        dose = self.dosage(hap)
        w = np.ones(hap.shape[0])
        for i, l in enumerate(self.loci):
            c = float(P["fitness_cost_target_site"] if l.kind == TARGET_SITE
                      else P["fitness_cost_metabolic"])
            h = float(P["kdr_dominance"] if l.kind == TARGET_SITE else P["metabolic_dominance"])
            scale = np.where(dose[:, i] == 2, 1.0, np.where(dose[:, i] == 1, h, 0.0))
            w *= 1.0 - c * scale
        return np.clip(w, 0.0, 1.0)
