import { useState, type FormEvent } from "react";
import { post } from "../api";
import { devSignIn, emailLink, google, type Config } from "../auth";
import type { Me } from "../types";
import { Icon } from "../ui";

function Brand() {
  return <div className="brand"><span className="mark"><Icon name="brief" /></span><span>Job search</span></div>;
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
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) { setErr("Enter your email address"); return; }
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
      </form>
    </div>
  );
}

export function Join({ me, onJoined, onSignOut }: { me: Me; onJoined: (m: Me) => void; onSignOut: () => void }) {
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!code.trim()) { setErr("Enter your invite code"); return; }
    try { onJoined(await post<Me>("/api/invite", { code })); }
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
        {err && <p className="fail small">{err}</p>}
        <button className="primary" type="submit">Join</button>
        <button type="button" className="quiet" onClick={onSignOut}>Sign out</button>
      </form>
    </div>
  );
}
