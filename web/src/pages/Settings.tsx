import { useEffect, useState } from "react";
import { download, post } from "../api";
import { connectedApps, disconnectApp, loadConfig, type ConnectedApp } from "../auth";
import type { Me } from "../types";
import { applyTheme, day, getTheme, useToast, type Theme } from "../ui";

const DELETE_WORDS = "delete my account";

export function Settings({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const [theme, setTheme] = useState<Theme>(getTheme());
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState("");
  const toast = useToast();

  const pick = (t: Theme) => { applyTheme(t); setTheme(t); };
  const remove = async () => {
    setErr("");
    if (confirm.trim().toLowerCase() !== DELETE_WORDS) { setErr(`Type “${DELETE_WORDS}” to confirm`); return; }
    try { await post("/api/account/delete", { confirm }); toast("Your account and everything kept for it are deleted"); onSignOut(); }
    catch (e) { setErr((e as Error).message); }
  };

  return (
    <>
      <h1>Settings</h1>
      <p className="sub">Signed in as {me.email}.</p>

      <div className="card stack">
        <h3>Appearance</h3>
        <div className="tabs plain" role="radiogroup" aria-label="Theme" style={{ margin: 0 }}>
          {(["system", "light", "dark"] as Theme[]).map(t => (
            <button key={t} role="radio" aria-checked={theme === t} className={theme === t ? "on" : ""} onClick={() => pick(t)}>
              {t === "system" ? "Same as my device" : t === "light" ? "Light" : "Dark"}
            </button>
          ))}
        </div>
      </div>

      <Claude />

      <div className="card stack" style={{ marginTop: 12 }}>
        <h3>Your data</h3>
        <p className="small">Everything kept for you: your confirmed facts, preferences, the uploaded resume, ranked jobs and built resumes.</p>
        <div><button onClick={() => download("/api/account/export", "jobsearch-export.zip").catch(e => toast((e as Error).message))}>
          Download everything (zip)
        </button></div>
      </div>

      <div className="card stack" style={{ marginTop: 12 }}>
        <h3>Privacy</h3>
        <p className="small">
          <a href="#/privacy">Privacy notice</a> · <a href="#/terms">Terms of use</a>
          {me.consent && <span className="muted"> · you agreed to version {me.consent.version} on {day(me.consent.at)}</span>}
        </p>
      </div>

      <div className="card stack" style={{ marginTop: 12 }}>
        <h3>Delete your account</h3>
        <p className="small">Deletes your profile, your resume and everything worked out for you. It can't be undone - download a copy first if you want one.</p>
        <label htmlFor="confirm">Type “{DELETE_WORDS}” to confirm</label>
        <input id="confirm" value={confirm} onChange={e => { setConfirm(e.target.value); setErr(""); }} autoComplete="off" />
        {err && <p className="fail small">{err}</p>}
        <div><button className="danger" onClick={remove}>Delete my account</button></div>
      </div>

      <div style={{ marginTop: 20 }}><button className="quiet" onClick={onSignOut}>Sign out</button></div>
    </>
  );
}

/** SwitchKaro inside Claude: the address to add, and taking it back. */
function Claude() {
  const [hosted, setHosted] = useState(false);
  const [apps, setApps] = useState<ConnectedApp[]>([]);
  const toast = useToast();
  const address = `${window.location.origin}/mcp`;
  const load = () => connectedApps().then(setApps).catch(() => setApps([]));
  useEffect(() => { loadConfig().then(c => { setHosted(c.auth === "supabase"); if (c.auth === "supabase") load(); }); }, []);
  if (!hosted) return null;
  const drop = async (a: ConnectedApp) => {
    try { await disconnectApp(a.id); toast(`${a.name} disconnected`); load(); }
    catch (e) { toast((e as Error).message); }
  };
  return (
    <div className="card stack" style={{ marginTop: 12 }}>
      <h3>Use SwitchKaro in Claude</h3>
      <p className="small">Ask Claude about your jobs in any chat. In Claude: <b>Settings &gt; Connectors &gt; Add custom
        connector</b>, paste this address, then <b>Connect</b> and approve it here. Needs a paid Claude plan.</p>
      <div className="row">
        <code className="addr">{address}</code>
        <button onClick={() => navigator.clipboard.writeText(address).then(() => toast("Copied"), () => toast("Select it to copy"))}>Copy</button>
      </div>
      {apps.length > 0 && (
        <ul className="asked">
          {apps.map(a => (
            <li key={a.id}><b>{a.name}</b> <span className="muted small">connected {day(a.since)}</span>
              <button className="quiet" onClick={() => drop(a)}>Disconnect</button></li>
          ))}
        </ul>
      )}
    </div>
  );
}
