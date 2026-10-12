// Who is here decides what they see: sign in, then an invite code once, then
// setup until a profile exists, then the app.

import { useCallback, useEffect, useState } from "react";
import { ApiError, get } from "./api";
import { loadConfig, onChange, signOut, token, type Config } from "./auth";
import type { Me, Overview } from "./types";
import { ago, applyTheme, Icon, useDark, useRoute, type IconName } from "./ui";
import { SignIn, Join, Reconsent } from "./pages/SignIn";
import { Today } from "./pages/Today";
import { Tracker } from "./pages/Tracker";
import { Posting } from "./pages/Posting";
import { Setup } from "./pages/Setup";
import { Settings } from "./pages/Settings";
import { Admin } from "./pages/Admin";
import { Legal } from "./pages/Legal";

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
  if (!me.member) return <Join me={me} onJoined={setMe} onSignOut={async () => { await signOut(); refresh(); }} />;
  if (me.consent_current === false) {
    const out = async () => { await signOut(); refresh(); };
    return <Reconsent me={me} onAgreed={setMe} onSignOut={out} settings={<Settings me={me} onSignOut={out} />} />;
  }
  return <Shell me={me} onMe={setMe} onSignOut={async () => { await signOut(); refresh(); }} />;
}

interface NavItem { hash: string; view: string; label: string; icon: IconName }

function Shell({ me, onMe, onSignOut }: { me: Me; onMe: (m: Me) => void; onSignOut: () => void }) {
  const route = useRoute();
  const [count, setCount] = useState<number | null>(null);
  const [health, setHealth] = useState<Overview | null>(null);
  const dark = useDark();

  useEffect(() => {
    const load = () => get<Overview>("/api/overview").then(setHealth).catch(() => setHealth(null));
    load();
    const t = window.setInterval(load, 60_000);
    return () => window.clearInterval(t);
  }, []);

  const ready = me.profile_ready;
  const nav: NavItem[] = [
    ...(ready ? [{ hash: "#/", view: "today", label: "Today", icon: "sun" as IconName },
                 { hash: "#/applied", view: "applied", label: "Applied", icon: "send" as IconName }] : []),
    { hash: "#/setup", view: "setup", label: "Setup", icon: "upload" },
    { hash: "#/settings", view: "settings", label: "Settings", icon: "gear" },
    ...(me.admin ? [{ hash: "#/admin", view: "admin", label: "Admin", icon: "shield" as IconName }] : []),
  ];

  // Before a profile exists, setup (and settings) is all there is.
  let key = route.path[0] || "today";
  if (!ready && !["setup", "settings", "admin", ...LEGAL].includes(key)) key = "setup";
  const view = key === "p" ? "applied" : key;

  const page = (() => {
    switch (key) {
      case "applied": return <Tracker query={route.query} />;
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
        <div className="brand"><span className="mark"><Icon name="brief" /></span><span>Job search</span></div>
        {nav.map(n => (
          <a key={n.view} href={n.hash} className={view === n.view ? "on" : ""} aria-current={view === n.view ? "page" : undefined}>
            <Icon name={n.icon} /><span>{n.label}</span>
            {n.view === "today" && count ? <span className="count">{count}</span> : null}
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
          </span>
          <div className="top-actions">
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
