// Every resume built for you, newest first: what it says about you, the files
// to download, and which jobs it went to.

import { useEffect, useState } from "react";
import { get, openFile } from "../api";
import type { ResumeVariant } from "../types";
import { day, Icon, useToast } from "../ui";

const FILE_NAMES: Record<string, string> = { pdf: "PDF", html: "Web page", txt: "Plain text", tex: "LaTeX" };
const SKILLS_SHOWN = 12;

export function Resumes() {
  const [vs, setVs] = useState<ResumeVariant[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    get<{ variants: ResumeVariant[] }>("/api/resumes")
      .then(r => setVs([...r.variants].sort((a, b) => String(b.created).localeCompare(String(a.created)))))
      .catch(e => setErr((e as Error).message));
  }, []);

  if (err) return <p className="fail">{err}</p>;
  if (!vs) return <p className="muted">Loading…</p>;
  return (
    <>
      <h1>Resumes</h1>
      <p className="sub">Each is built for a job from your confirmed facts only, and reused for another job when nothing new would match it better.</p>
      {vs.length ? <div className="stack">{vs.map(v => <ResumeCard key={v.id} v={v} />)}</div> : (
        <div className="card stack">
          <p><b>No resumes yet.</b></p>
          <p className="small muted">Open any job in <a href="#/jobs">Jobs</a> and choose <b>Build resume</b>. It's built from what you
            added to your profile, matched to what that job asks for, and checked the way an applicant tracking system reads it.</p>
        </div>
      )}
    </>
  );
}

function ResumeCard({ v }: { v: ResumeVariant }) {
  const toast = useToast();
  const [all, setAll] = useState(false);
  const skills = all ? v.surfaces : v.surfaces.slice(0, SKILLS_SHOWN);
  return (
    <div className="card stack">
      <div className="spread">
        <div>
          <b>{v.descriptor || "Resume"}</b>
          <div className="muted small">Built {day(v.created)}</div>
        </div>
        {v.checks_ok !== null && (
          <span className={`badge ${v.checks_ok ? "strong" : "closed"}`}
                title="Its PDF's text, read back the way an applicant tracking system reads it">
            {v.checks_ok ? "Reads cleanly" : "Read-back check failed"}
          </span>
        )}
      </div>
      {v.summary && <p className="small">{v.summary}</p>}
      {v.surfaces.length > 0 && (
        <div className="tags">
          {skills.map(s => <span key={s} className="pill">{s}</span>)}
          {v.surfaces.length > SKILLS_SHOWN && (
            <button className="quiet small" onClick={() => setAll(!all)}>
              {all ? "Fewer" : `+${v.surfaces.length - SKILLS_SHOWN} more`}
            </button>
          )}
        </div>
      )}
      {v.files.length > 0 && (
        <div className="row">
          {v.files.map(f => {
            const ext = (f.name.split(".").pop() || "").toLowerCase();
            return (
              <button key={f.name} onClick={() => openFile(f.url).catch(e => toast((e as Error).message))}>
                <Icon name="file" />{FILE_NAMES[ext] || ext.toUpperCase()}
              </button>
            );
          })}
        </div>
      )}
      <div>
        <div className="muted small">Used for</div>
        {v.sent_to.length ? (
          <ul className="used">
            {v.sent_to.map((s, n) => {
              const what = `${s.role || "a job"}${s.company ? ` at ${s.company}` : ""}`;
              return (
                <li key={n}>
                  {s.known ? <a href={`#/p/${encodeURIComponent(s.posting_id)}`}>{what}</a> : <span>{what}</span>}
                  <span className="muted small">
                    {[s.date, s.ats ? `matches ${Math.round(s.ats.match * 100)}% of what it asks` : ""].filter(Boolean).join(" · ")}
                  </span>
                </li>
              );
            })}
          </ul>
        ) : <p className="small muted" style={{ margin: 0 }}>Not used for a job yet.</p>}
      </div>
    </div>
  );
}
