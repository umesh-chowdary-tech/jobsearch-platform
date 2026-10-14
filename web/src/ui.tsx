// Small shared pieces: icons, the company badge, fit labels, toasts, the
// router and the theme.

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { Fit, Row } from "./types";

const ICONS = {
  sun: "M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  moon: "M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z",
  star: "M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z",
  check: "M5 12.5l4.5 4.5L19 7.5",
  upload: "M12 15V4M7 9l5-5 5 5M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3",
  list: "M9 6h11M9 12h11M9 18h11M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2",
  target: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18zM12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8zM12 11.5v1",
  activity: "M3 12h4l3-7 4 14 3-7h4",
  clock: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18zM12 7v5l3 2",
  brief: "M5 7h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2zM9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M3 13h18",
  send: "M21 3L10 14M21 3l-6.5 18-3.5-7-7-3.5z",
  gear: "M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
  thumbUp: "M7 11v9H4v-9zM7 11l4-8a2 2 0 0 1 2 2v4h5.5a2 2 0 0 1 2 2.3l-1.2 7A2 2 0 0 1 17.3 20H7",
  thumbDown: "M17 13V4h3v9zM17 13l-4 8a2 2 0 0 1-2-2v-4H5.5a2 2 0 0 1-2-2.3l1.2-7A2 2 0 0 1 6.7 4H17",
  file: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h4",
  external: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  search: "M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14zM16 16l4.5 4.5",
  user: "M12 4a4 4 0 1 0 0 8a4 4 0 1 0 0-8zM4.5 20.5a7.5 7.5 0 0 1 15 0",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  edit: "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
  chart: "M4 20h16M7 16v-5M12 16V6M17 16v-8",
};
export type IconName = keyof typeof ICONS;

export function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg className={className ? `i ${className}` : "i"} viewBox="0 0 24 24" aria-hidden="true">
      <path d={ICONS[name]} />
    </svg>
  );
}

export function initials(name: string): string {
  const words = String(name || "?").replace(/[^\p{L}\p{N} ]/gu, " ").split(/\s+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] || "?").slice(0, 2)).toUpperCase();
}

export function tint(name: string): number {
  let h = 0;
  for (const c of String(name || "")) h = (h * 31 + (c.codePointAt(0) || 0)) >>> 0;
  return h % 6;
}

export function Avatar({ name, big }: { name: string; big?: boolean }) {
  return <span className={`avatar a${tint(name)}${big ? " big" : ""}`} aria-hidden="true">{initials(name)}</span>;
}

const FIT_WORD: Record<string, string> = { strong: "Strong fit", possible: "Possible fit", weak: "Weak fit" };
export function FitBadge({ fit }: { fit: Fit }) {
  return <span className={`badge ${fit || ""}`}>{(fit && FIT_WORD[fit]) || fit || "Not read yet"}</span>;
}

/** What the posting asks for that the profile backs (teal) and doesn't (coral). */
export function JobTags({ row }: { row: Pick<Row, "coverage" | "skills" | "gaps"> }) {
  const [have, of] = String(row.coverage || "").split("/").map(Number);
  if (!row.skills?.length && !row.gaps?.length && !(of > 0)) return null;
  return (
    <div className="tags">
      {row.skills.map(s => <span key={s} className="pill c">{s}</span>)}
      {row.gaps.map(g => <span key={g} className="pill g" title="asked for, not backed">{g}</span>)}
      {of > 0 && (
        <span className="meter" title={`${have} of the ${of} skills it names are backed by your profile`}>
          <span className="track"><i style={{ width: `${Math.round((100 * have) / of)}%` }} /></span>
          {have}/{of} backed
        </span>
      )}
    </div>
  );
}

export function ago(iso?: string | null): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

export const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });

/** First seen on its board in the last three days. */
export const fresh = (iso?: string | null) => !!iso && Date.now() - new Date(iso).getTime() < 3 * 86_400_000;

// ── toasts ─────────────────────────────────────────────────────────────────
const ToastContext = createContext<(msg: string) => void>(() => {});
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState("");
  const timer = useRef<number>(0);
  const show = useCallback((m: string) => {
    setMsg(m);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMsg(""), 3200);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      {msg && <div className="toast" role="status">{msg}</div>}
    </ToastContext.Provider>
  );
}

// ── the router: #/path?query ───────────────────────────────────────────────
export interface Route { path: string[]; query: URLSearchParams }

function read(): Route {
  const [p, q] = window.location.hash.replace(/^#/, "").split("?");
  return { path: (p || "/").split("/").filter(Boolean), query: new URLSearchParams(q || "") };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => { setRoute(read()); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export const go = (hash: string) => { window.location.hash = hash; };

// ── the theme ──────────────────────────────────────────────────────────────
const THEME_KEY = "jobsearch-theme";
const THEME_EVENT = "jobsearch-theme";
export type Theme = "system" | "light" | "dark";

export function getTheme(): Theme {
  try {
    const t = window.localStorage.getItem(THEME_KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(t: Theme): void {
  if (t === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  try {
    if (t === "system") window.localStorage.removeItem(THEME_KEY);
    else window.localStorage.setItem(THEME_KEY, t);
  } catch { /* kept for this visit only */ }
  window.dispatchEvent(new Event(THEME_EVENT));
}

/** Whether the page is dark right now, following the switch and the device. */
export function useDark(): boolean {
  const [dark, setDark] = useState(isDark);
  useEffect(() => {
    const on = () => setDark(isDark());
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    window.addEventListener(THEME_EVENT, on);
    mq.addEventListener("change", on);
    return () => { window.removeEventListener(THEME_EVENT, on); mq.removeEventListener("change", on); };
  }, []);
  return dark;
}

export const isDark = () => {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
};
