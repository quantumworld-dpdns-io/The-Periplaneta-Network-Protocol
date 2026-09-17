"use client";

import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/Panel";
import { BarRow } from "@/components/ui/Chart";
import { Button, Slider, Toggle } from "@/components/ui/Controls";
import { Caveats, Explain, Reproduce } from "@/components/ui/Explain";
import { ExportButton } from "@/components/ui/ExportButton";
import { ErrorNote } from "@/components/ui/RunState";
import { api, type CompareResult } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { strategyLabel } from "@/lib/model";

export default function StrategyPage() {
  const t = useT();
  const [req, setReq] = useState({
    seeds: 8, generations: 40, population: 400, exposed: 0.6, dose_ld50: 50, linked: false,
  });
  const [data, setData] = useState<CompareResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await api.compare(req));
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

  const best = data?.summary.reduce((a, b) => (b.resistant_runs < a.resistant_runs ? b : a));

  return (
    <>
      <Panel title={t("strategyTitle")}>
        <Explain>{t("strategyExplain")}</Explain>
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mt-3">
          <Slider label={t("seeds")} value={req.seeds} min={1} max={24}
                  onChange={(v) => setReq({ ...req, seeds: v })} />
          <Slider label={t("generations")} value={req.generations} min={5} max={120}
                  onChange={(v) => setReq({ ...req, generations: v })} />
          <Slider label={t("populationSize")} value={req.population} min={50} max={1200} step={50}
                  onChange={(v) => setReq({ ...req, population: v })} />
          <Slider label={t("exposedFraction")} value={req.exposed} min={0} max={1} step={0.05}
                  format={(v) => `${Math.round(v * 100)}%`}
                  onChange={(v) => setReq({ ...req, exposed: v })} />
          <Slider label={t("doseLd50")} value={req.dose_ld50} min={0} max={200} step={5}
                  format={(v) => `${v}×`}
                  onChange={(v) => setReq({ ...req, dose_ld50: v })} />
        </div>
        <div className="flex items-center gap-3 mt-3 flex-wrap">
          <Toggle label={t("linkedLoci")} value={req.linked} onChange={(v) => setReq({ ...req, linked: v })} />
          <Button onClick={run} busy={busy}>{busy ? t("running") : t("run")}</Button>
          <ExportButton path="/api/export/compare.csv" body={req} label="CSV" />
        </div>
      </Panel>

      <Panel title={t("navStrategy")}>
        <ErrorNote error={error} />
        {data ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-[11px] tabular-nums min-w-[36rem]">
                <thead className="text-[10px] uppercase tracking-[0.15em] text-slate-500 text-left">
                  <tr>
                    <th className="py-1">{t("strategy")}</th>
                    <th>{t("resistantRuns")}</th>
                    <th>{t("medianGen")}</th>
                    <th className="w-40">{t("peakTargetSite")}</th>
                    <th className="w-40">{t("meanSurvival")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.summary.map((r) => (
                    <tr key={r.strategy}
                        className={`border-t border-white/5 ${r.strategy === best?.strategy ? "text-emerald-300" : "text-slate-300"}`}>
                      <td className="py-1 pr-3">{strategyLabel(r.strategy)}</td>
                      <td>{r.resistant_runs}/{r.seeds}</td>
                      <td>{r.median_time_to_resistance ?? t("never")}</td>
                      <td className="pr-3"><BarRow label="" value={r.peak_target_site_mean} max={1} colour="#e05252" /></td>
                      <td><BarRow label="" value={r.mean_survival} max={1} colour="#3b82f6" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-[10px] text-slate-500">{t("meanSurvival")}: {t("lowerIsBetter")}</p>
            <Explain title={t("whatThisMeans")}>{t("strategyTradeoff")}</Explain>
            <Reproduce command={data.reproduce} elapsed={data.elapsed_s} />
            <Caveats items={data.caveats} />
          </>
        ) : null}
      </Panel>
    </>
  );
}
