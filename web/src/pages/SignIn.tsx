import React, { useState, type FormEvent } from "react";
import { post } from "../api";
import { appName, devSignIn, emailLink, google, type Config } from "../auth";
import type { Me } from "../types";
import { Icon } from "../ui";
import { LEGAL_VERSION } from "./Legal";

function LegalLinks() {
  return <p className="muted small"><a href="#/privacy">Privacy notice</a> · <a href="#/terms">Terms of use</a></p>;
}

function Brand() {
  return <div className="brand"><span className="mark"><Icon name="brief" /></span><span>{appName()}</span></div>;
}

export function SignIn({ config, onDone }: { config: Config | null; onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const dev = config?.auth === "dev";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr("");
    const [local, domain = ""] = email.trim().split("@");
    if (!local || !domain.includes(".") || email.trim().split("@").length !== 2 || /\s/.test(email.trim())) {
      setErr("Enter your email address"); return;
    }
    setBusy(true);
    try {
      if (dev) { await devSignIn(email.trim()); onDone(); }
      else { await emailLink(email.trim()); setSent(true); }
    } catch (x) { setErr((x as Error).message); }
    setBusy(false);
  };

  return (
    <div className="gate">
      <form className="card stack" onSubmit={submit}>
        <Brand />
        <h1>Sign in</h1>
        <p className="sub">Jobs ranked against your own confirmed experience, and a resume tailored for each.</p>
        {sent ? (
          <p>Check your email - the sign-in link is on its way to <b>{email}</b>.</p>
        ) : (
          <>
            <label htmlFor="email">Email</label>
            <input id="email" type="email" autoComplete="email" placeholder="name@example.com"
                   value={email} onChange={e => { setEmail(e.target.value); setErr(""); }} />
            {err && <p className="fail small">{err}</p>}
            <button className="primary" type="submit" disabled={busy}>{dev ? "Sign in" : "Email me a sign-in link"}</button>
            {!dev && <button type="button" onClick={() => google().catch(x => setErr((x as Error).message))}>Continue with Google</button>}
          </>
        )}
        {dev && <p className="muted small">Dev sign-in: any email works, on this machine only.</p>}
        <p className="muted small">Invite only for now. You can export or delete everything kept for you at any time.</p>
        <LegalLinks />
      </form>
    </div>
  );
}

/** An account an admin paused: nothing is done for it, but its data is still
 * its owner's to download or delete. */
export function Paused({ me, onSignOut, settings }: { me: Me; onSignOut: () => void; settings: React.ReactNode }) {
  const [leaving, setLeaving] = useState(false);
  if (leaving) return <div className="legal-page">{settings}</div>;
  return (
    <div className="gate">
      <div className="card stack">
        <Brand />
        <h1>Your account is paused</h1>
        <p className="sub">Signed in as {me.email}. The people running this service paused it, so nothing is done with
          your data for now. You can still download or delete everything kept for you.</p>
        <button onClick={() => setLeaving(true)}>Download or delete my data</button>
        <button className="quiet" onClick={onSignOut}>Sign out</button>
      </div>
    </div>
  );
}

export function Reconsent({ me, onAgreed, onSignOut, settings }: {
  me: Me; onAgreed: (m: Me) => void; onSignOut: () => void; settings: React.ReactNode;
}) {
  const [agree, setAgree] = useState(false);
  const [err, setErr] = useState("");
  const [leaving, setLeaving] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!agree) { setErr("Tick the box to agree, or take your data instead"); return; }
    try { onAgreed(await post<Me>("/api/consent", { consent: LEGAL_VERSION })); }
    catch (x) { setErr((x as Error).message); }
  };
  if (leaving) return <div className="legal-page">{settings}</div>;
  return (
    <div className="gate">
      <form className="card stack" onSubmit={submit}>
        <Brand />
        <h1>The privacy notice changed</h1>
        <p className="sub">Signed in as {me.email}. Nothing more is done with your data until you agree to the new version.</p>
        <label className="inline consent">
          <input type="checkbox" checked={agree} onChange={e => { setAgree(e.target.checked); setErr(""); }} />
          <span>I've read the new <a href="#/privacy" target="_blank" rel="noopener">privacy notice</a> and <a
            href="#/terms" target="_blank" rel="noopener">terms of use</a>, and I agree.</span>
        </label>
        {err && <p className="fail small">{err}</p>}
        <button className="primary" type="submit">Agree and continue</button>
        <button type="button" onClick={() => setLeaving(true)}>I don't agree - download or delete my data</button>
        <button type="button" className="quiet" onClick={onSignOut}>Sign out</button>
      </form>
    </div>
  );
}

export function Join({ me, onJoined, onSignOut }: { me: Me; onJoined: (m: Me) => void; onSignOut: () => void }) {
  const [code, setCode] = useState("");
  const [agree, setAgree] = useState(false);
  const [err, setErr] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!code.trim()) { setErr("Enter your invite code"); return; }
    if (!agree) { setErr("Tick the box to agree to the privacy notice and terms"); return; }
    try { onJoined(await post<Me>("/api/invite", { code, consent: LEGAL_VERSION })); }
    catch (x) { setErr((x as Error).message); }
  };
  return (
    <div className="gate">
      <form className="card stack" onSubmit={submit}>
        <Brand />
        <h1>Your invite code</h1>
        <p className="sub">Signed in as {me.email}. The beta is invite only - enter the code you were given.</p>
        <label htmlFor="code">Invite code</label>
        <input id="code" placeholder="ABCD-EFGH" autoComplete="off" value={code}
               onChange={e => { setCode(e.target.value); setErr(""); }} />
        <label className="inline consent">
          <input type="checkbox" checked={agree} onChange={e => { setAgree(e.target.checked); setErr(""); }} />
          <span>I'm 18 or older, and I agree to the <a href="#/privacy" target="_blank" rel="noopener">privacy
            notice</a> and the <a href="#/terms" target="_blank" rel="noopener">terms of use</a>.</span>
        </label>
        {err && <p className="fail small">{err}</p>}
        <button className="primary" type="submit">Join</button>
        <button type="button" className="quiet" onClick={onSignOut}>Sign out</button>
      </form>
    </div>
  );
}
