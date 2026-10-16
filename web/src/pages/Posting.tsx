// One job: why it fits, your decision, "good match?", the tailored resume,
// what the application form will ask, and asking someone for a referral.

import { useCallback, useEffect, useState } from "react";
import { get, openFile, post } from "../api";
import { STAGES, Star, useJob } from "../jobs";
import { DraftCard, DraftForm, Leads } from "../referrals";
import type { PostingDetail } from "../types";
import { ago, Avatar, FitBadge, Icon, useToast } from "../ui";

interface Kit {
  experience: { title: string; company: string; location: string; from: string; from_words: string; current: boolean }[];
  skills_add: { workday: string; why: string; seen: boolean; skill: string; scope?: string }[];
  not_backed: string[];
  certifications: { issuer: string; year: string; name: string }[];
  education: { school: string; degree: string; field: string; from: string; to: string; grade?: string }[];
  languages: string[];
  links: string[];
}

const PART_LABEL: [string, string, number][] = [
  ["skills", "Skills you can back", 45], ["gaps", "Required skills missing", 0], ["role", "Kind of work", 15],
  ["place", "Place", 15], ["level", "Level", 15], ["fresh", "How new", 10],
];

/** The offline match score, and what made it - worked out before any AI. */
function MatchParts({ score, parts }: { score: number; parts: Record<string, number> }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="small" style={{ marginTop: 8 }}>
      <button className="quiet" aria-expanded={open} onClick={() => setOpen(!open)}>{score}% match {open ? "▴" : "▾"}</button>
      {open && (
        <ul className="muted" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
          {PART_LABEL.filter(([k]) => k in parts).map(([k, label, max]) => (
            <li key={k}>{label}: {parts[k]}{max ? ` of ${max}` : ""}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CopyRow({ label, value }: { label: string; value: string }) {
  const toast = useToast();
  if (!value) return null;
  return (
    <div className="kit-row">
      <span className="k">{label}</span><span className="v">{value}</span>
      <button className="quiet" aria-label={`Copy ${label}`}
              onClick={() => navigator.clipboard.writeText(value).then(() => toast("Copied"), () => toast("Select the text to copy it"))}>
        Copy
      </button>
    </div>
  );
}

function ApplyKit({ id }: { id: string }) {
  const [kit, setKit] = useState<Kit | null>(null);
  const [err, setErr] = useState("");
  const load = () => { if (!kit) get<Kit>(`/api/postings/${encodeURIComponent(id)}/kit`).then(setKit).catch(e => setErr((e as Error).message)); };
  return (
    <details className="card" onToggle={e => (e.currentTarget as HTMLDetailsElement).open && load()} style={{ marginTop: 16 }}>
      <summary><b>Apply kit</b> · what the application form asks for, ready to copy</summary>
      {err && <p className="fail">{err}</p>}
      {!kit && !err && <p className="muted">Loading…</p>}
      {kit && (
        <div className="stack" style={{ marginTop: 12 }}>
          <h3>Experience</h3>
          {kit.experience.map((j, n) => (
            <div key={n}>
              <CopyRow label="Job title" value={j.title} /><CopyRow label="Company" value={j.company} />
              <CopyRow label="Location" value={j.location} /><CopyRow label="From" value={j.from} />
              {j.current && <p className="muted small">To: tick “I currently work here”.</p>}
            </div>
          ))}
          <h3>Skills to add · {kit.skills_add.length}</h3>
          {kit.skills_add.map(s => <CopyRow key={s.workday} label={s.why === "this posting asks for it" ? "★ asked" : "resume"} value={s.workday} />)}
          {kit.not_backed.length > 0 && (
            <p className="small"><b>Don't add, even though the posting asks: </b>{kit.not_backed.join(", ")}
              <span className="muted"> - nothing in your profile backs them.</span></p>
          )}
          {kit.certifications.length > 0 && <h3>Certifications</h3>}
          {kit.certifications.map(c => <CopyRow key={c.name} label={`${c.issuer} · ${c.year}`} value={c.name} />)}
          <h3>Education</h3>
          {kit.education.map((ed, n) => (
            <div key={n}>
              <CopyRow label="School" value={ed.school} /><CopyRow label="Degree" value={ed.degree} />
              <CopyRow label="Field of study" value={ed.field} /><CopyRow label="From" value={ed.from} />
              <CopyRow label="To" value={ed.to} />{ed.grade && <CopyRow label="Grade" value={ed.grade} />}
            </div>
          ))}
          {kit.languages.length > 0 && <h3>Languages</h3>}
          {kit.languages.map(l => <CopyRow key={l} label="Language" value={l} />)}
          {kit.links.length > 0 && <h3>Links</h3>}
          {kit.links.map(l => <CopyRow key={l} label={l.includes("github") ? "GitHub" : "LinkedIn"} value={l} />)}
        </div>
      )}
    </details>
  );
}

export function Posting({ id }: { id: string }) {
  const [p, setP] = useState<PostingDetail | null>(null);
  const [err, setErr] = useState("");
  const toast = useToast();
  const load = useCallback(() => {
    get<PostingDetail>(`/api/postings/${encodeURIComponent(id)}`).then(setP).catch(e => setErr((e as Error).message));
  }, [id]);
  useEffect(load, [load]);
  const build = useJob(load);

  if (err) return <p className="fail">{err}</p>;
  if (!p) return <p className="muted">Loading…</p>;
  const base = `/api/postings/${encodeURIComponent(p.id)}`;
  const setStatus = async (status: string) => {
    try { await post(`${base}/status`, { status }); toast(`Marked ${status}`); load(); }
    catch (e) { toast((e as Error).message); }
  };
  const vote = async (good: boolean) => {
    try { await post(`${base}/feedback`, { good }); toast("Thanks - that helps the ranking"); load(); }
    catch (e) { toast((e as Error).message); }
  };
  const pct = (x: number) => `${Math.round(x * 100)}%`;

  return (
    <>
      <div className="spread">
        <div className="posting-head">
          <Avatar name={p.company} big />
          <div>
            <div className="row" style={{ flexWrap: "nowrap", alignItems: "flex-start" }}>
              <Star row={p} onChange={load} /><h1>{p.title} · {p.company}</h1>
            </div>
            <p className="sub">{[p.location, p.live === false ? "closed on the board" : ""].filter(Boolean).join(" · ")}</p>
          </div>
        </div>
        <FitBadge fit={p.fit} />
      </div>

      <div className="tabs" role="group" aria-label="Your decision">
        {STAGES.map(s => (
          <button key={s} className={p.status === s ? "on" : ""} aria-pressed={p.status === s} onClick={() => setStatus(s)}>{s}</button>
        ))}
      </div>

      {p.why && <div className="why">{p.why} <span className="muted">— {p.judged_by}</span></div>}
      {p.checks && (
        <p className="small muted" style={{ marginTop: 4 }}>
          The AI checked: {Object.entries(p.checks).map(([k, v]) => `${k} ${v === "ok" ? "✓" : v === "no" ? "✗" : "?"}`).join(" · ")}
        </p>
      )}
      {p.match != null && p.verdict === "keep" && <MatchParts score={p.match} parts={p.match_parts} />}
      <div className="tags">
        {Object.keys(p.matched).map(k => <span key={k} className="pill c">{k}</span>)}
        {p.required_gaps.map(g => <span key={g} className="pill g" title="asked for, not backed">{g}</span>)}
      </div>
      {p.fit && (
        <div className="vote" style={{ marginTop: 12 }}>
          <span className="muted small">Good match?</span>
          <button className={p.feedback?.good === true ? "on" : ""} aria-pressed={p.feedback?.good === true} onClick={() => vote(true)}>
            <Icon name="thumbUp" /> Yes
          </button>
          <button className={p.feedback?.good === false ? "on" : ""} aria-pressed={p.feedback?.good === false} onClick={() => vote(false)}>
            <Icon name="thumbDown" /> No
          </button>
        </div>
      )}

      <div className="steps">
        <div className="card stack">
          <h3>1 · Resume</h3>
          {p.variant ? (
            <>
              {p.ats && (
                <p className="small">Matches <b>{pct(p.ats.match)}</b> of what the posting asks for
                  <span className="muted"> · your profile allows at most {pct(p.ats.ceiling)}</span></p>
              )}
              <div className="row">
                {p.files.map(f => (
                  <button key={f.name} onClick={() => openFile(f.url).catch(e => toast((e as Error).message))}>
                    <Icon name="file" />{f.name.split(".").pop()!.toUpperCase()}
                  </button>
                ))}
              </div>
              <button className="quiet" disabled={build.running} onClick={() => build.start("assemble", { posting: p.id })}>
                {build.running ? "Building…" : "Rebuild"}
              </button>
            </>
          ) : (
            <>
              <p className="small muted">A resume built from your confirmed facts only, for this job.</p>
              <button className="primary" disabled={build.running} onClick={() => build.start("assemble", { posting: p.id })}>
                {build.running ? "Building…" : "Build resume"}
              </button>
            </>
          )}
          {build.lines.length > 0 && <div className="console" aria-live="polite">{build.lines.join("\n")}</div>}
          {build.result === "failed" && <p className="fail small">The build didn't finish - see above.</p>}
        </div>
        <div className="card stack">
          <h3>2 · Apply</h3>
          <p className="small muted">You apply on the company's site. Nothing here submits anything.</p>
          <div className="row">
            {/^https?:\/\//.test(p.url) && (
              <a href={p.url} target="_blank" rel="noopener noreferrer" className="row"><Icon name="external" />Open posting</a>
            )}
            {p.status !== "applied" && <button onClick={() => setStatus("applied")}>Mark applied</button>}
          </div>
        </div>
        <div className="card stack">
          <h3>3 · Referral</h3>
          <p className="small muted">
            {p.drafts.length ? `${p.drafts.length} ${p.drafts.length === 1 ? "ask" : "asks"} below.` : "A referral often gets a resume read first."}
          </p>
          <Leads id={p.id} />
        </div>
      </div>

      <ApplyKit id={p.id} />

      <h2>Ask for a referral</h2>
      {p.variant
        ? <DraftForm posting={p.id} onDone={load} />
        : <p className="muted">Build the resume first: a referral message shouldn't promise one that doesn't exist yet.</p>}
      {p.drafts.length > 0 && (
        <div className="stack" style={{ marginTop: 12 }}>{p.drafts.map(d => <DraftCard key={d.id} d={d} onChange={load} />)}</div>
      )}

      <h2>The posting</h2>
      <div className="desc">{p.description || "No description stored."}</div>
      {p.history.length > 0 && (
        <>
          <h2>History</h2>
          <div className="list">
            {p.history.map((h, n) => (
              <div key={n} className="item">
                <span className="status">{h.status}{h.note ? ` - ${h.note}` : ""}</span>
                <span className="muted">{ago(h.at)}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}
