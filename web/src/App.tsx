// Who is here decides what they see: sign in, then an invite code once, then
// setup until a profile exists, then the app.

import { useCallback, useEffect, useState } from "react";
import { ApiError, get } from "./api";
import { appName, CONSENT_PATH, loadConfig, onChange, signOut, token, type Config } from "./auth";
import type { Me, Overview } from "./types";
import { ago, applyTheme, day, Icon, useDark, useRoute, type IconName } from "./ui";
import { SignIn, Join, Paused, Reconsent } from "./pages/SignIn";
import { Today } from "./pages/Today";
import { Jobs } from "./pages/Jobs";
import { Tracker } from "./pages/Tracker";
import { Resumes } from "./pages/Resumes";
import { Profile } from "./pages/Profile";
import { Market } from "./pages/Market";
import { Posting } from "./pages/Posting";
import { Setup } from "./pages/Setup";
import { Settings } from "./pages/Settings";
import { Admin } from "./pages/Admin";
import { Legal } from "./pages/Legal";
import { Consent } from "./pages/Consent";

type Phase = "loading" | "signed-out" | "ready" | "error";

const LEGAL = ["privacy", "terms"];

export function App() {
  const route = useRoute();
  const [config, setConfig] = useState<Config | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const c = await loadConfig();
      setConfig(c);
      if (!(await token())) { setMe(null); setPhase("signed-out"); return; }
      setMe(await get<Me>("/api/me"));
      setPhase("ready");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) { await signOut(); setMe(null); setPhase("signed-out"); return; }
      setError((e as Error).message);
      setPhase("error");
    }
  }, []);

  useEffect(() => {
    let off = () => {};
    refresh().then(() => { off = onChange(refresh); });
    return () => off();
  }, [refresh]);

  // The privacy notice and terms are readable by anyone, signed in or not.
  if (LEGAL.includes(route.path[0]) && !(phase === "ready" && me?.member)) {
    return <Legal doc={route.path[0]} standalone />;
  }
  if (phase === "loading") return <div className="gate"><div className="card muted">Loading…</div></div>;
  if (phase === "error") {
    return (
      <div className="gate"><div className="card stack">
        <p className="fail">{error}</p>
        <button className="primary" onClick={refresh}>Try again</button>
      </div></div>
    );
  }
  if (phase === "signed-out" || !me || !config) return <SignIn config={config} onDone={refresh} />;
  // Approving the Claude connector: it says itself what's missing, if anything.
  if (window.location.pathname === CONSENT_PATH) {
    return <Consent me={me} onSignOut={async () => { await signOut(); refresh(); }} />;
  }
  if (!me.member) return <Join me={me} onJoined={setMe} onSignOut={async () => { await signOut(); refresh(); }} />;
  if (me.suspended) {
    const out = async () => { await signOut(); refresh(); };
    return <Paused me={me} onSignOut={out} settings={<Settings me={me} onSignOut={out} />} />;
  }
  if (me.consent_current === false) {
    const out = async () => { await signOut(); refresh(); };
    return <Reconsent me={me} onAgreed={setMe} onSignOut={out} settings={<Settings me={me} onSignOut={out} />} />;
  }
  return <Shell me={me} onMe={setMe} onSignOut={async () => { await signOut(); refresh(); }} />;
}

interface NavItem { hash: string; view: string; label: string; icon: IconName }

/** When a future moment comes, in words: "in 20 min", "in 3 h", "on 17 Oct". */
function inTime(iso: string): string {
  const s = (new Date(iso).getTime() - Date.now()) / 1000;
  if (s < 90) return "soon";
  if (s < 5400) return `in ${Math.round(s / 60)} min`;
  if (s < 43200) return `in ${Math.round(s / 3600)} h`;
  return `on ${day(iso)}`;
}

function Shell({ me, onMe, onSignOut }: { me: Me; onMe: (m: Me) => void; onSignOut: () => void }) {
  const route = useRoute();
  const [count, setCount] = useState<number | null>(null);
  const [health, setHealth] = useState<Overview | null>(null);
  const [more, setMore] = useState(false);
  const dark = useDark();
  useEffect(() => setMore(false), [route]);

  // Every minute; every 20 seconds while the AI is reading, so its count moves.
  const reading = health?.ai_queue?.state === "running";
  useEffect(() => {
    const load = () => get<Overview>("/api/overview").then(setHealth).catch(() => setHealth(null));
    load();
    const t = window.setInterval(load, reading ? 20_000 : 60_000);
    return () => window.clearInterval(t);
  }, [reading]);
  const q = health?.ai_queue;
  const queueText = !q ? "" : q.state === "running"
    ? `AI reading your jobs ${q.done ?? 0}/${q.total ?? 0}`
    : `AI paused${q.until ? `, back ${inTime(q.until)}` : ""}`;
  const queueTitle = !q ? "" : q.state === "running"
    ? `The AI is reading your new jobs in the background, four at a time: ${q.done ?? 0} of ${q.total ?? 0} done. You can keep using the app.`
    : `The AI stopped reading for now: ${q.why || "a limit was reached"}. It carries on by itself.`;

  const ready = me.profile_ready;
  const setupEtc: NavItem[] = [
    { hash: "#/setup", view: "setup", label: "Setup", icon: "upload" },
    { hash: "#/settings", view: "settings", label: "Settings", icon: "gear" },
    ...(me.admin ? [{ hash: "#/admin", view: "admin", label: "Admin", icon: "shield" as IconName }] : []),
  ];
  // Once there's a profile, the everyday screens come first; on a phone they
  // are the tab bar and the rest sits behind the top bar's "More".
  const nav: NavItem[] = ready ? [
    { hash: "#/", view: "today", label: "Today", icon: "sun" },
    { hash: "#/jobs", view: "jobs", label: "Jobs", icon: "search" },
    { hash: "#/applied", view: "applied", label: "Applied", icon: "send" },
    { hash: "#/resumes", view: "resumes", label: "Resumes", icon: "file" },
    { hash: "#/profile", view: "profile", label: "Profile", icon: "user" },
    { hash: "#/market", view: "market", label: "Market", icon: "chart" },
  ] : setupEtc;
  const minor: NavItem[] = ready ? setupEtc : [];

  // Before a profile exists, setup (and settings) is all there is.
  let key = route.path[0] || "today";
  if (!ready && !["setup", "settings", "admin", ...LEGAL].includes(key)) key = "setup";
  const view = key === "p" ? "jobs" : key;

  const page = (() => {
    switch (key) {
      case "jobs": return <Jobs key={route.query.toString()} query={route.query} />;
      case "applied": return <Tracker query={route.query} />;
      case "resumes": return <Resumes />;
      case "profile": return <Profile />;
      case "market": return <Market />;
      case "p": return <Posting id={decodeURIComponent(route.path[1] || "")} />;
      case "setup": return <Setup onSaved={() => get<Me>("/api/me").then(onMe)} />;
      case "settings": return <Settings me={me} onSignOut={onSignOut} />;
      case "admin": return me.admin ? <Admin /> : null;
      case "privacy": case "terms": return <Legal doc={key} />;
      default: return <Today onCount={setCount} />;
    }
  })();

  const flip = () => applyTheme(dark ? "light" : "dark");

  return (
    <div className="shell">
      <nav className="side" aria-label="Sections">
        <div className="brand"><span className="mark"><Icon name="brief" /></span><span>{appName()}</span></div>
        {nav.map(n => (
          <a key={n.view} href={n.hash} className={view === n.view ? "on" : ""} aria-current={view === n.view ? "page" : undefined}>
            <Icon name={n.icon} /><span>{n.label}</span>
            {n.view === "today" && count ? <span className="count">{count}</span> : null}
          </a>
        ))}
        {minor.length > 0 && <div className="sep" />}
        {minor.map(n => (
          <a key={n.view} href={n.hash} className={`minor${view === n.view ? " on" : ""}`} aria-current={view === n.view ? "page" : undefined}>
            <Icon name={n.icon} /><span>{n.label}</span>
          </a>
        ))}
        <div className="who">{me.email}</div>
      </nav>
      <div className="main">
        <header className="top">
          <span className="top-brand" aria-hidden="true"><span className="mark"><Icon name="brief" /></span></span>
          <span className="health">
            <span className={`dot ${health?.busy ? "busy" : health?.last_cycle ? "ok" : ""}`} />
            <span>{health?.busy ? "Updating jobs…" : health?.last_cycle ? `Jobs updated ${ago(health.last_cycle.at)}` : "Jobs not updated yet"}</span>
            {q && <span className="ai-queue" title={queueTitle} aria-live="polite"> · {queueText}</span>}
          </span>
          <div className="top-actions">
            {minor.length > 0 && (
              <div className="top-more">
                <button className="icon" onClick={() => setMore(!more)} aria-expanded={more} aria-label="More" title="More">
                  <Icon name="more" className="dots" />
                </button>
                {more && (
                  <div className="more-menu">
                    {minor.map(n => (
                      <a key={n.view} href={n.hash} className={view === n.view ? "on" : ""}><Icon name={n.icon} />{n.label}</a>
                    ))}
                  </div>
                )}
              </div>
            )}
            <button className="icon" onClick={flip} aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
                    title={dark ? "Switch to light mode" : "Switch to dark mode"}>
              <Icon name={dark ? "sun" : "moon"} />
            </button>
          </div>
        </header>
        <main className="view">{page}</main>
      </div>
    </div>
  );
}
