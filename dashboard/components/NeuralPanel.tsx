import { Panel } from "./Panel";
import type { Model } from "@/lib/model";

export function NeuralPanel({ model }: { model: Model }) {
  const { rows, burden_ld50, species_caveat, testable_orderings, known_blind_spots } = model.neural;
  const actives = [...new Set(rows.map((r) => r.active))];
  const hours = [...new Set(rows.map((r) => r.hours))].sort((a, b) => a - b);
  const at = (a: string, h: number) => rows.find((r) => r.active === a && r.hours === h);
  return (
    <Panel title="Predicted neural signature" right={<span>{burden_ld50} LD50, sub-lethal</span>}>
      <table className="w-full text-[11px] tabular-nums">
        <thead className="text-[10px] uppercase tracking-[0.15em] text-slate-500">
          <tr className="text-left">
            <th className="py-1">active</th>
            {hours.map((h) => <th key={h} className="text-right">{h} h</th>)}
          </tr>
        </thead>
        <tbody>
          {actives.map((a) => (
            <tr key={a} className="border-t border-white/5 text-slate-300">
              <td className="py-1">{a}</td>
              {hours.map((h) => {
                const r = at(a, h);
                const fold = r?.rate_fold ?? 1;
                return (
                  <td key={h} className={`text-right ${fold > 1.05 ? "text-rose-300" : fold < 0.95 ? "text-sky-300" : ""}`}>
                    {fold.toFixed(2)}×
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-[10px] text-slate-500">Firing rate relative to baseline. <span className="italic">{species_caveat}.</span></p>
      <div className="mt-3 grid grid-cols-1 lg:grid-cols-2 gap-3 text-[10px] leading-snug">
        <div>
          <h3 className="uppercase tracking-[0.15em] text-slate-500 mb-1">What would falsify this</h3>
          <ol className="list-decimal list-inside text-slate-400 space-y-0.5">
            {testable_orderings.map((t) => <li key={t}>{t}</li>)}
          </ol>
        </div>
        <div>
          <h3 className="uppercase tracking-[0.15em] text-slate-500 mb-1">What it cannot tell apart</h3>
          <ul className="list-disc list-inside text-slate-400 space-y-0.5">
            {known_blind_spots.map((t) => <li key={t}>{t}</li>)}
          </ul>
        </div>
      </div>
    </Panel>
  );
}
