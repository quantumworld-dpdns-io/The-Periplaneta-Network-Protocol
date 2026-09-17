"use client";

import { useState } from "react";
import { download } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Button } from "./Controls";

/** Asks the server for a file and lets the browser save it. */
export function ExportButton({ path, body, label }: { path: string; body?: unknown; label: string }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <span className="inline-flex items-center gap-2">
      <Button
        kind="ghost"
        busy={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await download(path, body);
          } catch (e) {
            setError(e instanceof Error ? e.message : "failed");
          } finally {
            setBusy(false);
          }
        }}
      >
        ↓ {t("export")} {label}
      </Button>
      {error ? <span className="text-[10px] text-rose-300">{error}</span> : null}
    </span>
  );
}
