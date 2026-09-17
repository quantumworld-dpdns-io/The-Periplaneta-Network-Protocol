import { Panel } from "./Panel";
import type { Model } from "@/lib/model";

export function Provenance({ model }: { model: Model }) {
  const { by_source, literature, assumptions_needing_sensitivity } = model.parameters;
  const total = Object.values(by_source).reduce((a, b) => a + b, 0);
  return (
    <Panel
      title="Where the numbers come from"
      right={<span>{total} named parameters</span>}
    >
      <div className="flex gap-1.5 mb-3">
        {Object.entries(by_source).map(([k, v]) => (
          <span
            key={k}
            className={`px-2 py-0.5 rounded text-[10px] ${
              k === "literature" ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-300"
            }`}
          >
            {v} {k}
          </span>
        ))}
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 text-[10px]">
        <div>
          <h3 className="uppercase tracking-[0.15em] text-slate-500 mb-1">From the literature</h3>
          <ul className="space-y-0.5 text-slate-400">
            {literature.map((p) => (
              <li key={p.name}>
                <span className="text-slate-200">{p.name}</span> = {p.value} {p.unit}
                <span className="block text-slate-500 pl-3">{p.cite}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="uppercase tracking-[0.15em] text-slate-500 mb-1">
            Assumptions awaiting a sensitivity analysis
          </h3>
          <p className="text-slate-500 mb-1">
            Each declares a sweep range, and the parameter class refuses to construct one without.
            A sweep range is not a measurement.
          </p>
          <div className="flex flex-wrap gap-1">
            {assumptions_needing_sensitivity.map((p) => (
              <span key={p.name} className="px-1.5 py-0.5 rounded bg-white/5 text-slate-400" title={`${p.value} ${p.unit}, swept ${p.sweep[0]}–${p.sweep[1]}`}>
                {p.name}
              </span>
            ))}
          </div>
        </div>
      </div>
    </Panel>
  );
}
