/** One line-icon set (24-grid, 1.7 stroke, round caps) so every icon has the same weight — the old screens mixed
 * unicode symbols of different fonts and sizes. Decorative: always aria-hidden; the text next to it carries meaning. */
import type { ReactNode, SVGProps } from "react";

type P = SVGProps<SVGSVGElement>;
const S = ({ children, ...p }: P & { children: ReactNode }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" {...p}>{children}</svg>
);

export const IHome = (p: P) => <S {...p}><path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z" /></S>;
export const IStocks = (p: P) => <S {...p}><circle cx="10.5" cy="10.5" r="6" /><path d="m15 15 5 5" /><path d="M7.8 11.6 9.7 9.4l1.7 1.8 2-2.6" /></S>;
export const IPortfolio = (p: P) => <S {...p}><path d="M12 3.5a8.5 8.5 0 1 0 8.5 8.5H12z" /><path d="M15 3.8A8.5 8.5 0 0 1 20.2 9H15z" /></S>;
export const IMarket = (p: P) => <S {...p}><path d="M3.5 12h3l2.2-5.5 3.6 11 2.6-7 1.6 3.5h4" /></S>;
export const IPerf = (p: P) => <S {...p}><path d="M4 19.5h16" /><path d="M6.5 16v-4M11 16V8M15.5 16v-6M20 16V5.5" /></S>;
export const ISettings = (p: P) => <S {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 14.6a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z" /></S>;
export const IHelp = (p: P) => <S {...p}><circle cx="12" cy="12" r="8.5" /><path d="M9.6 9.5a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.6" /><path d="M12 17h.01" /></S>;
export const IRefresh = (p: P) => <S {...p}><path d="M20 11a8 8 0 0 0-14.6-4.4L4 8.5" /><path d="M4 4v4.5h4.5" /><path d="M4 13a8 8 0 0 0 14.6 4.4l1.4-1.9" /><path d="M20 20v-4.5h-4.5" /></S>;
export const IStar = (p: P) => <S {...p}><path d="m12 4 2.4 5 5.3.6-4 3.7 1.1 5.3L12 16l-4.8 2.6 1.1-5.3-4-3.7 5.3-.6z" /></S>;
export const IPlus = (p: P) => <S {...p}><path d="M12 5v14M5 12h14" /></S>;
export const IArrow = (p: P) => <S {...p}><path d="M5 12h14M13 6l6 6-6 6" /></S>;
export const ICheck = (p: P) => <S {...p}><path d="m5 12.5 4.5 4.5L19 7.5" /></S>;
export const IAlert = (p: P) => <S {...p}><path d="M12 4 2.8 19.5h18.4z" /><path d="M12 10v4.5M12 17.2h.01" /></S>;
export const IInfo = (p: P) => <S {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5.5M12 7.8h.01" /></S>;
export const IClock = (p: P) => <S {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></S>;
export const IScan = (p: P) => <S {...p}><path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3" /><path d="M7 12h10" /></S>;
export const IDownload = (p: P) => <S {...p}><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5" /><path d="M5 19.5h14" /></S>;
export const ISpark = (p: P) => <S {...p}><path d="M12 3.5 13.8 10l6.7 2-6.7 2L12 20.5 10.2 14l-6.7-2 6.7-2z" /></S>;
export const ITrash = (p: P) => <S {...p}><path d="M4.5 7h15M10 7V4.5h4V7M6.5 7l1 12.5h9l1-12.5" /></S>;
export const IChevron = (p: P) => <S {...p}><path d="m9 6 6 6-6 6" /></S>;
export const IEvent = (p: P) => <S {...p}><rect x="4" y="5" width="16" height="15" rx="2" /><path d="M8 3.5v3M16 3.5v3M4 10h16" /></S>;
export const IShield = (p: P) => <S {...p}><path d="M12 3.5 5 6.5v5.2c0 4 2.9 7.3 7 8.8 4.1-1.5 7-4.8 7-8.8V6.5z" /></S>;
export const IFlag = (p: P) => <S {...p}><path d="M5 20.5V4.5M5 5h11l-2 4 2 4H5" /></S>;
export const ILayers = (p: P) => <S {...p}><path d="m12 4 8.5 4.5L12 13 3.5 8.5z" /><path d="m3.5 12.5 8.5 4.5 8.5-4.5" /></S>;

/** Brand mark: "Lenny", a round lens buddy — the lens ring with a small face and a price-trace smile. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="ml-ring" x1="4" y1="4" x2="28" y2="28" gradientUnits="userSpaceOnUse">
          <stop stopColor="#d9d0ff" /><stop offset="0.55" stopColor="#a494f8" /><stop offset="1" stopColor="#ff9ecf" />
        </linearGradient>
        <radialGradient id="ml-glow" cx="16" cy="16" r="14" gradientUnits="userSpaceOnUse">
          <stop stopColor="#a494f8" stopOpacity="0.4" /><stop offset="1" stopColor="#a494f8" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="16" cy="16" r="14" fill="url(#ml-glow)" />
      <path d="M23.4 23.4 27 27" stroke="url(#ml-ring)" strokeWidth="3" strokeLinecap="round" />
      <circle cx="15" cy="15" r="10.5" fill="#141a2e" stroke="url(#ml-ring)" strokeWidth="2.2" />
      <circle cx="11.6" cy="13.4" r="1.45" fill="#f3f2fa" />
      <circle cx="18.4" cy="13.4" r="1.45" fill="#f3f2fa" />
      <circle cx="9.6" cy="17" r="1.3" fill="#ff9ecf" opacity="0.55" />
      <circle cx="20.4" cy="17" r="1.3" fill="#ff9ecf" opacity="0.55" />
      <path d="M11.8 17.6c1 1.3 2.1 1.9 3.2 1.9s2.2-.6 3.2-1.9" fill="none" stroke="#5fe0bd" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}

/** The same buddy for empty and quiet states: calm (nothing to show), sleepy (closed / waiting), happy (all clear). */
export function Buddy({ mood = "calm", className }: { mood?: "calm" | "sleepy" | "happy"; className?: string }) {
  const eyes = mood === "sleepy"
    ? <><path d="M17 27.5q2.4 1.6 4.8 0" /><path d="M30.2 27.5q2.4 1.6 4.8 0" /></>
    : <><circle cx="19.4" cy="27" r="2.5" fill="#f3f2fa" stroke="none" /><circle cx="32.6" cy="27" r="2.5" fill="#f3f2fa" stroke="none" /></>;
  const mouth = mood === "happy" ? "M21.5 33.5q4.5 4.6 9 0" : mood === "sleepy" ? "M24.4 35h3.2" : "M22.6 34.2q3.4 2.4 6.8 0";
  return (
    <svg className={className ?? "buddy"} viewBox="0 0 52 52" fill="none" stroke="#f3f2fa" strokeWidth="2" strokeLinecap="round" aria-hidden="true" focusable="false">
      <path d="M38.5 38.5 46 46" stroke="#a494f8" strokeWidth="5" />
      <circle cx="26" cy="27" r="17" fill="#161d33" stroke="#a494f8" strokeWidth="3" />
      <path d="M17.5 17.5q3-3.4 7.5-4" stroke="#d9d0ff" strokeWidth="2.2" opacity="0.6" />
      {eyes}
      <circle cx="15.6" cy="32.2" r="2.4" fill="#ff9ecf" stroke="none" opacity="0.5" />
      <circle cx="36.4" cy="32.2" r="2.4" fill="#ff9ecf" stroke="none" opacity="0.5" />
      <path d={mouth} stroke="#5fe0bd" />
      {mood === "sleepy" && <path d="M39 8.5h5l-5 6h5" stroke="#a494f8" strokeWidth="1.8" strokeLinejoin="round" />}
    </svg>
  );
}
export const IBell = (p: P) => <S {...p}><path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z" /><path d="M10 20.5a2 2 0 0 0 4 0" /></S>;
