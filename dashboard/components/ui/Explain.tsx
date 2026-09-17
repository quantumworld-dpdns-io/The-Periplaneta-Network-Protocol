"use client";

import { useState, type ReactNode } from "react";
import { useT } from "@/lib/i18n";

/** Plain-language box. Open by default, because the explanation is the point. */
export function Explain({ children, title }: { children: ReactNode; title?: string }) {
  const t = useT();
  const [open, setOpen] = useState(true);
  return (
    <div className="rounded border border-sky-400/20 bg-sky-400/5 text-[11px] leading-relaxed">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-left text-sky-300 uppercase tracking-[0.15em] text-[10px]"
      >
        <span className={`transition-transform ${open ? "rotate-90" : ""}`}>›</span>
        {title ?? t("whatThisMeans")}
      </button>
      {open ? <div className="px-3 pb-2.5 text-slate-300">{children}</div> : null}
    </div>
  );
}

export function Caveats({ items }: { items?: string[] }) {
  const t = useT();
  if (!items?.length) return null;
  return (
    <details className="mt-3 text-[10px]">
      <summary className="cursor-pointer text-amber-300/80 uppercase tracking-[0.15em]">
        {t("beScepticalOf")} ({items.length})
      </summary>
      <ul className="list-disc list-inside mt-1 space-y-0.5 text-slate-500 leading-snug">
        {items.map((c) => <li key={c}>{c}</li>)}
      </ul>
    </details>
  );
}

export function Reproduce({ command, elapsed }: { command?: string; elapsed?: number }) {
  const t = useT();
  if (!command) return null;
  return (
    <div className="mt-2 text-[10px]">
      <div className="text-slate-600 uppercase tracking-[0.15em] mb-0.5">
        {t("howToReproduce")}
        {elapsed !== undefined ? ` · ${t("tookSeconds")} ${elapsed.toFixed(1)}${t("seconds")}` : ""}
      </div>
      <code className="block bg-black/40 border border-white/5 rounded px-2 py-1 text-slate-400 overflow-x-auto whitespace-pre">
        {command}
      </code>
    </div>
  );
}
