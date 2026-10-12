import { useState } from "react";
import { download, post } from "../api";
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
