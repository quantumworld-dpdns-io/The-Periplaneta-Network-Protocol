import type { CSSProperties } from "react";
import { STATE_COLORS, type StateName } from "@/lib/colors";

export type CockroachState = StateName;

export interface CockroachIconProps {
  size?: number;
  state?: CockroachState;
  /** extra glow intensity 0..1 (e.g. spike flash) */
  glow?: number;
  className?: string;
  title?: string;
  style?: CSSProperties;
}

/**
 * Stylised cockroach silhouette. Same geometry as app/icon.svg (favicon):
 * a single colour (currentColor), filled body/head + stroked legs/antennae.
 * viewBox 0 0 64 64; legible at 16 px because the body is one solid mass.
 */
export const COCKROACH_BODY =
  "M32 18C40 18 43.5 27 43.5 36C43.5 47 39.5 55.5 32 55.5C24.5 55.5 20.5 47 20.5 36C20.5 27 24 18 32 18Z" +
  "M32 10.5C36 10.5 37.5 13 37.5 15.5C37.5 18.5 35 19.8 32 19.8C29 19.8 26.5 18.5 26.5 15.5C26.5 13 28 10.5 32 10.5Z";
export const COCKROACH_LIMBS =
  "M29.5 11.5C25 6 19 3.5 10 2.5M34.5 11.5C39 6 45 3.5 54 2.5" +
  "M24 26L14 20L8 24.5M40 26L50 20L56 24.5" +
  "M21.5 36L10 34L4.5 40.5M42.5 36L54 34L59.5 40.5" +
  "M24 46L14 52.5L10 60.5M40 46L50 52.5L54 60.5";

export function CockroachIcon({ size = 24, state = "baseline", glow = 0, className, title, style }: CockroachIconProps) {
  const color = STATE_COLORS[state];
  const blur = state === "offline" ? 0 : 3 + 6 * Math.min(1, Math.max(0, glow));
  const filter = blur > 0 ? `drop-shadow(0 0 ${blur}px ${color})` : undefined;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role="img"
      aria-label={title ?? `cockroach ${state}`}
      className={className}
      style={{ color, filter, transition: "color 300ms, filter 150ms", ...style }}
    >
      {title ? <title>{title}</title> : null}
      <path d={COCKROACH_BODY} fill="currentColor" />
      <path d={COCKROACH_LIMBS} fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" />
      {/* wing seam, subtle */}
      <path d="M32 22V54" stroke="#0a0e15" strokeOpacity={0.55} strokeWidth={1.5} strokeLinecap="round" />
    </svg>
  );
}

export default CockroachIcon;
