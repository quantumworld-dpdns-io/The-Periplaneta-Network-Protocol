"use client";

import { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/Panel";
import { Button, Select, Slider } from "@/components/ui/Controls";
import { Caveats, Explain, Reproduce } from "@/components/ui/Explain";
import { ErrorNote } from "@/components/ui/RunState";
import { api, type ContactResult } from "@/lib/api";
import { useT } from "@/lib/i18n";

const BAITS = ["", "deltamethrin", "imidacloprid", "fipronil"] as const;

export default function Colony() {
  const t = useT();
  const [req, setReq] = useState({
    colony: 150, hours: 4, harborages: 6, resources: 3, arena_cm: 300,
    bait: "" as string, stations: 1, seed: 1,
  });
  const [data, setData] = useState<ContactResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await api.contact({ ...req, bait: req.bait || null }));
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

  const size = data?.config.arena_cm ?? req.arena_cm;
  const maxDeg = data ? Math.max(1, ...data.degree) : 1;
  const tox = data?.toxicology;

  return (
    <>
      <Panel title={t("colonyTitle")}>
        <Explain>{t("colonyExplain")}</Explain>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mt-3">
          <Slider label={t("colonySize")} value={req.colony} min={20} max={400} step={10}
                  onChange={(v) => setReq({ ...req, colony: v })} />
          <Slider label={t("hours")} value={req.hours} min={1} max={24}
                  format={(v) => `${v} h`}
                  onChange={(v) => setReq({ ...req, hours: v })} />
          <Slider label={t("harborages")} value={req.harborages} min={1} max={20}
                  onChange={(v) => setReq({ ...req, harborages: v })} />
          <Slider label={t("resources")} value={req.resources} min={1} max={10}
                  onChange={(v) => setReq({ ...req, resources: v })} />
          <Select label={t("bait")} value={req.bait}
                  onChange={(v) => setReq({ ...req, bait: v })}
                  options={BAITS.map((b) => ({ value: b, label: b || t("noBait") }))} />
          <Slider label={t("stations")} value={req.stations} min={0} max={req.resources}
                  onChange={(v) => setReq({ ...req, stations: v })} />
          <Slider label={t("seed")} value={req.seed} min={1} max={50}
                  onChange={(v) => setReq({ ...req, seed: v })} />
        </div>
        <div className="mt-3">
          <Button onClick={run} busy={busy}>{busy ? t("running") : t("run")}</Button>
        </div>
      </Panel>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <Panel title={t("navColony")}>
          <ErrorNote error={error} />
          {data ? (
            <>
              <svg viewBox={`0 0 ${size} ${size}`}
                   className="w-full max-w-[420px] h-auto rounded border border-white/5 bg-black/30 mx-auto">
                {data.harborages.map((h, i) => (
                  <circle key={`h${i}`} cx={h.x} cy={h.y} r={h.r * 2.2}
                          fill="#2a3550" stroke="#4a6a9a" strokeWidth="1.2" />
                ))}
                {data.resources.map((r, i) => (
                  <circle key={`r${i}`} cx={r.x} cy={r.y} r={r.r * 2.4}
                          fill={data.treated.includes(i) ? "#4a2020" : "#3a3520"}
                          stroke={data.treated.includes(i) ? "#d05555" : "#9a8a4a"} strokeWidth="1.5" />
                ))}
                {data.positions.map((p, i) => (
                  <circle key={i} cx={p[0]} cy={p[1]} r={2.4}
                          fill={data.alive[i]
                            ? `hsl(${20 + (data.degree[i] / maxDeg) * 20}, 78%, ${34 + (data.degree[i] / maxDeg) * 36}%)`
                            : "#3b4252"}
                          opacity={data.alive[i] ? 0.92 : 0.55} />
                ))}
              </svg>
              <div className="flex gap-4 justify-center mt-2 text-[10px] text-slate-500">
                <span><span className="inline-block w-2 h-2 rounded-full bg-[#2a3550] border border-[#4a6a9a] mr-1" />{t("legendHarborage")}</span>
                <span><span className="inline-block w-2 h-2 rounded-full bg-[#3a3520] border border-[#9a8a4a] mr-1" />{t("legendFood")}</span>
                <span>{t("legendRoach")}</span>
              </div>
            </>
          ) : null}
        </Panel>

        <Panel title={t("contacts")}>
          {data ? (
            <>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] tabular-nums">
                {[
                  [t("contacts"), data.network.edges],
                  [t("density"), data.network.density],
                  [t("meanDegree"), data.network.mean_degree],
                  [t("isolated"), data.network.isolated],
                  [t("largestComponent"), data.network.largest_component_fraction],
                  ...(tox
                    ? [
                        [t("dead"), tox.dead],
                        [t("fedAtBait"), tox.fed_at_bait],
                        [t("secondaryKill"), Math.max(0, tox.dead - tox.fed_at_bait)],
                      ]
                    : []),
                ].map(([k, v]) => (
                  <div key={String(k)} className="contents">
                    <dt className="text-slate-500">{k}</dt>
                    <dd className="text-slate-200">{String(v)}</dd>
                  </div>
                ))}
              </dl>
              {tox ? (
                <div className="mt-3">
                  <div className="text-[10px] uppercase tracking-[0.15em] text-slate-500 mb-1">
                    {t("bait")} → {Object.keys(tox.acquisition_routes).join(" / ")}
                  </div>
                  <div className="flex h-3 rounded overflow-hidden">
                    {Object.entries(tox.acquisition_routes).map(([k, v], i) => (
                      <div key={k} title={`${k} ${(v * 100).toFixed(1)}%`}
                           style={{ width: `${v * 100}%`, background: ["#d08a3a", "#3a8ad0", "#6fc27a", "#a97fd0"][i] }} />
                    ))}
                  </div>
                </div>
              ) : null}
              <Reproduce command={data.reproduce} elapsed={data.elapsed_s} />
              <Caveats items={data.caveats} />
            </>
          ) : null}
        </Panel>
      </div>
    </>
  );
}
