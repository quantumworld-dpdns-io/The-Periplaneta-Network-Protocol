import { Panel } from "./Panel";
import { LOCI, LOCUS_COLOUR, strategyLabel, type Model, type StrategyRow } from "@/lib/model";

function Bar({ value, max, colour }: { value: number; max: number; colour: string }) {
  const w = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <span className="inline-block w-16 h-2 rounded-sm bg-white/5 align-middle overflow-hidden">
      <span className="block h-full rounded-sm" style={{ width: `${w}%`, background: colour }} />
    </span>
  );
}

export function StrategyTable({ model }: { model: Model }) {
  const { summary, config } = model.strategy;
  const best = summary.reduce((a, b) => (b.resistant_runs < a.resistant_runs ? b : a));
  return (
    <Panel
      title="Strategy comparison"
      right={<span>{config.seeds} seeds · {config.generations} generations · matched total dose</span>}
    >
      <table className="w-full text-[11px] tabular-nums">
        <thead className="text-[10px] uppercase tracking-[0.15em] text-slate-500">
          <tr className="text-left">
            <th className="py-1">strategy</th>
            <th>resistant</th>
            <th>median gen</th>
            <th>peak target-site</th>
            <th>survival</th>
            <th className="text-right">kdr</th>
            <th className="text-right">rdl</th>
            <th className="text-right">cyp6</th>
          </tr>
        </thead>
        <tbody>
          {summary.map((r: StrategyRow) => {
            const winner = r.strategy === best.strategy;
            return (
              <tr key={r.strategy} className={`border-t border-white/5 ${winner ? "text-emerald-300" : "text-slate-300"}`}>
                <td className="py-1 pr-2">{strategyLabel(r.strategy)}{winner ? " ←" : ""}</td>
                <td>{r.resistant_runs}/{r.seeds}</td>
                <td>{r.median_time_to_resistance ?? "never"}</td>
                <td>
                  <Bar value={r.peak_target_site_mean} max={1} colour={LOCUS_COLOUR.kdr} />
                  <span className="ml-1.5">{r.peak_target_site_mean.toFixed(3)}</span>
                </td>
                <td>
                  <Bar value={r.mean_survival} max={1} colour="#357" />
                  <span className="ml-1.5">{r.mean_survival.toFixed(3)}</span>
                </td>
                {LOCI.slice(0, 3).map((l) => (
                  <td key={l} className="text-right">{(r[`f_${l}`] as number).toFixed(3)}</td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-3 text-[10px] leading-snug text-slate-500">
        Lower survival is better control. Adult numbers are not shown: every arm sits at
        carrying capacity because the colony rebounds within a generation whatever the kill,
        which is why resistance management rather than knockdown is the question.
        <span className="text-slate-400"> Median generation is over the runs that reached the
        threshold only.</span>
      </p>
    </Panel>
  );
}
