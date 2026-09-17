"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { LiveColony } from "@/components/live/LiveColony";
import { Panel } from "@/components/Panel";
import { Caveats, Explain, Reproduce } from "@/components/ui/Explain";
import { ErrorNote } from "@/components/ui/RunState";
import { BarRow } from "@/components/ui/Chart";
import { api, type CompareResult } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { strategyLabel } from "@/lib/model";

export default function Overview() {
  const t = useT();
  const [data, setData] = useState<CompareResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // a small live run, so the front page is the model rather than a picture of it
    api
      .compare({ seeds: 4, generations: 30, population: 300 })
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  const best = data?.summary.reduce((a, b) => (b.resistant_runs < a.resistant_runs ? b : a));

  return (
    <>
      <Panel title={t("theQuestion")}>
        <p className="text-[12px] text-slate-200 leading-relaxed">{t("tagline")}</p>
        <p className="mt-2 text-[11px] text-slate-400 leading-relaxed">{t("overviewIntro")}</p>
      </Panel>

      <LiveColony />

      <Panel title={t("theAnswer")} right={data ? <span>{data.config.seeds} × {data.config.generations}</span> : null}>
        <ErrorNote error={error} />
        {data && best ? (
          <>
            <p className="text-[13px] text-emerald-300 mb-3">{strategyLabel(best.strategy)}</p>
            <div className="grid gap-1.5">
              {data.summary.map((r) => (
                <div key={r.strategy} className="grid grid-cols-[minmax(9rem,14rem)_1fr] items-center gap-3">
                  <span className={`text-[11px] truncate ${r.strategy === best.strategy ? "text-emerald-300" : "text-slate-400"}`}>
                    {strategyLabel(r.strategy)}
                  </span>
                  <BarRow
                    label={r.strategy}
                    value={r.peak_target_site_mean}
                    max={1}
                    colour={r.strategy === best.strategy ? "#34d399" : "#e05252"}
                  />
                </div>
              ))}
            </div>
            <p className="mt-2 text-[10px] text-slate-500">{t("peakTargetSite")} · 0 – 1</p>
            <Reproduce command={data.reproduce} elapsed={data.elapsed_s} />
            <Caveats items={data.caveats} />
          </>
        ) : error ? null : (
          <p className="text-[11px] text-slate-500">{t("loading")}</p>
        )}
      </Panel>

      <Explain title={t("startHere")}>
        <p>{t("overviewGuide")}</p>
        <p className="mt-2 flex gap-3">
          <Link href="/strategy" className="text-amber-300 hover:underline">→ {t("navStrategy")}</Link>
          <Link href="/colony" className="text-amber-300 hover:underline">→ {t("navColony")}</Link>
        </p>
      </Explain>
    </>
  );
}
