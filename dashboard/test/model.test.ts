import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { LOCI, TARGET_SITE_LOCI, freq, strategyLabel, type Model } from "@/lib/model";

const model: Model = JSON.parse(
  readFileSync(join(process.cwd(), "public", "blattella.json"), "utf8"),
);

/**
 * These tests guard the contract between `python -m blattella.cli export` and the
 * page that renders it. If the model grows a field or renames an arm, the page
 * should fail here rather than silently render blanks.
 */
describe("the exported model", () => {
  it("carries the question, the answer and its caveats", () => {
    expect(model.question).toMatch(/rotation, mixture or single-product/i);
    expect(model.answer).toMatch(/rotation/i);
    expect(model.caveats.length).toBeGreaterThanOrEqual(4);
    expect(model.generated).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  });

  it("says out loud that ground truth is simulated", () => {
    expect(model.caveats.join(" ")).toMatch(/simulated/i);
  });

  it("has one summary row and one trajectory per strategy arm", () => {
    const named = model.strategy.summary.map((r) => r.strategy).sort();
    expect(named.length).toBeGreaterThanOrEqual(4);
    expect(Object.keys(model.strategy.trajectories).sort()).toEqual(named);
  });

  it("reports every locus the page draws", () => {
    for (const row of model.strategy.summary) {
      for (const l of LOCI) expect(typeof row[`f_${l}`]).toBe("number");
    }
    const first = Object.values(model.strategy.trajectories)[0].history[0];
    for (const l of LOCI) expect(freq(first, l)).toBeGreaterThanOrEqual(0);
  });

  it("leaves the median censored when an arm never became resistant", () => {
    const untreated = model.strategy.summary.find((r) => r.strategy === "untreated");
    expect(untreated).toBeDefined();
    expect(untreated!.resistant_runs).toBe(0);
    expect(untreated!.median_time_to_resistance).toBeNull();
  });

  it("keeps the chemistry reference separate from the backends and labels maturity", () => {
    expect(model.chemistry.reference.maturity).toBe("reference");
    expect(model.chemistry.backends.length).toBeGreaterThanOrEqual(1);
    for (const b of model.chemistry.backends) {
      expect(["production", "demonstration"]).toContain(b.maturity);
      expect(typeof b.sign_agrees_with_reference).toBe("boolean");
    }
  });

  it("carries the neural predictions with their blind spots", () => {
    expect(model.neural.testable_orderings.length).toBeGreaterThanOrEqual(3);
    expect(model.neural.known_blind_spots.length).toBeGreaterThanOrEqual(2);
    expect(model.neural.species_caveat).toMatch(/extrapolation/i);
  });

  it("counts more assumptions than literature parameters, and lists both", () => {
    const { by_source, literature, assumptions_needing_sensitivity } = model.parameters;
    expect(by_source.assumption).toBeGreaterThan(by_source.literature);
    expect(literature.every((p) => p.cite.length > 0)).toBe(true);
    expect(assumptions_needing_sensitivity.every((p) => p.sweep.length === 2)).toBe(true);
  });

  it("gives the arena enough to draw", () => {
    const c = model.contact;
    expect(c.positions.length).toBe(c.degree.length);
    expect(c.harborages.length).toBeGreaterThan(0);
    expect(c.network.edges).toBeGreaterThan(0);
  });
});

describe("display helpers", () => {
  it("names every arm readably", () => {
    for (const row of model.strategy.summary) {
      const label = strategyLabel(row.strategy);
      expect(label.length).toBeGreaterThan(0);
      expect(label).not.toMatch(/delt-imid/);
    }
    expect(strategyLabel("untreated")).toBe("untreated");
    expect(strategyLabel("single:deltamethrin")).toBe("single · deltamethrin");
    expect(strategyLabel("rotation:delt-imid-fipr/3")).toMatch(/3 gens each/);
  });

  it("treats a missing frequency as zero rather than throwing", () => {
    expect(freq({}, "kdr")).toBe(0);
  });

  it("knows which loci the resistance threshold is measured on", () => {
    expect(TARGET_SITE_LOCI).toEqual(["kdr", "rdl"]);
    for (const l of TARGET_SITE_LOCI) expect(LOCI).toContain(l);
  });
});
