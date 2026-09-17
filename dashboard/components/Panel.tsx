import type { ReactNode } from "react";

export function Panel({
  title,
  right,
  children,
  className = "",
  bodyClassName = "",
}: {
  title: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`panel flex flex-col min-h-0 ${className}`}>
      <header className="flex items-center justify-between gap-2 px-3 py-1.5 border-b border-white/5">
        <h2 className="text-[11px] font-semibold tracking-[0.18em] uppercase text-slate-400">{title}</h2>
        {right ? <div className="text-[11px] text-slate-500 font-mono flex items-center gap-2">{right}</div> : null}
      </header>
      <div className={`p-3 min-h-0 flex-1 ${bodyClassName}`}>{children}</div>
    </section>
  );
}

export function Dot({ ok, warn }: { ok: boolean; warn?: boolean }) {
  const c = ok ? "bg-emerald-400 shadow-[0_0_6px_#34d399]" : warn ? "bg-amber-400 shadow-[0_0_6px_#f59e0b]" : "bg-slate-600";
  return <span className={`inline-block w-1.5 h-1.5 rounded-full ${c}`} />;
}

export function fmtTime(tMs: number): string {
  const d = new Date(tMs);
  return d.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
