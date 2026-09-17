import { Panel } from "./Panel";
import type { Model } from "@/lib/model";

const MATURITY: Record<string, string> = {
  reference: "text-emerald-300",
  production: "text-sky-300",
  demonstration: "text-amber-300",
};

export function ChemistryTable({ model }: { model: Model }) {
  const { case: c, reference, backends } = model.chemistry;
  const rows = [reference, ...backends];
  const wrongSign = backends.filter((b) => b.sign_agrees_with_reference === false);
  return (
    <Panel title="Binding free energy" right={<span>{c.mutation} in {c.target} ({c.gene}) vs {c.ligand}</span>}>
      <table className="w-full text-[11px] tabular-nums">
        <thead className="text-[10px] uppercase tracking-[0.15em] text-slate-500">
          <tr className="text-left">
            <th className="py-1">backend</th>
            <th>maturity</th>
            <th className="text-right">ΔΔG kcal/mol</th>
            <th className="text-right">implied RR</th>
            <th className="text-right">vs reference</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.backend} className="border-t border-white/5 text-slate-300">
              <td className="py-1">{r.backend}</td>
              <td className={MATURITY[r.maturity] ?? "text-slate-400"}>{r.maturity}</td>
              <td className="text-right">
                {r.ddg_kcal_per_mol > 0 ? "+" : ""}{r.ddg_kcal_per_mol.toFixed(3)}
                <span className="text-slate-500"> ± {r.uncertainty.toFixed(3)}</span>
              </td>
              <td className="text-right">{r.implied_resistance_ratio}</td>
              <td className={`text-right ${r.sign_agrees_with_reference === false ? "text-rose-300" : ""}`}>
                {r.error_vs_reference_kcal === undefined
                  ? "—"
                  : `${r.error_vs_reference_kcal > 0 ? "+" : ""}${r.error_vs_reference_kcal.toFixed(3)}`}
                {r.sign_agrees_with_reference === false ? " (sign wrong)" : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-[10px] leading-snug text-slate-500">
        Positive means weaker binding, i.e. resistance. The reference is a{" "}
        <span className="text-slate-300">measurement</span>, not a model: a resistance ratio of{" "}
        {reference.implied_resistance_ratio} implies RT ln(RR). The population layer uses it, not
        the backends.
        {wrongSign.length > 0 && (
          <>
            {" "}
            <span className="text-rose-300">
              {wrongSign.map((b) => b.backend).join(", ")} disagrees with the measurement on whether
              the mutation confers resistance at all.
            </span>{" "}
            The two backends fail in opposite directions, which says the dominant term is channel
            geometry rather than either sterics or electronic structure.
          </>
        )}
      </p>
    </Panel>
  );
}
