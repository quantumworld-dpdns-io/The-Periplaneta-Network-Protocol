"use client";

import { useT } from "@/lib/i18n";

export function ErrorNote({ error }: { error: string | null }) {
  const t = useT();
  if (!error) return null;
  const unreachable = error === "unreachable";
  return (
    <p className="text-[11px] text-rose-300 border border-rose-400/30 bg-rose-400/5 rounded px-3 py-1.5">
      {unreachable ? t("apiDown") : error}
    </p>
  );
}

export function Busy() {
  const t = useT();
  return <p className="text-[11px] text-slate-500">{t("loading")}</p>;
}
