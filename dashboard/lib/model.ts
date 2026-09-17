/**
 * The shape of `public/blattella.json`, written by `python -m blattella.cli export`.
 *
 * There is no backend. The dashboard renders one static document produced by the
 * model, so nothing on screen can be newer or different from what the science
 * actually computed. `generated` is shown in the header for exactly that reason.
 */

export interface StrategyRow {
  strategy: string;
  seeds: number;
  resistant_runs: number;
  median_time_to_resistance: number | null;
  peak_target_site_mean: number;
  mean_population: number;
  mean_survival: number;
  extinctions: number;
  [freq: string]: string | number | null;
}

export interface HistoryRow {
  generation: number;
  n: number;
  active: string | null;
  survival: number | null;
  [freq: string]: string | number | null;
}

export interface Trajectory {
  description: string;
  history: HistoryRow[];
}

export interface Site {
  x: number;
  y: number;
  r: number;
}

export interface DdgRow {
  backend: string;
  maturity: string;
  ddg_kcal_per_mol: number;
  uncertainty: number;
  implied_resistance_ratio: number;
  note: string;
  error_vs_reference_kcal?: number;
  sign_agrees_with_reference?: boolean;
}

export interface NeuralRow {
  active: string;
  hours: number;
  rate_hz: number;
  rate_fold: number;
  isi_cv: number;
  cv_fold: number | null;
  fano: number;
  silent: boolean;
}

export interface Model {
  generated: string;
  question: string;
  answer: string;
  caveats: string[];
  parameters: {
    by_source: Record<string, number>;
    assumptions_needing_sensitivity: { name: string; value: number; unit: string; sweep: number[] }[];
    literature: { name: string; value: number; unit: string; cite: string }[];
  };
  strategy: {
    config: { seeds: number; generations: number; exposed_fraction: number; median_dose_ld50: number };
    summary: StrategyRow[];
    trajectories: Record<string, Trajectory>;
  };
  contact: {
    config: { n: number; hours: number; arena_cm: number; harborages: number; resources: number };
    network: Record<string, number>;
    harborages: Site[];
    resources: Site[];
    positions: number[][];
    degree: number[];
    trips: number[];
  };
  chemistry: {
    case: { target: string; gene: string; mutation: string; ligand: string; note: string };
    reference: DdgRow;
    backends: DdgRow[];
  };
  neural: {
    burden_ld50: number;
    species_caveat: string;
    testable_orderings: string[];
    known_blind_spots: string[];
    rows: NeuralRow[];
  };
}

/** Locus names carried in the summary and history rows, in display order. */
export const LOCI = ["kdr", "rdl", "cyp6", "est", "gst"] as const;
export type Locus = (typeof LOCI)[number];

export const LOCUS_COLOUR: Record<Locus, string> = {
  kdr: "#e05252",
  rdl: "#e0a252",
  cyp6: "#52a0e0",
  est: "#6fc27a",
  gst: "#a97fd0",
};

/** Target-site loci are the ones the resistance threshold is measured on. */
export const TARGET_SITE_LOCI: Locus[] = ["kdr", "rdl"];

export function freq(row: { [k: string]: unknown }, locus: Locus): number {
  const v = row[`f_${locus}`];
  return typeof v === "number" ? v : 0;
}

/** Shorten the machine-generated arm names for display without losing which is which. */
export function strategyLabel(name: string): string {
  if (name === "untreated") return "untreated";
  const [kind, rest = ""] = name.split(":");
  if (kind === "single") return `single · ${rest}`;
  if (kind === "rotation") {
    const [actives, period] = rest.split("/");
    return `rotation · ${actives.split("-").length} actives${period ? `, ${period} gens each` : ""}`;
  }
  if (kind === "mixture") return `mixture · ${rest.split("@")[0].split("-").length} actives, matched dose`;
  return name;
}
