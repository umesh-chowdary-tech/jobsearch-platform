// Who has joined, what each costs this month, and invite codes.

import { useCallback, useEffect, useState } from "react";
import { get, post } from "../api";
import type { AdminOverview } from "../types";
import { day, useToast } from "../ui";

export function Admin() {
  const [o, setO] = useState<AdminOverview | null>(null);
  const [err, setErr] = useState("");
  const [n, setN] = useState(5);
  const [note, setNote] = useState("");
  const [made, setMade] = useState<string[]>([]);
  const toast = useToast();
  const load = useCallback(() => { get<AdminOverview>("/api/admin/overview").then(setO).catch(e => setErr((e as Error).message)); }, []);
  useEffect(load, [load]);

  const make = async () => {
    try { setMade((await post<{ codes: string[] }>("/api/admin/invites", { n, note })).codes); setNote(""); load(); }
    catch (e) { toast((e as Error).message); }
  };

  if (err) return <p className="fail">{err}</p>;
  if (!o) return <p className="muted">Loading…</p>;
  return (
    <>
      <h1>Admin</h1>
      <p className="sub">{o.people.length} {o.people.length === 1 ? "person" : "people"} · ${o.month_usd.toFixed(2)} of AI this month · {o.invites_used} invites used</p>

      <div className="card stack">
        <h3>Invite codes</h3>
        <div className="row">
          <label className="field" style={{ width: 90 }}>How many
            <input type="number" min={1} max={100} value={n} onChange={e => setN(Number(e.target.value))} /></label>
          <label className="field" style={{ flex: 1, minWidth: 200 }}>For (only you see this)
            <input value={note} onChange={e => setNote(e.target.value)} placeholder="College friends" /></label>
        </div>
        <div><button className="primary" onClick={make}>Make codes</button></div>
        {made.length > 0 && <pre className="box">{made.join("\n")}</pre>}
        {o.invites_unused.length > 0 && (
          <details><summary>{o.invites_unused.length} unused</summary>
            <pre className="box">{o.invites_unused.map(i => `${i.code}  ${i.note || ""}`).join("\n")}</pre>
          </details>
        )}
      </div>

      <h2>People</h2>
      <div className="card scroll-x">
        <table>
          <thead><tr><th>Email</th><th>Joined</th><th>Profile</th><th>AI requests</th><th>AI cost</th></tr></thead>
          <tbody>
            {o.people.map(p => (
              <tr key={p.profile}>
                <td>{p.email}</td><td>{p.joined ? day(p.joined) : ""}</td>
                <td>{p.profile_ready ? "set up" : "not yet"}</td>
                <td>{p.month.requests}</td><td>${p.month.usd.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!o.people.length && <p className="empty">Nobody has joined yet.</p>}
      </div>
    </>
  );
}
