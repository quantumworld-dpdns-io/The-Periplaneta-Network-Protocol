"use client";

import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/Panel";
import { Button, Slider } from "@/components/ui/Controls";
import { Caveats, Explain, Reproduce } from "@/components/ui/Explain";
import { ExportButton } from "@/components/ui/ExportButton";
import { ErrorNote } from "@/components/ui/RunState";
import { api, type NeuralResult } from "@/lib/api";
import { useT } from "@/lib/i18n";

const HOURS = [0.5, 2, 8, 24];

export default function Neural() {
  const t = useT();
  const [req, setReq] = useState({ burden_ld50: 0.5, hours: HOURS, seed: 0 });
  const [data, setData] = useState<NeuralResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await api.neural(req));
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

  const actives = data ? [...new Set(data.rows.map((r) => r.active))] : [];
  const at = (a: string, h: number) => data?.rows.find((r) => r.active === a && r.hours === h);

  return (
    <>
      <Panel title={t("neuralTitle")}>
        <Explain>{t("neuralExplain")}</Explain>
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3 mt-3">
          <Slider label={t("burden")} value={req.burden_ld50} min={0} max={5} step={0.1}
                  format={(v) => `${v.toFixed(1)}×`}
                  onChange={(v) => setReq({ ...req, burden_ld50: v })} />
          <Slider label={t("seed")} value={req.seed} min={0} max={30}
                  onChange={(v) => setReq({ ...req, seed: v })} />
        </div>
        <div className="flex items-center gap-3 mt-3 flex-wrap">
          <Button onClick={run} busy={busy}>{busy ? t("running") : t("run")}</Button>
          <ExportButton path="/api/export/neural.md" body={req} label="Markdown" />
        </div>
      </Panel>

      <Panel title={t("navNeural")}>
        <ErrorNote error={error} />
        {data ? (
          <>
            <table className="w-full text-[11px] tabular-nums">
              <thead className="text-[10px] uppercase tracking-[0.15em] text-slate-500 text-left">
                <tr>
                  <th className="py-1">{t("ligand")}</th>
                  {req.hours.map((h) => <th key={h} className="text-right">{h} h</th>)}
                </tr>
              </thead>
              <tbody>
                {actives.map((a) => (
                  <tr key={a} className="border-t border-white/5 text-slate-300">
                    <td className="py-1">{a}</td>
                    {req.hours.map((h) => {
                      const r = at(a, h);
                      const fold = r?.rate_fold ?? 1;
                      return (
                        <td key={h} className={`text-right ${fold > 1.05 ? "text-rose-300" : fold < 0.95 ? "text-sky-300" : ""}`}>
                          {r?.silent ? "—" : `${fold.toFixed(2)}×`}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-[10px] text-slate-500 italic">{data.species_caveat}.</p>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3 text-[10px] leading-snug">
              <div>
                <h3 className="uppercase tracking-[0.15em] text-slate-500 mb-1">{t("wouldFalsify")}</h3>
                <ol className="list-decimal list-inside text-slate-400 space-y-0.5">
                  {data.testable_orderings.map((x) => <li key={x}>{x}</li>)}
                </ol>
              </div>
              <div>
                <h3 className="uppercase tracking-[0.15em] text-slate-500 mb-1">{t("cannotTellApart")}</h3>
                <ul className="list-disc list-inside text-slate-400 space-y-0.5">
                  {data.known_blind_spots.map((x) => <li key={x}>{x}</li>)}
                </ul>
              </div>
            </div>
            <Reproduce command={data.reproduce} />
            <Caveats items={data.caveats} />
          </>
        ) : null}
      </Panel>
    </>
  );
}
