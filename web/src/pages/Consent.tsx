// Where a person approves an app's access to their SwitchKaro - in practice
// the Claude connector (src/jobsearch/hosted/mcp.py). Supabase's OAuth server
// sends them here with ?authorization_id=... Only a client whose return
// address is Claude's own can be approved: anyone may register an app, under
// any name, so the name alone proves nothing.

import { useEffect, useState } from "react";
import { consentDetails, decideConsent, pendingConsent, type ConsentDetails } from "../auth";
import type { Me } from "../types";
import { Brand } from "./SignIn";

const CLAUDE = ["https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback"];

export function Consent({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const id = new URLSearchParams(window.location.search).get("authorization_id") || pendingConsent();
  const [d, setD] = useState<ConsentDetails | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const ready = me.member && !me.suspended && me.consent_current !== false;

  useEffect(() => {
    if (!id) { setErr("This page answers a request from an app. Start from Claude: Settings > Connectors."); return; }
    if (!ready) return;
    consentDetails(id).then(x => {
      if ("authorization_id" in x) setD(x);
      else window.location.assign(x.redirect_url);          // approved before: straight back
    }).catch(e => setErr((e as Error).message));
  }, [id, ready]);

  const decide = async (allow: boolean) => {
    if (!d) return;
    setBusy(true);
    try { await decideConsent(d.authorization_id, allow); }
    catch (e) { setErr((e as Error).message); setBusy(false); }
  };
  const home = <a href="/#/">Open SwitchKaro</a>;
  const claude = !!d && CLAUDE.includes(d.redirect_uri);
  let host = "";
  try { host = d ? new URL(d.redirect_uri).host : ""; } catch { host = d?.redirect_uri || ""; }

  return (
    <div className="gate">
      <div className="card stack">
        <Brand />
        {err ? (
          <><p className="fail">{err}</p><p>{home}</p></>
        ) : !me.member ? (
          <><h1>Join first</h1>
            <p className="sub">You're signed in as {me.email}, which hasn't joined SwitchKaro yet. Join with your invite code,
              then connect again from Claude.</p><p>{home} · <button className="quiet" onClick={onSignOut}>Sign out</button></p></>
        ) : me.suspended ? (
          <><h1>Account paused</h1><p className="sub">This account is paused, so nothing can connect to it.</p><p>{home}</p></>
        ) : me.consent_current === false ? (
          <><h1>One thing first</h1>
            <p className="sub">The privacy notice changed. Open SwitchKaro and agree to it, then connect again from Claude.</p><p>{home}</p></>
        ) : !d ? (
          <p className="muted">Loading…</p>
        ) : !claude ? (
          <><h1>Not allowed</h1>
            <p className="sub">Only Claude can connect to SwitchKaro. This request comes from “{d.client.name}”, which would
              send you back to {host}.</p>
            <div className="row"><button onClick={() => decide(false)} disabled={busy}>Refuse it</button></div></>
        ) : (
          <><h1>Connect Claude to your SwitchKaro?</h1>
            <p className="sub">Signed in as {me.email}.</p>
            <div className="small">
              <p style={{ marginTop: 0 }}><b>In your Claude chats, Claude will be able to:</b></p>
              <ul>
                <li>see your jobs, your profile and what you've decided</li>
                <li>read new jobs and record how well each fits you</li>
                <li>record what you tell it you did: shortlisted, applied, skipped, later</li>
              </ul>
              <p><b>It can't</b> apply anywhere, message anyone, or change your profile. What Claude reads goes to Anthropic
                under your own Claude account. Disconnect any time in SwitchKaro's Settings.</p>
            </div>
            <div className="row">
              <button className="primary" onClick={() => decide(true)} disabled={busy}>{busy ? "Connecting…" : "Connect"}</button>
              <button onClick={() => decide(false)} disabled={busy}>Cancel</button>
            </div></>
        )}
      </div>
    </div>
  );
}
