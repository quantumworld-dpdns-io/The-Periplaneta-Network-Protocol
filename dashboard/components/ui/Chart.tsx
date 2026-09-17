"use client";

/** Small dependency-free SVG charts. Everything is drawn from the API's numbers. */

export interface Series {
  label: string;
  colour: string;
  points: { x: number; y: number }[];
}

export function LineChart({
  series, xMax, yMax = 1, xLabel, yLabel, threshold, height = 190,
}: {
  series: Series[]; xMax: number; yMax?: number;
  xLabel?: string; yLabel?: string; threshold?: number; height?: number;
}) {
  const W = 640;
  const H = height;
  const L = 42;
  const B = 26;
  const x = (v: number) => L + (v / Math.max(xMax, 1)) * (W - L - 10);
  const y = (v: number) => H - B - (v / yMax) * (H - B - 12);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * yMax);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img" aria-label={yLabel}>
      {ticks.map((tv) => (
        <g key={tv}>
          <line x1={L} y1={y(tv)} x2={W - 10} y2={y(tv)} stroke="#1e2733" strokeWidth="1" />
          <text x={L - 6} y={y(tv) + 3} textAnchor="end" fontSize="9" fill="#64748b">
            {tv.toFixed(2)}
          </text>
        </g>
      ))}
      {threshold !== undefined ? (
        <line x1={L} y1={y(threshold)} x2={W - 10} y2={y(threshold)}
              stroke="#94a3b8" strokeDasharray="4 3" strokeWidth="1" />
      ) : null}
      <line x1={L} y1={y(0)} x2={W - 10} y2={y(0)} stroke="#334155" strokeWidth="1" />
      <line x1={L} y1={y(0)} x2={L} y2={12} stroke="#334155" strokeWidth="1" />
      {series.map((s) => (
        <polyline
          key={s.label}
          fill="none"
          stroke={s.colour}
          strokeWidth="1.8"
          strokeLinejoin="round"
          points={s.points.map((p) => `${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join(" ")}
        />
      ))}
      {xLabel ? (
        <text x={(W + L) / 2} y={H - 4} textAnchor="middle" fontSize="9" fill="#64748b">{xLabel}</text>
      ) : null}
      {yLabel ? (
        <text x={10} y={14} fontSize="9" fill="#64748b">{yLabel}</text>
      ) : null}
    </svg>
  );
}

export function Legend({ series }: { series: { label: string; colour: string }[] }) {
  return (
    <div className="flex flex-wrap gap-3 text-[10px] text-slate-400">
      {series.map((s) => (
        <span key={s.label} className="flex items-center gap-1.5">
          <span className="w-3 h-[3px] rounded" style={{ background: s.colour }} />
          {s.label}
        </span>
      ))}
    </div>
  );
}

export function BarRow({
  label, value, max, colour, format,
}: { label: string; value: number; max: number; colour: string; format?: (v: number) => string }) {
  const w = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-2">
      <div className="h-3 rounded-sm bg-white/5 overflow-hidden" title={label}>
        <div className="h-full rounded-sm transition-all" style={{ width: `${w}%`, background: colour }} />
      </div>
      <span className="text-[11px] tabular-nums text-slate-300 w-12 text-right">
        {(format ?? ((v: number) => v.toFixed(3)))(value)}
      </span>
    </div>
  );
}
