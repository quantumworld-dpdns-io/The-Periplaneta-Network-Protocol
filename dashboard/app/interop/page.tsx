"use client";

import { useEffect, useState } from "react";
import { Panel } from "@/components/Panel";
import { Explain } from "@/components/ui/Explain";
import { ErrorNote } from "@/components/ui/RunState";
import { api, type InteropDiscovery, type FormatSpec } from "@/lib/api";
import { useT } from "@/lib/i18n";

const AVAILABILITY_COLORS = {
  ready: "bg-emerald-950 text-emerald-200 border-emerald-700",
  degraded: "bg-yellow-950 text-yellow-200 border-yellow-700",
  needs_data: "bg-red-950 text-red-200 border-red-700",
  unsupported: "bg-slate-950 text-slate-300 border-slate-700",
};

interface FormatCardProps {
  spec: FormatSpec;
}

function FormatCard({ spec }: FormatCardProps) {
  return (
    <div className="border border-slate-700 rounded-md p-4 bg-slate-900/50">
      <div className="flex items-start justify-between gap-2 mb-2">
        <div>
          <h3 className="text-sm font-semibold text-slate-100">{spec.title}</h3>
          <p className="text-xs text-slate-400 mt-0.5">{spec.filename}</p>
        </div>
        <span
          className={`text-xs px-2 py-1 rounded-sm border whitespace-nowrap ${
            AVAILABILITY_COLORS[spec.availability]
          }`}
        >
          {spec.availability}
        </span>
      </div>

      <div className="space-y-2 mb-3">
        <div>
          <p className="text-xs font-semibold text-slate-300 mb-1">Format:</p>
          <a
            href={spec.spec_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-blue-400 hover:text-blue-300 underline"
          >
            {spec.spec}
          </a>
        </div>

        <div>
          <p className="text-xs font-semibold text-slate-300 mb-1">What it is:</p>
          <p className="text-xs text-slate-200">{spec.what_it_is}</p>
        </div>

        <div>
          <p className="text-xs font-semibold text-slate-300 mb-1">What it is NOT:</p>
          <p className="text-xs text-slate-300 italic">{spec.what_it_is_not}</p>
        </div>

        {spec.caveats.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-slate-300 mb-1">Caveats:</p>
            <ul className="text-xs text-slate-300 space-y-0.5 list-disc list-inside">
              {spec.caveats.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </div>
        )}

        {spec.requires.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-slate-300 mb-1">Requires:</p>
            <p className="text-xs text-slate-400">{spec.requires.join(", ")}</p>
          </div>
        )}

        <div>
          <p className="text-xs font-semibold text-slate-300 mb-1">Used by:</p>
          <div className="flex flex-wrap gap-1">
            {spec.consumers.map((c) => (
              <span key={c} className="text-xs bg-slate-800 text-slate-200 px-2 py-1 rounded">
                {c}
              </span>
            ))}
          </div>
        </div>

        {spec.audience.length > 0 && (
          <div>
            <p className="text-xs font-semibold text-slate-300 mb-1">Audience:</p>
            <div className="flex flex-wrap gap-1">
              {spec.audience.map((a) => (
                <span key={a} className="text-xs bg-slate-700 text-slate-100 px-2 py-1 rounded">
                  {a}
                </span>
              ))}
            </div>
          </div>
        )}

        {spec.cli && (
          <div>
            <p className="text-xs font-semibold text-slate-300 mb-1">CLI:</p>
            <code className="text-xs bg-slate-950 text-slate-200 p-1 rounded block whitespace-pre-wrap">
              {spec.cli}
            </code>
          </div>
        )}
      </div>

      <div className="flex gap-2 flex-wrap">
        {spec.method === "GET" ? (
          <a
            href={spec.path}
            download={spec.filename}
            className="text-xs px-3 py-1 rounded bg-blue-700 hover:bg-blue-600 text-white transition"
          >
            Download
          </a>
        ) : (
          <button
            disabled
            title="POST formats must be called via API with parameters"
            className="text-xs px-3 py-1 rounded bg-slate-700 text-slate-400 cursor-not-allowed opacity-50"
          >
            POST (API only)
          </button>
        )}
        <a
          href={`/api/interop/formats`}
          className="text-xs px-3 py-1 rounded bg-slate-700 hover:bg-slate-600 text-slate-200 transition"
        >
          JSON
        </a>
      </div>
    </div>
  );
}

export default function Interop() {
  const t = useT();
  const [data, setData] = useState<InteropDiscovery | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.interop().then(setData).catch((e) => setError(e.message));
  }, []);

  return (
    <>
      <Panel title="Interoperability & Handoff Formats" right={data ? <span>{data.formats.length}</span> : null}>
        <Explain>
          Sixteen formats for integration with CADD tools, resistance management, and
          academic labs. Each format carries its own provenance. For more context, see{" "}
          <a href="https://github.com/cxrodgers/The-Periplaneta-Protocol" className="text-blue-400 hover:text-blue-300">
            CADD_HANDOFF.md
          </a>
          .
        </Explain>

        {data && (
          <div className="mt-4 space-y-2 text-xs text-slate-400">
            <div>
              <strong>Provenance:</strong> {data.provenance.literature} from literature,{" "}
              {data.provenance.assumption} assumptions, {data.provenance.total} total parameters
            </div>
            <div>
              <strong>Audiences:</strong> see format cards for audience tags
            </div>
          </div>
        )}

        <ErrorNote error={error} />
      </Panel>

      {data && (
        <div className="grid grid-cols-1 gap-3">
          {data.formats.map((fmt) => (
            <FormatCard key={fmt.id} spec={fmt} />
          ))}
        </div>
      )}
    </>
  );
}
