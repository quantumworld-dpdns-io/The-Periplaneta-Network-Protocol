/**
 * Accent colours. `StateName` used to come from the streaming wire contract; that
 * module went with the frozen stack, so the handful of names the palette needs
 * are declared here.
 */
export type StateName = "baseline" | "toxin" | "drug" | "radiation" | "offline" | "collapsed";

/** Per-state accent colours (mirrors tailwind.config.ts `state.*`). */
export const STATE_COLORS: Record<StateName, string> = {
  baseline: "#38bdf8",
  toxin: "#f59e0b",
  drug: "#34d399",
  radiation: "#c084fc",
  offline: "#64748b",
  collapsed: "#ef4444",
};

type RGB = [number, number, number];

/** Stress ramp stops (0 → 1): deep blue → teal → amber → red → white-hot. */
const STOPS: [number, RGB][] = [
  [0.0, [8, 18, 48]],
  [0.25, [16, 76, 130]],
  [0.5, [14, 165, 150]],
  [0.7, [245, 158, 11]],
  [0.88, [239, 68, 68]],
  [1.0, [255, 240, 220]],
];

function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t;
}

export function stressToRgb(v: number): RGB {
  const x = Math.min(1, Math.max(0, v));
  for (let i = 1; i < STOPS.length; i++) {
    const [p1, c1] = STOPS[i];
    const [p0, c0] = STOPS[i - 1];
    if (x <= p1) {
      const t = (x - p0) / (p1 - p0);
      return [Math.round(lerp(c0[0], c1[0], t)), Math.round(lerp(c0[1], c1[1], t)), Math.round(lerp(c0[2], c1[2], t))];
    }
  }
  return STOPS[STOPS.length - 1][1];
}

/** 256-entry RGBA lookup table packed for a Uint32Array view over ImageData. */
export function buildStressLut(): Uint32Array {
  const lut = new Uint32Array(256);
  const le = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;
  for (let i = 0; i < 256; i++) {
    const [r, g, b] = stressToRgb(i / 255);
    // ImageData is RGBA byte order; on little-endian hosts the u32 is ABGR.
    lut[i] = le ? ((255 << 24) | (b << 16) | (g << 8) | r) >>> 0 : ((r << 24) | (g << 16) | (b << 8) | 255) >>> 0;
  }
  return lut;
}

export function stressCss(v: number): string {
  const [r, g, b] = stressToRgb(v);
  return `rgb(${r},${g},${b})`;
}
