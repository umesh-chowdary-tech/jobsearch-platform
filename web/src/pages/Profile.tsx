// Your profile as saved: skills by tier, then every entry - the personal
// agent's Profile screen. Read-only: every change goes through Setup.

import { useEffect, useState } from "react";
import { get } from "../api";
import type { ProfileView } from "../types";
import { go, Icon } from "../ui";

const TIERS: [string, string][] = [
  ["core", "Core"], ["working", "Working"], ["scoped", "Scoped (narrower than the word)"],
  ["incidental", "Incidental (never under Skills)"], ["not_a_skill", "Not a skill"], ["banned", "Banned"],
];

export function Profile() {
  const [p, setP] = useState<ProfileView | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { get<ProfileView>("/api/profile").then(setP).catch(e => setErr((e as Error).message)); }, []);

  if (err) return <p className="fail">{err}</p>;
  if (!p) return <p className="muted">Loading…</p>;
  return (
    <>
      <div className="spread">
        <div>
          <h1>{p.identity.name || "Your profile"}</h1>
          <p className="sub">{[p.identity.title, p.identity.location].filter(Boolean).join(" · ")}
            {" · "}change anything here in Setup</p>
        </div>
        <button onClick={() => go("#/setup")}><Icon name="edit" />Update in Setup</button>
      </div>

      <h2>Skills</h2>
      {TIERS.filter(([k]) => p.tiers[k]?.length).map(([k, label]) => (
        <div key={k} className="pills" style={{ marginBottom: 10 }}>
          <div className="muted small">{label} · {p.tiers[k].length}</div>
          {p.tiers[k].map(s => (
            <span key={s.skill} className={`pill${k === "core" ? " c" : ""}`} title={s.scope || undefined}>{s.skill}</span>
          ))}
        </div>
      ))}

      <h2>Entries · {p.entries.length}</h2>
      {p.entries.map(e => (
        <div key={e.id} className="card stack" style={{ marginTop: 10 }}>
          <div className="spread">
            <b>{e.title || e.headline}</b>
            <span className="muted small">{[e.type, e.where, e.period, e.priority === "low" && "cut first"].filter(Boolean).join(" · ")}</span>
          </div>
          {e.title && <div className="small">{e.headline}</div>}
          <ul className="small">
            {(e.facts || []).map((f, n) => <li key={n}>{f}</li>)}
            {(e.metrics || []).filter(m => typeof m !== "string" && m.measured).map((m, n) => (
              <li key={`m${n}`}><b>measured: </b>{typeof m === "string" ? m : m.claim}</li>
            ))}
          </ul>
          <div className="pills">{(e.tech || []).map(t => <span key={t} className="pill">{t}</span>)}</div>
        </div>
      ))}
    </>
  );
}
