"use client";

import { useEffect, useState } from "react";
import { Panel } from "@/components/Panel";
import { Explain } from "@/components/ui/Explain";
import { ExportButton } from "@/components/ui/ExportButton";
import { ErrorNote } from "@/components/ui/RunState";
import { api, type ParametersResult } from "@/lib/api";
import { useT } from "@/lib/i18n";

export default function Parameters() {
  const t = useT();
  const [data, setData] = useState<ParametersResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.parameters().then(setData).catch((e) => setError(e.message));
  }, []);

  return (
    <>
      <Panel
        title={t("parametersTitle")}
        right={data ? <span>{data.literature.length} + {data.assumptions.length}</span> : null}
      >
        <Explain>{t("parametersExplain")}</Explain>
        <div className="mt-3">
          <ExportButton path="/api/export/parameters.md" label="Markdown" />
        </div>
        <ErrorNote error={error} />
      </Panel>

      {data ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <Panel title={t("fromLiterature")} right={<span>{data.literature.length}</span>}>
            <ul className="space-y-1.5 text-[11px]">
              {data.literature.map((p) => (
                <li key={p.name}>
                  <span className="text-emerald-300">{p.name}</span>{" "}
                  <span className="tabular-nums text-slate-200">{p.value}</span>{" "}
                  <span className="text-slate-500">{p.unit}</span>
                  <div className="text-[10px] text-slate-500 pl-3 leading-snug">{p.cite}</div>
                </li>
              ))}
            </ul>
          </Panel>
          <Panel title={t("assumed")} right={<span>{data.assumptions.length}</span>}>
            <ul className="space-y-1.5 text-[11px]">
              {data.assumptions.map((p) => (
                <li key={p.name}>
                  <span className="text-amber-300">{p.name}</span>{" "}
                  <span className="tabular-nums text-slate-200">{p.value}</span>{" "}
                  <span className="text-slate-500">{p.unit}</span>
                  <span className="text-slate-600"> · {t("sweptOver")} {p.sweep[0]}–{p.sweep[1]}</span>
                  {p.note ? <div className="text-[10px] text-slate-500 pl-3 leading-snug">{p.note}</div> : null}
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      ) : null}
    </>
  );
}
