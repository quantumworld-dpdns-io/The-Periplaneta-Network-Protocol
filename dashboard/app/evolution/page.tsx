"use client";

import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/Panel";
import { Legend, LineChart, type Series } from "@/components/ui/Chart";
import { Button, Select, Slider, Toggle } from "@/components/ui/Controls";
import { Caveats, Explain, Reproduce } from "@/components/ui/Explain";
import { ExportButton } from "@/components/ui/ExportButton";
import { ErrorNote } from "@/components/ui/RunState";
import { api, type EvolveResult } from "@/lib/api";
import { useLang, useT } from "@/lib/i18n";
import { LOCI, LOCUS_COLOUR } from "@/lib/model";

const STRATEGIES = ["single", "rotation", "rotation3", "mixture", "untreated"] as const;
type Strategy = (typeof STRATEGIES)[number];

const LABEL: Record<Strategy, { en: string; zh: string }> = {
  single: { en: "one product only", zh: "只用一種藥" },
  rotation: { en: "rotate every generation", zh: "每代輪替" },
  rotation3: { en: "rotate every 3 generations", zh: "每三代輪替" },
  mixture: { en: "mixture, matched dose", zh: "混合，總量相同" },
  untreated: { en: "no insecticide", zh: "不用藥" },
};

export default function Evolution() {
  const t = useT();
  const { lang } = useLang();
  const [req, setReq] = useState({
    strategy: "single" as Strategy,
    generations: 40,
    population: 400,
    exposed: 0.6,
    dose_ld50: 50,
    founder_frequency: 0.05,
    fitness_cost: 0.1,
    linked: false,
    seed: 1,
  });
  const [data, setData] = useState<EvolveResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await api.evolve(req));
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }, [req]);

  useEffect(() => {
    void run();
    // first load only; afterwards the Run button is in charge
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const series: Series[] = data
    ? LOCI.map((l) => ({
        label: l,
        colour: LOCUS_COLOUR[l],
        points: data.history.map((h) => ({ x: h.generation, y: Number(h[`f_${l}`] ?? 0) })),
      }))
    : [];

  return (
    <>
      <Panel title={t("evolutionTitle")}>
        <Explain>{t("evolutionExplain")}</Explain>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mt-3">
          <Select
            label={t("strategy")}
            value={req.strategy}
            onChange={(v) => setReq({ ...req, strategy: v })}
            options={STRATEGIES.map((s) => ({ value: s, label: LABEL[s][lang] }))}
          />
          <Slider label={t("generations")} value={req.generations} min={5} max={120}
                  onChange={(v) => setReq({ ...req, generations: v })} />
          <Slider label={t("populationSize")} value={req.population} min={50} max={1200} step={50}
                  onChange={(v) => setReq({ ...req, population: v })} />
          <Slider label={t("seed")} value={req.seed} min={1} max={50}
                  onChange={(v) => setReq({ ...req, seed: v })} />
          <Slider label={t("exposedFraction")} value={req.exposed} min={0} max={1} step={0.05}
                  format={(v) => `${Math.round(v * 100)}%`}
                  onChange={(v) => setReq({ ...req, exposed: v })} />
          <Slider label={t("doseLd50")} value={req.dose_ld50} min={0} max={200} step={5}
                  format={(v) => `${v}×`}
                  onChange={(v) => setReq({ ...req, dose_ld50: v })} />
          <Slider label={t("founderFrequency")} value={req.founder_frequency} min={0} max={1} step={0.05}
                  format={(v) => v.toFixed(2)}
                  onChange={(v) => setReq({ ...req, founder_frequency: v })} />
          <Slider label={t("fitnessCost")} value={req.fitness_cost} min={0} max={0.5} step={0.02}
                  format={(v) => v.toFixed(2)}
                  onChange={(v) => setReq({ ...req, fitness_cost: v })} />
        </div>
        <div className="flex items-center gap-3 mt-3 flex-wrap">
          <Toggle label={t("linkedLoci")} value={req.linked} onChange={(v) => setReq({ ...req, linked: v })} />
          <Button onClick={run} busy={busy}>{busy ? t("running") : t("run")}</Button>
          <ExportButton path="/api/export/evolve.csv" body={req} label="CSV" />
        </div>
      </Panel>

      <Panel title={t("alleleFrequency")} right={data ? <span>{data.strategy.description}</span> : null}>
        <ErrorNote error={error} />
        {data ? (
          <>
            <LineChart series={series} xMax={data.generations_run} yMax={1} threshold={0.5}
                       xLabel={t("generation")} yLabel={t("alleleFrequency")} />
            <Legend series={series} />
            <Reproduce command={data.reproduce} elapsed={data.elapsed_s} />
            <Caveats items={data.caveats} />
          </>
        ) : null}
      </Panel>

      <Explain title={t("tryThis")}>{t("evolutionTry")}</Explain>
    </>
  );
}
