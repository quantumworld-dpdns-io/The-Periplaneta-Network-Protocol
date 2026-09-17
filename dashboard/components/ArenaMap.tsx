import { Panel } from "./Panel";
import type { Model } from "@/lib/model";

export function ArenaMap({ model }: { model: Model }) {
  const { config, harborages, resources, positions, degree, network, trips } = model.contact;
  const size = config.arena_cm;
  const maxDeg = Math.max(1, ...degree);
  return (
    <Panel
      title="Colony and contact network"
      right={<span>{config.n} individuals · {config.hours} h · {size}×{size} cm</span>}
    >
      <div className="flex flex-col lg:flex-row gap-4">
        <svg viewBox={`0 0 ${size} ${size}`} className="w-full max-w-[320px] h-auto rounded border border-white/5 bg-black/30">
          {harborages.map((h, i) => (
            <circle key={`h${i}`} cx={h.x} cy={h.y} r={h.r * 2.2} fill="#2a3550" stroke="#4a6a9a" strokeWidth="1.2" />
          ))}
          {resources.map((r, i) => (
            <circle key={`r${i}`} cx={r.x} cy={r.y} r={r.r * 2.2} fill="#3a3520" stroke="#9a8a4a" strokeWidth="1.2" />
          ))}
          {positions.map((p, i) => (
            <circle
              key={i}
              cx={p[0]}
              cy={p[1]}
              r={2.2}
              fill={`hsl(${20 + (degree[i] / maxDeg) * 20}, 75%, ${35 + (degree[i] / maxDeg) * 35}%)`}
              opacity={0.9}
            />
          ))}
        </svg>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] tabular-nums content-start min-w-0">
          {[
            ["contacts (edges)", network.edges],
            ["density", network.density],
            ["mean degree", network.mean_degree],
            ["max degree", network.max_degree],
            ["isolated", network.isolated],
            ["largest component", network.largest_component_fraction],
            ["harborages", config.harborages],
            ["food sites", config.resources],
            ["mean trips per animal", (trips.reduce((a, b) => a + b, 0) / Math.max(trips.length, 1)).toFixed(1)],
          ].map(([k, v]) => (
            <div key={String(k)} className="contents">
              <dt className="text-slate-500">{k}</dt>
              <dd className="text-slate-200">{String(v)}</dd>
            </div>
          ))}
        </dl>
      </div>
      <p className="mt-3 text-[10px] leading-snug text-slate-500">
        Blue discs are harborages, amber are food. Each dot is one animal, brighter where it has
        more contact partners. The network is <span className="text-slate-300">not prescribed</span>:
        contacts emerge because animals seek the same refuges, follow the same pheromone marks and
        feed at the same places. Delete one animal and the rest end up elsewhere, which is the
        property the whole rebuild turned on.
      </p>
    </Panel>
  );
}
