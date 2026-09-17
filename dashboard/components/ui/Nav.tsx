"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { CockroachIcon } from "@/components/CockroachIcon";
import { useLang, useT, type Key } from "@/lib/i18n";

const PAGES: { href: string; key: Key }[] = [
  { href: "/", key: "navOverview" },
  { href: "/colony", key: "navColony" },
  { href: "/evolution", key: "navEvolution" },
  { href: "/strategy", key: "navStrategy" },
  { href: "/chemistry", key: "navChemistry" },
  { href: "/neural", key: "navNeural" },
  { href: "/parameters", key: "navParameters" },
  { href: "/interop", key: "navInterop" },
];

export function Nav() {
  const t = useT();
  const { lang, setLang } = useLang();
  const path = usePathname();
  return (
    <header className="panel sticky top-0 z-10 backdrop-blur">
      <div className="flex items-center gap-3 px-3 py-2 flex-wrap">
        <Link href="/" className="flex items-center gap-2 shrink-0">
          <CockroachIcon size={26} state="baseline" glow={0.4} title={t("appName")} />
          <span className="text-[13px] font-semibold tracking-[0.18em] uppercase text-slate-100">
            {t("appName")}
          </span>
        </Link>
        <nav className="flex items-center gap-1 flex-wrap text-[11px]">
          {PAGES.map((p) => {
            const active = p.href === "/" ? path === "/" : path.startsWith(p.href);
            return (
              <Link
                key={p.href}
                href={p.href}
                aria-current={active ? "page" : undefined}
                className={`px-2 py-1 rounded transition-colors ${
                  active
                    ? "bg-amber-400/15 text-amber-200"
                    : "text-slate-400 hover:text-slate-200 hover:bg-white/5"
                }`}
              >
                {t(p.key)}
              </Link>
            );
          })}
        </nav>
        <button
          type="button"
          onClick={() => setLang(lang === "en" ? "zh" : "en")}
          className="ml-auto px-2 py-1 rounded border border-white/10 text-[11px] text-slate-300 hover:border-amber-400/50 hover:text-amber-200"
        >
          {t("language")}
        </button>
      </div>
    </header>
  );
}
