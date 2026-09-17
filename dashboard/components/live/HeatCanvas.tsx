"use client";

/**
 * The colony, drawn.
 *
 * Three things are stacked on one canvas:
 *
 *   1. a heat field, which is real model state downsampled to a small grid --
 *      the aggregation pheromone the animals deposit and follow, the insecticide
 *      residue lying on the floor, or simply where they are;
 *   2. the furniture, meaning harborages and food sites, with baited sites ringed;
 *   3. the animals themselves, one dot each, coloured by how much poison they
 *      are carrying.
 *
 * The heat grid arrives base64-encoded, one byte a cell, already square-rooted
 * server-side so the low end is visible. It is painted at grid resolution into a
 * tiny offscreen canvas and then scaled up with smoothing, which is both faster
 * and softer than drawing 2304 rectangles.
 */
import { useEffect, useMemo, useRef } from "react";
import type { LiveFrame } from "@/lib/api";

export type LayerName = "pheromone" | "residue" | "density";

/** Ramp endpoints per layer, as [r,g,b]. Alpha carries the intensity. */
const RAMP: Record<LayerName, [number, number, number]> = {
  pheromone: [34, 211, 238],   // cyan: what they leave for each other
  residue: [224, 82, 82],      // red: what we left for them
  density: [167, 139, 250],    // violet: where they are
};

const STATE_COLOUR = ["#64748b", "#fbbf24", "#38bdf8"]; // resting, foraging, returning

function decode(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** Poison load → colour. Green while clean, amber at a fraction of an LD50, red at lethal. */
function burdenColour(f: number): string {
  if (f <= 0) return "";
  const k = Math.min(f, 1);
  const r = Math.round(52 + k * 172);
  const g = Math.round(211 - k * 129);
  const b = Math.round(153 - k * 71);
  return `rgb(${r},${g},${b})`;
}

export function HeatCanvas({
  frame, layer, arenaCm, harborages, resources, colourByState, onStationClick,
}: {
  frame: LiveFrame;
  layer: LayerName;
  arenaCm: number;
  harborages: { x: number; y: number; r: number }[];
  resources: { x: number; y: number; r: number }[];
  colourByState: boolean;
  /** Clicking a food site toggles bait there. Placement is a real decision. */
  onStationClick?: (index: number) => void;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const heatRef = useRef<HTMLCanvasElement | null>(null);
  const treated = useMemo(
    () => new Set(Object.values(frame.treated ?? {}).flat()),
    [frame.treated],
  );

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const S = 720;                                   // internal resolution
    if (canvas.width !== S) { canvas.width = S; canvas.height = S; }
    const px = (v: number) => (v / arenaCm) * S;     // cm → canvas pixels

    ctx.fillStyle = frame.dark ? "#05070c" : "#0d1017";
    ctx.fillRect(0, 0, S, S);

    // --- 1. heat field ------------------------------------------------------
    const g = frame.grid;
    const bytes = decode(frame.layers[layer].data);
    if (bytes.length === g * g) {
      let off = heatRef.current;
      if (!off) { off = document.createElement("canvas"); heatRef.current = off; }
      off.width = g; off.height = g;
      const octx = off.getContext("2d");
      if (octx) {
        const img = octx.createImageData(g, g);
        const [cr, cg, cb] = RAMP[layer];
        for (let i = 0; i < bytes.length; i++) {
          const v = bytes[i];
          img.data[i * 4] = cr;
          img.data[i * 4 + 1] = cg;
          img.data[i * 4 + 2] = cb;
          img.data[i * 4 + 3] = Math.round(v * 0.78);
        }
        octx.putImageData(img, 0, 0);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(off, 0, 0, g, g, 0, 0, S, S);
      }
    }

    // --- 2. furniture -------------------------------------------------------
    // A harborage is 3 cm across in a 3 m arena, which is four pixels here and
    // invisible under the animals sitting in it. Draw the marker at a floor size
    // so the reader can see where the furniture is; the model still uses the
    // real radius.
    const ring = (x: number, y: number, r: number, floor: number) =>
      ctx.arc(px(x), px(y), Math.max(px(r), floor), 0, Math.PI * 2);

    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 4]);
    ctx.strokeStyle = "rgba(148,163,184,0.8)";
    for (const h of harborages) {
      ctx.beginPath();
      ring(h.x, h.y, h.r, 15);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    const visits = frame.station_visits ?? [];
    resources.forEach((r, i) => {
      const isBait = treated.has(i);
      const unused = (visits[i] ?? 0) === 0;
      ctx.beginPath();
      ring(r.x, r.y, r.r, 11);
      ctx.fillStyle = isBait ? "rgba(248,113,113,0.18)" : "rgba(251,191,36,0.10)";
      ctx.fill();
      // A station no animal walks to kills nobody however much poison is in it,
      // so it is drawn dimmed rather than looking like a working one.
      ctx.strokeStyle = isBait ? "#f87171" : unused ? "rgba(251,191,36,0.3)" : "rgba(251,191,36,0.85)";
      ctx.lineWidth = isBait ? 3 : 1.5;
      ctx.stroke();
      ctx.fillStyle = unused ? "rgba(148,163,184,0.7)" : "rgba(251,191,36,0.95)";
      ctx.font = "600 13px ui-monospace, monospace";
      ctx.textAlign = "center";
      ctx.fillText(String(visits[i] ?? 0), px(r.x), px(r.y) - Math.max(px(r.r), 11) - 5);
    });

    // --- 3. the animals -----------------------------------------------------
    const { positions, alive, state, lethal_fraction: lethal } = frame;
    for (let i = 0; i < positions.length; i++) {
      const [x, y] = positions[i];
      const cx = px(x), cy = px(y);
      if (!alive[i]) {
        ctx.strokeStyle = "rgba(100,116,139,0.5)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(cx - 2.5, cy - 2.5); ctx.lineTo(cx + 2.5, cy + 2.5);
        ctx.moveTo(cx + 2.5, cy - 2.5); ctx.lineTo(cx - 2.5, cy + 2.5);
        ctx.stroke();
        continue;
      }
      const poisoned = burdenColour(lethal[i] ?? 0);
      ctx.fillStyle = poisoned || (colourByState ? STATE_COLOUR[state[i]] ?? "#94a3b8" : "#e2e8f0");
      ctx.beginPath();
      ctx.arc(cx, cy, poisoned ? 3.2 : 2.4, 0, Math.PI * 2);
      ctx.fill();
    }
  }, [frame, layer, arenaCm, harborages, resources, colourByState, treated]);

  const click = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!onStationClick) return;
    const box = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - box.left) / box.width) * arenaCm;
    const y = ((e.clientY - box.top) / box.height) * arenaCm;
    // generous hit radius: the drawn marker is bigger than the real site
    const grab = (arenaCm / 720) * 20;
    let best = -1;
    let bestD = Infinity;
    resources.forEach((r, i) => {
      const d = Math.hypot(r.x - x, r.y - y);
      if (d < Math.max(r.r, grab) && d < bestD) { best = i; bestD = d; }
    });
    if (best >= 0) onStationClick(best);
  };

  return (
    <canvas
      ref={ref}
      onClick={click}
      className={`w-full aspect-square rounded border border-white/10 bg-black ${
        onStationClick ? "cursor-pointer" : ""
      }`}
    />
  );
}
