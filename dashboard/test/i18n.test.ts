import { describe, expect, it } from "vitest";
import { DICT } from "@/lib/i18n";

/**
 * The dictionary is the whole translation layer, so these tests are the whole
 * guarantee: a key added in one language and forgotten in the other would
 * otherwise render as a blank on a live page.
 */
describe("the dictionary", () => {
  const entries = Object.entries(DICT);

  it("has both languages for every key, and neither is empty", () => {
    for (const [key, value] of entries) {
      expect(value, key).toHaveProperty("en");
      expect(value, key).toHaveProperty("zh");
      expect(value.en.trim().length, key).toBeGreaterThan(0);
      expect(value.zh.trim().length, key).toBeGreaterThan(0);
    }
  });

  it("actually translates rather than copying the English through", () => {
    // a handful of labels are legitimately identical in both, but prose is not
    const prose = entries.filter(([, v]) => v.en.length > 40);
    expect(prose.length).toBeGreaterThan(8);
    for (const [key, v] of prose) {
      expect(v.zh, key).not.toBe(v.en);
      expect(v.zh, key).toMatch(/[一-鿿]/);
    }
  });

  it("covers every navigation entry and every shared control", () => {
    const required = [
      "navOverview", "navColony", "navEvolution", "navStrategy",
      "navChemistry", "navNeural", "navParameters",
      "run", "running", "export", "whatThisMeans", "beScepticalOf",
      "howToReproduce", "apiDown",
    ];
    for (const key of required) expect(Object.keys(DICT)).toContain(key);
  });

  it("keeps field terms in their usual written form inside the Chinese", () => {
    // translating kdr or LD50 would make the page harder to read, not easier
    expect(DICT.linkedLoci.zh).toContain("kdr");
    expect(DICT.doseLd50.zh).toContain("LD50");
    expect(DICT.chemistryExplain.zh).toContain("ΔΔG");
  });
});
