// Your profile as saved, on one page: skills, your experience - work, then
// personal projects - and what you're looking for. Skill gaps are on Market.
// Read-only: every change goes through Setup, where each one is checked.

import { useEffect, useState } from "react";
import { get } from "../api";
import type { ProfileEntry, ProfileView } from "../types";
import { go, Icon } from "../ui";

const KINDS: [string, string][] = [["work", "Work"], ["project", "Personal projects"], ["education", "Education"],
                                  ["certification", "Certifications"], ["award", "Awards"]];
const TIERS: [string, string, string][] = [
  ["core", "Strong", "Your main skills. Resumes lead with these."],
  ["working", "Used them", "Skills you've used in your work or projects."],
  ["scoped", "Used in part", "Used in a narrower way than the name suggests."],
  ["incidental", "Touched", "Part of your work, but never listed as a skill on a resume."],
];
const LINK_NAMES: Record<string, string> = { linkedin: "LinkedIn", github: "GitHub", portfolio: "Portfolio" };

/** A link to open, or null: only web addresses, never anything a browser would run. */
function href(u?: string | null): string | null {
  const s = String(u || "").trim();
  if (/^https?:\/\/[^\s]+$/i.test(s)) return s;
  if (/^[\w-]+(\.[\w-]+)+(\/\S*)?$/.test(s)) return `https://${s}`;
  return null;
}

function month(s?: string): string {
  if (!s) return "";
  if (s === "present") return "now";
  const m = /^(\d{4})-(\d{2})/.exec(s);
  return m ? new Date(Number(m[1]), Number(m[2]) - 1).toLocaleDateString(undefined, { month: "short", year: "numeric" }) : s;
}

export function Profile() {
  const [p, setP] = useState<ProfileView | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { get<ProfileView>("/api/profile").then(setP).catch(e => setErr((e as Error).message)); }, []);

  if (err) return <p className="fail">{err}</p>;
  if (!p) return <p className="muted">Loading…</p>;
  const links = Object.entries(p.links).map(([k, u]) => [k, href(u)] as const).filter(([, u]) => u);

  return (
    <>
      <div className="spread profile-head">
        <div>
          <h1>{p.identity.name || "Your profile"}</h1>
          <p className="sub">{[p.identity.title, p.identity.location].filter(Boolean).join(" · ") || " "}</p>
          {(links.length > 0 || p.languages.length > 0) && (
            <div className="links">
              {links.map(([k, u]) => (
                <a key={k} href={u!} target="_blank" rel="noopener noreferrer"><Icon name="external" />{LINK_NAMES[k] || k}</a>
              ))}
              {p.languages.length > 0 && <span className="muted">Speaks {p.languages.join(", ")}</span>}
            </div>
          )}
        </div>
        <button onClick={() => go("#/setup")}><Icon name="edit" />Update in Setup</button>
      </div>

      <h2>Skills</h2>
      <Skills p={p} />
      <Experience p={p} />
      <h2>What you're looking for</h2>
      <Prefs p={p} />
    </>
  );
}

function Skills({ p }: { p: ProfileView }) {
  const shown = TIERS.filter(([k]) => p.tiers[k]?.length);
  return (
    <div className="card">
      {shown.length ? shown.map(([k, label, help]) => (
        <div key={k} className="tier">
          <div className="lbl">{label} · {p.tiers[k].length}</div>
          <div className="help">{help}</div>
          <div>
            {p.tiers[k].map(s => (
              <span key={s.skill} className={`pill${k === "core" ? " c" : ""}`} title={s.scope || undefined}>
                {s.skill}{k === "scoped" && s.scope ? ` (${s.scope})` : ""}
              </span>
            ))}
          </div>
        </div>
      )) : <p className="empty">No skills yet.</p>}
      <p className="small muted tier">Skills come only from work you confirmed. To change one, <a href="#/setup">update it in Setup</a>.</p>
    </div>
  );
}

function Experience({ p }: { p: ProfileView }) {
  const known = new Set(KINDS.map(([k]) => k));
  const groups: [string, ProfileEntry[]][] = [
    ...KINDS.map(([k, label]) => [label, p.entries.filter(e => (e.type || "work") === k)] as [string, ProfileEntry[]]),
    ["Other", p.entries.filter(e => !known.has(e.type || "work"))],
  ];
  if (!p.employment.length && !p.entries.length) {
    return <><h2>Experience</h2><p className="empty">Nothing confirmed yet. <a href="#/setup">Add your resumes in Setup</a>.</p></>;
  }
  return (
    <>
      <h2>Experience</h2>
      {p.employment.length > 0 && (
        <div className="card history">
          {p.employment.map((j, n) => (
            <div key={n} className="h">
              <div>
                <b>{j.title || "Role not given"}</b> · {j.employer}
                {j.client && <span className="muted"> · for {j.client}</span>}
                {j.location && <div className="muted small">{j.location}</div>}
              </div>
              <span className="muted small">{[month(j.start), month(j.end)].filter(Boolean).join(" – ")}</span>
            </div>
          ))}
        </div>
      )}
      {groups.filter(([, es]) => es.length).map(([label, es]) => (
        <section key={label}>
          <h3 className="group">{label} · {es.length}</h3>
          <div className="stack">{es.map(e => <Entry key={e.id} e={e} />)}</div>
        </section>
      ))}
    </>
  );
}

function Entry({ e }: { e: ProfileEntry }) {
  const metrics = (e.metrics || []).map(m => (typeof m === "string" ? { claim: m } as { claim: string; measured?: boolean; estimate?: boolean } : m));
  const meta = [e.where, e.period].filter(Boolean).join(" · ");
  const evidence = href(e.evidence);
  return (
    <div className="card entry">
      <div className="spread">
        <div>
          <b>{e.title || e.headline || e.id}</b>
          {meta && <div className="muted small">{meta}</div>}
        </div>
        <div className="row">
          {e.priority === "low" && <span className="pill" title="Left off first when a resume runs short of room">cut first</span>}
          {e.status === "parked" && <span className="pill" title="Never put on a resume until you decide">parked</span>}
          {evidence && <a className="row small" href={evidence} target="_blank" rel="noopener noreferrer"><Icon name="external" />Open</a>}
        </div>
      </div>
      {e.title && e.headline && <p className="small">{e.headline}</p>}
      {((e.facts?.length || 0) > 0 || metrics.length > 0) && (
        <ul>
          {(e.facts || []).map((f, n) => <li key={n}>{f}</li>)}
          {metrics.map((m, n) => (
            <li key={`m${n}`}>
              {m.measured === true && <span className="pill c" title="Measured - a resume may print this number">measured</span>}
              {(m.estimate || m.measured === false) && (
                <span className="pill" title="Your estimate - kept for you, never printed on a resume as a number">estimate</span>
              )}{" "}{m.claim}
            </li>
          ))}
        </ul>
      )}
      {(e.tech?.length || 0) > 0 && <div className="tags">{e.tech!.map(t => <span key={t} className="pill">{t}</span>)}</div>}
      {e.notes && <p className="small muted">Note for your resumes: {e.notes}</p>}
      {(e.sources?.length || 0) > 0 && (
        <details>
          <summary>Where this came from</summary>
          <ul className="src-list">{e.sources!.map((s, n) => <li key={n}>{s}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

function Prefs({ p }: { p: ProfileView }) {
  const v = p.prefs;
  if (!v) return <p className="empty">No preferences saved yet. <a href="#/setup">Set them in Setup</a>.</p>;
  const list = (xs: string[]) => (xs.length ? xs.join(", ") : "None");
  return (
    <div className="card">
      <dl className="prefs">
        <dt>Cities you prefer</dt><dd>{list(v.preferred)}</dd>
        <dt>Also fine</dt><dd>{list(v.acceptable)}</dd>
        <dt>Remote jobs</dt><dd>{v.remote ? "Yes" : "No"}</dd>
        <dt>Your years of experience</dt><dd>{v.years ?? "Not given"}</dd>
        <dt>Most years a job may ask</dt><dd>{v.max_years ?? "Not given"}</dd>
        <dt>Level</dt><dd>{v.level || "Not given"}</dd>
        <dt>Companies to watch</dt><dd>{list(v.watch)}</dd>
      </dl>
      <p className="small muted" style={{ marginBottom: 0 }}>Jobs outside these aren't kept for you. <a href="#/setup">Change them in Setup</a>.</p>
    </div>
  );
}
