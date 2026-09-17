"use client";

import type { ReactNode } from "react";

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1 min-w-0">
      <span className="text-[10px] uppercase tracking-[0.15em] text-slate-500">{label}</span>
      {children}
      {hint ? <span className="text-[10px] text-slate-600 leading-snug">{hint}</span> : null}
    </label>
  );
}

export function Slider({
  label, value, onChange, min, max, step = 1, format = (v: number) => String(v), hint,
}: {
  label: string; value: number; onChange: (v: number) => void;
  min: number; max: number; step?: number; format?: (v: number) => string; hint?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <span className="flex items-center gap-2">
        <input
          type="range" min={min} max={max} step={step} value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="flex-1 accent-amber-400 min-w-0"
        />
        <output className="w-14 text-right tabular-nums text-slate-200 text-[11px]">{format(value)}</output>
      </span>
    </Field>
  );
}

export function Select<T extends string>({
  label, value, onChange, options, hint,
}: {
  label: string; value: T; onChange: (v: T) => void;
  options: { value: T; label: string }[]; hint?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        className="bg-black/40 border border-white/10 rounded px-2 py-1 text-[11px] text-slate-200 focus:border-amber-400 outline-none"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </Field>
  );
}

export function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-[11px] text-slate-300 cursor-pointer select-none">
      <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} className="accent-amber-400" />
      {label}
    </label>
  );
}

export function Button({
  children, onClick, busy, kind = "primary", disabled,
}: {
  children: ReactNode; onClick: () => void; busy?: boolean;
  kind?: "primary" | "ghost"; disabled?: boolean;
}) {
  const base = "px-3 py-1 rounded text-[11px] border transition-colors disabled:opacity-40 disabled:cursor-not-allowed";
  const style = kind === "primary"
    ? "border-amber-400/50 text-amber-200 hover:bg-amber-400/10"
    : "border-white/10 text-slate-400 hover:border-white/30 hover:text-slate-200";
  return (
    <button type="button" onClick={onClick} disabled={busy || disabled} className={`${base} ${style}`}>
      {busy ? "…" : children}
    </button>
  );
}
