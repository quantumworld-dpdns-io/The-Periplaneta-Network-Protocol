/**
 * Client for the model server (`blattella.api`).
 *
 * Every interactive page runs the real simulation through this. Nothing here
 * caches or approximates: a slider move is a request, and the response carries
 * both the numbers and the command line that would reproduce them.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("unreachable", 0);
  }
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep the status */
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

const post = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

// ------------------------------------------------------------------- shapes --
export interface Caveated {
  caveats?: string[];
  reproduce?: string;
  elapsed_s?: number;
}

export interface HistoryRow {
  generation: number;
  n: number;
  active: string | null;
  survival: number | null;
  [k: string]: string | number | boolean | null;
}

export interface EvolveResult extends Caveated {
  strategy: { id: string; name: string; description: string };
  history: HistoryRow[];
  final: Record<string, number>;
  generations_run: number;
  survivors: number;
}

export interface SummaryRow {
  strategy: string;
  seeds: number;
  resistant_runs: number;
  median_time_to_resistance: number | null;
  peak_target_site_mean: number;
  mean_survival: number;
  extinctions: number;
  [k: string]: string | number | null;
}

export interface CompareResult extends Caveated {
  config: Record<string, number | boolean>;
  summary: SummaryRow[];
}

export interface ContactResult extends Caveated {
  network: Record<string, number>;
  harborages: { x: number; y: number; r: number }[];
  resources: { x: number; y: number; r: number }[];
  treated: number[];
  positions: number[][];
  degree: number[];
  alive: boolean[];
  trips: number[];
  states: Record<string, number>;
  config: { arena_cm: number; [k: string]: unknown };
  toxicology?: {
    dead: number;
    mortality: number;
    ever_exposed: number;
    fed_at_bait: number;
    acquisition_routes: Record<string, number>;
  };
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

export interface ChemResult extends Caveated {
  case: { target: string; gene: string; mutation: string; ligand: string; note: string };
  reference: DdgRow;
  backends: DdgRow[];
}

export interface NeuralRow {
  active: string;
  hours: number;
  rate_fold: number;
  isi_cv: number;
  cv_fold: number | null;
  silent: boolean;
}

export interface NeuralResult extends Caveated {
  burden_ld50: number;
  species_caveat: string;
  testable_orderings: string[];
  known_blind_spots: string[];
  rows: NeuralRow[];
}

export interface ParametersResult {
  literature: { name: string; value: number; unit: string; cite: string }[];
  assumptions: { name: string; value: number; unit: string; sweep: number[]; note: string }[];
}

export interface FormatSpec {
  id: string;
  title: string;
  spec: string;
  spec_url: string;
  media_type: string;
  filename: string;
  method: "GET" | "POST";
  path: string;
  stage: string;
  consumers: string[];
  audience: string[];
  what_it_is: string;
  what_it_is_not: string;
  caveats: string[];
  cli: string;
  requires: string[];
  availability: "ready" | "degraded" | "needs_data" | "unsupported";
  needs_run: boolean;
  in_bundle: boolean;
}

export interface InteropDiscovery {
  version: string;
  generated: string;
  audiences: Record<string, string>;
  provenance: {
    report: string;
    table: string;
    literature: number;
    assumption: number;
    total: number;
  };
  reference_data: Record<string, unknown>;
  formats: FormatSpec[];
}

export interface LiveLayer { data: string; peak: number }

export interface LiveFrame {
  t_hours: number;
  elapsed_hours: number;
  dark: boolean;
  paused: boolean;
  speed: number;
  grid: number;
  layers: Record<"pheromone" | "residue" | "density", LiveLayer>;
  positions: number[][];
  alive: boolean[];
  state: number[];
  lethal_fraction: number[];
  counts: Record<string, number>;
  network: Record<string, number>;
  treated: Record<string, number[]>;
  station_visits: number[];
  events: { t_hours: number; note: string }[];
}

export interface LiveStart {
  session: string;
  grid: number;
  layers: string[];
  arena_cm: number;
  harborages: { x: number; y: number; r: number }[];
  resources: { x: number; y: number; r: number }[];
  actives: string[];
  frame: LiveFrame;
}

export type LiveAction =
  | "pause" | "speed" | "light" | "bait" | "clear_bait" | "toggle_station"
  | "add" | "remove" | "aggregation" | "concentration";

export interface Health {
  status: string;
  actives: string[];
  limits: Record<string, number>;
}

// ------------------------------------------------------------------- calls --
export const api = {
  health: () => request<Health>("/api/health"),
  strategies: () =>
    request<{ id: string; name: string; description: string; dose_share: number }[]>(
      "/api/strategies",
    ),
  parameters: () => request<ParametersResult>("/api/parameters"),
  interop: () => request<InteropDiscovery>("/api/interop/formats"),
  evolve: (body: Record<string, unknown>) => post<EvolveResult>("/api/evolve", body),
  compare: (body: Record<string, unknown>) => post<CompareResult>("/api/compare", body),
  contact: (body: Record<string, unknown>) => post<ContactResult>("/api/contact", body),
  chem: (body: Record<string, unknown>) => post<ChemResult>("/api/chem", body),
  neural: (body: Record<string, unknown>) => post<NeuralResult>("/api/neural", body),

  // --- live session ---------------------------------------------------------
  liveStart: (body: Record<string, unknown>) => post<LiveStart>("/api/live", body),
  liveAct: (session: string, action: LiveAction, params: Record<string, unknown> = {}) =>
    post<{ ok: boolean; note: string; frame: LiveFrame }>(`/api/live/${session}/act`, { action, params }),
  liveStop: (session: string) =>
    request<{ stopped: boolean }>(`/api/live/${session}`, { method: "DELETE" }),
  /** URL for an EventSource. The stream advances the simulation; the frame endpoint does not. */
  liveStreamUrl: (session: string, fps: number) =>
    `${API_BASE}/api/live/${session}/stream?fps=${fps}`,
};

/**
 * Ask the server for a file and hand it to the browser as a download.
 * The filename comes from the server's Content-Disposition, so the API decides
 * what things are called and the page does not have to guess.
 */
export async function download(path: string, body?: unknown): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(`export failed (${res.status})`, res.status);
  const disposition = res.headers.get("content-disposition") ?? "";
  const named = /filename="([^"]+)"/.exec(disposition)?.[1];
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = named ?? path.split("/").pop() ?? "export.txt";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
