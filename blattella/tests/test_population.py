import numpy as np
import pytest

from blattella import genome as gn
from blattella import population as pp
from blattella.behaviour import make_colony, simulate
from blattella.chem import KDR_L993F, reference_ddg, resistance_ratio_from_ddg
from blattella.genome import Genome, METABOLIC, TARGET_SITE, linked_loci
from blattella.population import Deployment, ExposureProfile, make_population
from blattella.toxicology import DELTAMETHRIN, FIPRONIL, IMIDACLOPRID, Toxicology

HEAVY = ExposureProfile(exposed_fraction=0.6, dose_ld50_median=50.0, dose_ld50_log_sd=1.0)
NONE = ExposureProfile(0.0, 0.0)


def _profiles(p=HEAVY):
    return {"deltamethrin": p, "imidacloprid": p, "fipronil": p, None: NONE}


def _run(schedule, generations=20, seed=1, n=400, genome=None, profile=HEAVY):
    pop = make_population(n=n, seed=seed, genome=genome)
    pop.run(Deployment(schedule), generations, _profiles(profile))
    return pop


# ------------------------------------------------------------------- genetics --
def test_karyotype_and_loci_are_declared_with_their_mechanism():
    g = Genome()
    assert gn.N_CHROMOSOME_PAIRS == 21                       # 2n = 42
    assert float(gn.P["chromosome_pairs"]) == 21
    kinds = {l.name: l.kind for l in g.loci}
    assert kinds["kdr"] == TARGET_SITE and kinds["rdl"] == TARGET_SITE
    assert kinds["cyp6"] == METABOLIC
    # metabolic resistance is cross-resistant; target-site is not
    cyp6 = g.loci[g.index("cyp6")]
    assert set(cyp6.actives) == {"deltamethrin", "imidacloprid", "fipronil"}
    assert g.loci[g.index("kdr")].target == "Vssc"


def test_founder_frequencies_and_dosage_are_consistent():
    g = Genome()
    rng = np.random.default_rng(0)
    hap = g.founder_haplotypes(5000, {l.name: 0.3 for l in g.loci}, rng)
    assert hap.shape == (5000, 2, g.n_loci)
    for v in g.allele_frequencies(hap).values():
        assert 0.27 < v < 0.33
    d = g.dosage(hap)
    assert d.shape == (5000, g.n_loci)
    assert d.min() >= 0 and d.max() <= 2


def test_neutral_inheritance_conserves_allele_frequencies():
    """No selection: frequencies drift but do not trend."""
    g = Genome()
    rng = np.random.default_rng(3)
    hap = g.founder_haplotypes(4000, {l.name: 0.4 for l in g.loci}, rng)
    before = g.allele_frequencies(hap)
    for _ in range(10):
        idx = np.arange(hap.shape[0])
        hap = g.mate(hap, rng.permutation(idx), rng.permutation(idx), rng)
    after = g.allele_frequencies(hap)
    for k in before:
        assert abs(after[k] - before[k]) < 0.05, (k, before[k], after[k])


def test_recombination_actually_breaks_linkage():
    """Two loci on one chromosome must produce recombinant gametes."""
    g = Genome(loci=linked_loci())
    rng = np.random.default_rng(0)
    n = 20000
    hap = np.zeros((n, 2, g.n_loci), dtype=np.uint8)
    i, j = g.index("kdr"), g.index("cyp6")
    hap[:, 0, i] = 1                      # strand 0 carries kdr only
    hap[:, 1, j] = 1                      # strand 1 carries cyp6 only
    gam = g.gametes(hap, rng)
    recombinant = ((gam[:, i] == 1) & (gam[:, j] == 1)) | ((gam[:, i] == 0) & (gam[:, j] == 0))
    # 5 cM apart, so a few percent of gametes recombine
    assert 0.005 < recombinant.mean() < 0.20, recombinant.mean()


def test_unlinked_loci_assort_independently():
    g = Genome()                                   # kdr on chr 1, cyp6 on chr 3
    rng = np.random.default_rng(0)
    n = 20000
    hap = np.zeros((n, 2, g.n_loci), dtype=np.uint8)
    i, j = g.index("kdr"), g.index("cyp6")
    hap[:, 0, i] = 1
    hap[:, 1, j] = 1
    gam = g.gametes(hap, rng)
    both = ((gam[:, i] == 1) & (gam[:, j] == 1)).mean()
    assert 0.22 < both < 0.28, both          # independent assortment gives a quarter


# ------------------------------------------------------ genotype to phenotype --
def test_target_site_effect_size_comes_from_the_measured_ddg():
    """The selection coefficient is not invented: it is RT ln(RR) read back."""
    g = Genome()
    hap = np.zeros((3, 2, g.n_loci), dtype=np.uint8)
    i = g.index("kdr")
    hap[1, 0, i] = 1                     # heterozygote
    hap[2, :, i] = 1                     # homozygote
    rr = g.resistance_ratio(hap, "deltamethrin", "Vssc")
    expected = resistance_ratio_from_ddg(reference_ddg(KDR_L993F).value)
    assert rr[0] == pytest.approx(1.0)
    assert rr[2] == pytest.approx(expected)
    assert 1.0 < rr[1] < rr[2]           # incomplete dominance sits between


def test_target_site_resistance_is_specific_to_its_target():
    g = Genome()
    hap = np.zeros((1, 2, g.n_loci), dtype=np.uint8)
    hap[0, :, g.index("kdr")] = 1
    assert g.resistance_ratio(hap, "deltamethrin", "Vssc")[0] > 100
    # kdr does nothing against a neonicotinoid, which acts on a different target
    assert g.resistance_ratio(hap, "imidacloprid", "nAChR")[0] == pytest.approx(1.0)


def test_metabolic_resistance_is_cross_resistant():
    g = Genome()
    hap = np.zeros((1, 2, g.n_loci), dtype=np.uint8)
    hap[0, :, g.index("cyp6")] = 1
    for active in ("deltamethrin", "imidacloprid", "fipronil"):
        assert g.clearance_multiplier(hap, active)[0] > 1.0


def test_resistance_carries_a_fitness_cost():
    g = Genome()
    hap = np.zeros((2, 2, g.n_loci), dtype=np.uint8)
    hap[1, :, g.index("kdr")] = 1
    w = g.fitness_cost(hap)
    assert w[0] == pytest.approx(1.0)
    assert w[1] < 1.0


# ------------------------------------------------------------------ evolution --
def test_single_active_drives_its_target_site_allele_toward_fixation():
    pop = _run(["deltamethrin"])
    assert pop.allele_frequencies()["kdr"] > 0.9


def test_withdrawing_the_insecticide_lets_resistance_decline():
    """The fitness cost is what makes rotation possible at all."""
    pop = _run([None], generations=20)
    assert pop.allele_frequencies()["kdr"] < 0.05


def test_without_a_fitness_cost_resistance_does_not_decline(monkeypatch):
    monkeypatch.setitem(gn.P, "fitness_cost_target_site", 0.0)
    monkeypatch.setitem(gn.P, "fitness_cost_metabolic", 0.0)
    pop = _run([None], generations=20)
    assert pop.allele_frequencies()["kdr"] > 0.02


def test_rotation_delays_target_site_resistance_relative_to_single_use():
    """The headline comparison, in its simplest form."""
    single = _run(["deltamethrin"])
    rotation = _run(["deltamethrin", "imidacloprid", "fipronil"])
    assert rotation.allele_frequencies()["kdr"] < single.allele_frequencies()["kdr"]


def test_selection_is_absent_without_exposure():
    pop = make_population(n=400, seed=2)
    start = pop.allele_frequencies()["kdr"]
    pop.run(Deployment(["deltamethrin"]), 5, {"deltamethrin": NONE})
    assert pop.allele_frequencies()["kdr"] == pytest.approx(start, abs=0.06)


def test_history_is_recorded_per_generation():
    pop = _run(["deltamethrin"], generations=6)
    assert len(pop.history) == 7                    # founders plus six generations
    assert pop.history[0]["generation"] == 0
    assert all("f_kdr" in row for row in pop.history)
    assert pop.history[-1]["active"] == "deltamethrin"


def test_a_population_can_be_driven_extinct():
    """A dose no genotype survives wipes the colony out and the run stops."""
    lethal = ExposureProfile(exposed_fraction=1.0, dose_ld50_median=1e9, dose_ld50_log_sd=0.1)
    pop = make_population(n=200, seed=0)
    pop.run(Deployment(["imidacloprid"]), 10, {"imidacloprid": lethal})
    assert pop.hap.shape[0] < 2
    assert pop.history[-1]["extinct"] is True


# ----------------------------------------------------- two-timescale coupling --
def test_exposure_profile_is_derived_from_an_agent_based_run():
    c = make_colony(n=80, seed=2)
    c.t = 12 * 3600.0
    tox = Toxicology(colony=c, actives=(DELTAMETHRIN, IMIDACLOPRID, FIPRONIL))
    tox.treat_resource("fipronil", [0])
    simulate(c, 2 * 3600.0, 1.0, observers=[tox])
    prof = ExposureProfile.from_toxicology(tox, "fipronil", c)
    assert prof.source.startswith("agent-based")
    assert 0.0 < prof.exposed_fraction <= 1.0
    assert prof.dose_ld50_median > 1.0            # gel baits deliver many LD50s
    draws = prof.sample(5000, np.random.default_rng(0))
    assert abs((draws > 0).mean() - prof.exposed_fraction) < 0.05


def test_life_history_parameters_are_literature_backed():
    from blattella.params import Source
    for k in ("eggs_per_ootheca", "oothecae_per_female", "generation_days"):
        assert pp.P[k].source is Source.LITERATURE, k
    assert float(pp.P["eggs_per_ootheca"]) == 30.0
    assert float(pp.P["generation_days"]) == 100.0
