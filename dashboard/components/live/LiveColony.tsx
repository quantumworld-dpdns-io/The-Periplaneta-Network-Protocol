"use client";

/**
 * A colony running right now, and every lever the model exposes for meddling
 * with it.
 *
 * The session lives on the server: this component starts one, opens an
 * EventSource against it, and posts actions back. Nothing about the simulation
 * is computed in the browser, so what you see on screen is the same state the
 * CLI and the other pages would produce -- the page is a window onto the model,
 * not a second implementation of it.
 *
 * The session is stopped on unmount and reaped server-side after fifteen idle
 * minutes, so a forgotten tab cannot pin a simulation forever.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Panel } from "@/components/Panel";
import { Button, Select, Slider } from "@/components/ui/Controls";
import { Explain } from "@/components/ui/Explain";
import { ErrorNote } from "@/components/ui/RunState";
import { api, type LiveAction, type LiveFrame, type LiveStart } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { HeatCanvas, type LayerName } from "./HeatCanvas";

const FPS = 4;

function Stat({ label, value, tone = "" }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[9px] uppercase tracking-[0.14em] text-slate-500 truncate">{label}</div>
      <div className={`text-[15px] tabular-nums ${tone || "text-slate-200"}`}>{value}</div>
    </div>
  );
}

export function LiveColony() {
  const t = useT();
  const [start, setStart] = useState<LiveStart | null>(null);
  const [frame, setFrame] = useState<LiveFrame | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [layer, setLayer] = useState<LayerName>("pheromone");
  const [active, setActive] = useState("fipronil");
  const [stations, setStations] = useState(1);
  const [aggregation, setAggregation] = useState(1.2);
  const [strength, setStrength] = useState(2.15);
  const [speed, setSpeed] = useState(30);
  const [lights, setLights] = useState("auto");
  const [nonce, setNonce] = useState(0);
  const sessionRef = useRef<string | null>(null);

  // --- session lifecycle ----------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    let source: EventSource | null = null;

    api
      .liveStart({ colony: 160, harborages: 6, resources: 4, speed: 30 })
      .then((s) => {
        if (cancelled) { void api.liveStop(s.session); return; }
        sessionRef.current = s.session;
        setStart(s);
        setFrame(s.frame);
        setError(null);
        source = new EventSource(api.liveStreamUrl(s.session, FPS));
        source.onmessage = (e) => {
          const f = JSON.parse(e.data);
          if (!f.end) setFrame(f);
        };
        // the browser retries on its own; only say something if it stays down
        source.onerror = () => {
          if (source?.readyState === EventSource.CLOSED) setError(t("apiDown"));
        };
      })
      .catch((e) => !cancelled && setError(e.message));

    return () => {
      cancelled = true;
      source?.close();
      const id = sessionRef.current;
      sessionRef.current = null;
      if (id) void api.liveStop(id);
    };
    // `nonce` restarts the whole session; `t` only changes the error wording
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce]);

  const act = useCallback((action: LiveAction, params: Record<string, unknown> = {}) => {
    const id = sessionRef.current;
    if (!id) return;
    api.liveAct(id, action, params).then((r) => setFrame(r.frame)).catch((e) => setError(e.message));
  }, []);

  if (error && !frame) {
    return <Panel title={t("liveTitle")}><ErrorNote error={error} /></Panel>;
  }
  if (!start || !frame) {
    return <Panel title={t("liveTitle")}><p className="text-[11px] text-slate-500">{t("connecting")}</p></Panel>;
  }

  const c = frame.counts;
  const clock = `${String(Math.floor(frame.t_hours)).padStart(2, "0")}:${String(
    Math.floor((frame.t_hours % 1) * 60)).padStart(2, "0")}`;
  const peak = frame.layers[layer].peak;

  return (
    <>
      <Panel
        title={t("liveTitle")}
        right={
          <span className="flex items-center gap-2">
            <span className={frame.dark ? "text-indigo-300" : "text-amber-300"}>
              {clock} {frame.dark ? t("night") : t("day")}
            </span>
            <span className="text-slate-600">+{frame.elapsed_hours.toFixed(1)} h</span>
          </span>
        }
        bodyClassName="grid gap-3 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]"
      >
        {/* ---------------------------------------------------------- canvas */}
        <div className="min-w-0">
          <HeatCanvas
            frame={frame}
            layer={layer}
            arenaCm={start.arena_cm}
            harborages={start.harborages}
            resources={start.resources}
            colourByState
            onStationClick={(i) => act("toggle_station", { index: i, active })}
          />
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-500">
            <span className="flex items-center gap-1">
              <i className="inline-block w-2 h-2 rounded-full bg-slate-400" />{t("resting")}
            </span>
            <span className="flex items-center gap-1">
              <i className="inline-block w-2 h-2 rounded-full bg-amber-400" />{t("foraging")}
            </span>
            <span className="flex items-center gap-1">
              <i className="inline-block w-2 h-2 rounded-full bg-sky-400" />{t("returning")}
            </span>
            <span className="flex items-center gap-1">
              <i className="inline-block w-2 h-2 rounded-full bg-red-400" />{t("burden")}
            </span>
            <span className="flex items-center gap-1">
              <i className="inline-block w-2 h-2 rounded-full border border-dashed border-slate-400" />
              {t("legendHarborage")}
            </span>
            <span className="flex items-center gap-1">
              <i className="inline-block w-2 h-2 rounded-full border border-amber-400" />{t("legendFood")}
            </span>
            <span className="flex items-center gap-1">
              <i className="inline-block w-2 h-2 rounded-full border-2 border-red-400" />{t("bait")}
            </span>
            <span className="ml-auto tabular-nums">{t("peakValue")} {peak.toPrecision(3)}</span>
            <span className="basis-full text-slate-600">
              {t("clickToBait")} · {t("stationVisits")}
            </span>
          </div>
        </div>

        {/* -------------------------------------------------------- controls */}
        <div className="min-w-0 flex flex-col gap-3">
          <div className="grid grid-cols-3 gap-2">
            <Stat label={t("alive")} value={c.alive} tone="text-emerald-300" />
            <Stat label={t("liveDead")} value={c.dead} tone={c.dead ? "text-red-400" : ""} />
            <Stat label={t("contacts")} value={frame.network.edges ?? 0} />
            <Stat label={t("resting")} value={c.resting ?? 0} />
            <Stat label={t("foraging")} value={c.foraging ?? 0} />
            <Stat label={t("fedAtBait")} value={c.fed_at_bait ?? 0} tone="text-amber-300" />
          </div>

          <div className="flex flex-wrap gap-1.5">
            <Button onClick={() => act("pause", { paused: !frame.paused })}>
              {frame.paused ? t("play") : t("pause")}
            </Button>
            <Button
              kind="ghost"
              onClick={() => { setLights("auto"); setSpeed(30); setNonce((n) => n + 1); }}
            >
              {t("restart")}
            </Button>
            <Button kind="ghost" onClick={() => act("add", { n: 20 })}>{t("immigrate")}</Button>
            <Button kind="ghost" onClick={() => act("remove", { n: 20 })}>{t("trap")}</Button>
          </div>

          <Select<LayerName>
            label={t("layer")} value={layer} onChange={setLayer}
            options={[
              { value: "pheromone", label: t("layerPheromone") },
              { value: "residue", label: t("layerResidue") },
              { value: "density", label: t("layerDensity") },
            ]}
          />

          <Select
            label={t("lights")}
            value={lights}
            onChange={(v: string) => { setLights(v); act("light", { phase: v }); }}
            options={[
              { value: "auto", label: t("lightsAuto") },
              { value: "dark", label: t("lightsDark") },
              { value: "light", label: t("lightsLight") },
            ]}
          />

          <div className="grid grid-cols-2 gap-2 items-end">
            <Select
              label={t("bait")} value={active} onChange={setActive}
              options={start.actives.map((a) => ({ value: a, label: a }))}
            />
            <Slider
              label={t("stations")} value={stations} onChange={setStations}
              min={1} max={start.resources.length} step={1}
              hint={t("clickToBait")}
            />
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Button onClick={() => act("bait", { active, stations })}>{t("baitBusiest")}</Button>
            <Button kind="ghost" onClick={() => act("clear_bait")}>{t("clearBait")}</Button>
          </div>

          <Slider
            label={t("speed")} value={speed}
            onChange={(v) => { setSpeed(v); act("speed", { speed: v }); }}
            min={1} max={120} step={1} format={(v) => `${v}s`}
            hint={t("simSecondsPerFrame")}
          />
          <Slider
            label={t("aggregation")} value={aggregation}
            onChange={(v) => { setAggregation(v); act("aggregation", { value: v }); }}
            min={0} max={4} step={0.1} format={(v) => v.toFixed(1)}
          />
          <Slider
            label={t("baitStrength")} value={strength}
            onChange={(v) => { setStrength(v); act("concentration", { value: v / 100 }); }}
            min={0} max={10} step={0.05} format={(v) => `${v.toFixed(2)}%`}
          />

          {frame.events.length > 0 ? (
            <div className="border-t border-white/5 pt-2">
              <div className="text-[9px] uppercase tracking-[0.14em] text-slate-500 mb-1">{t("eventLog")}</div>
              <ul className="text-[10px] text-slate-400 font-mono space-y-0.5 max-h-24 overflow-y-auto">
                {[...frame.events].reverse().map((e, i) => (
                  <li key={i}><span className="text-slate-600">{e.t_hours.toFixed(1)}h</span> {e.note}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </Panel>

      <Explain title={t("whatThisMeans")}>
        <p>{t("liveExplain")}</p>
        <p className="mt-2 text-amber-200/90"><strong>{t("tryThis")}:</strong> {t("liveTry")}</p>
      </Explain>
    </>
  );
}
