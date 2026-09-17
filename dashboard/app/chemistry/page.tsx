"use client";

import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/Panel";
import { Button, Select, Toggle } from "@/components/ui/Controls";
import { Caveats, Explain, Reproduce } from "@/components/ui/Explain";
import { ExportButton } from "@/components/ui/ExportButton";
import { ErrorNote } from "@/components/ui/RunState";
import { api, type ChemResult } from "@/lib/api";
import { useT } from "@/lib/i18n";

const MATURITY: Record<string, string> = {
  reference: "text-emerald-300",
  production: "text-sky-300",
  demonstration: "text-amber-300",
};

export default function Chemistry() {
  const t = useT();
  const [req, setReq] = useState({ mutation: "L993F", ligand: "deltamethrin", use_vqe: false });
  const [data, setData] = useState<ChemResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await api.chem(req));
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }, [req]);

  useEffect(() => {
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const rows = data ? [data.reference, ...data.backends] : [];

  return (
    <>
      <Panel title={t("chemistryTitle")}>
        <Explain>{t("chemistryExplain")}</Explain>
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3 mt-3">
          <Select label={t("mutation")} value={req.mutation}
                  onChange={(v) => setReq({ ...req, mutation: v })}
                  options={[{ value: "L993F", label: "kdr L993F (Vssc)" }]} />
          <Select label={t("ligand")} value={req.ligand}
                  onChange={(v) => setReq({ ...req, ligand: v })}
                  options={["deltamethrin", "imidacloprid", "fipronil"].map((v) => ({ value: v, label: v }))} />
        </div>
        <div className="flex items-center gap-3 mt-3 flex-wrap">
          <Toggle label={t("useVqe")} value={req.use_vqe} onChange={(v) => setReq({ ...req, use_vqe: v })} />
          <Button onClick={run} busy={busy}>{busy ? t("running") : t("run")}</Button>
          <ExportButton path="/api/export/chem.md" body={req} label="Markdown" />
        </div>
      </Panel>

      <Panel title="ΔΔG" right={data ? <span>{data.case.mutation} · {data.case.gene} · {data.case.ligand}</span> : null}>
        <ErrorNote error={error} />
        {data ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-[11px] tabular-nums min-w-[34rem]">
                <thead className="text-[10px] uppercase tracking-[0.15em] text-slate-500 text-left">
                  <tr>
                    <th className="py-1">{t("backend")}</th>
                    <th>{t("maturity")}</th>
                    <th className="text-right">ΔΔG kcal/mol</th>
                    <th className="text-right">{t("impliedRR")}</th>
                    <th className="text-right">{t("vsReference")}</th>
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
                        {r.sign_agrees_with_reference === false ? ` · ${t("signWrong")}` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Explain title={t("whatThisMeans")}>{t("chemistryHonest")}</Explain>
            <Reproduce command={data.reproduce} elapsed={data.elapsed_s} />
            <Caveats items={data.caveats} />
          </>
        ) : null}
      </Panel>
    </>
  );
}
