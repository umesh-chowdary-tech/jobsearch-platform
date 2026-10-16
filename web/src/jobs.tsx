// A posting as a list row, its actions, and running a job (build a resume,
// have the shortlist read) with its output followed live.

import { useCallback, useEffect, useRef, useState } from "react";
import { get, post } from "./api";
import type { JobSnapshot, Later, Row } from "./types";
import { ago, Avatar, day, FitBadge, fresh, Icon, JobTags, useToast } from "./ui";

export const STAGES = ["new", "shortlisted", "applied", "interviewing", "offer", "closed", "skipped"];

// The kinds of work a job title names (roles.py).
export const ROLE_LABEL: Record<string, string> = {
  backend: "Backend", frontend: "Frontend", fullstack: "Full stack", mobile: "Mobile", software: "Software",
  ml: "ML / AI", data: "Data", devops: "DevOps / SRE", security: "Security", qa: "QA / Testing",
  embedded: "Embedded", solutions: "Solutions / DevRel", support: "Support", management: "Management",
  design: "Design", product: "Product", program: "Program", "non-tech": "Non-tech", other: "Other",
};
export const roleLabel = (r?: string) => ROLE_LABEL[r || "other"] || r || "Other";

function laterText(l: Later | null): string {
  if (!l) return "";
  if (l.state === "someday") return "saved for later, no date";
  if (l.state === "due") return `back from later (saved ${l.since ? day(l.since) : ""})`;
  return `saved for later until ${l.until ? day(l.until) : ""}`;
}

export function Star({ row, onChange }: { row: Pick<Row, "id" | "favorite">; onChange: () => void }) {
  const [on, setOn] = useState(row.favorite);
  const toast = useToast();
  useEffect(() => setOn(row.favorite), [row.favorite]);
  const flip = async () => {
    setOn(!on);
    try { await post(`/api/postings/${encodeURIComponent(row.id)}/favorite`, { on: !on }); onChange(); }
    catch (e) { setOn(on); toast((e as Error).message); }
  };
  return (
    <button type="button" className={`star${on ? " on" : ""}`} onClick={flip} aria-pressed={on}
            aria-label={on ? "Remove from favorites" : "Add to favorites"} title={on ? "Favorite" : "Add to favorites"}>
      <Icon name="star" />
    </button>
  );
}

export function JobRow({ row, actions, onChange, stagePicker }: {
  row: Row; actions?: boolean; stagePicker?: boolean; onChange: () => void;
}) {
  const toast = useToast();
  const [menu, setMenu] = useState(false);
  const act = async (path: string, body: unknown, done: string) => {
    try { await post(`/api/postings/${encodeURIComponent(row.id)}/${path}`, body); toast(done); onChange(); }
    catch (e) { toast((e as Error).message); }
  };
  const meta = [row.location, row.role && roleLabel(row.role), row.variant && "resume ready", laterText(row.later)]
    .filter(Boolean).join(" · ");
  return (
    <div className="item">
      <div className="job-main">
        <Star row={row} onChange={onChange} />
        <Avatar name={row.company} />
        <div>
          <div className="t">
            <a href={`#/p/${encodeURIComponent(row.id)}`}>{row.title}</a>
            <FitBadge fit={row.fit} />
            {fresh(row.first_seen) && !row.closed && <span className="badge new" title={`First seen ${ago(row.first_seen)}`}>new</span>}
            {row.closed && <span className="badge closed">closed</span>}
            {row.match != null && row.verdict === "keep" && (
              <span className="badge" title="How well it matches your profile, worked out before any AI reads it">{row.match}% match</span>
            )}
          </div>
          <div className="co">{row.company}{meta && <span className="muted"> · {meta}</span>}</div>
          <JobTags row={row} />
        </div>
      </div>
      {actions && (
        <div className="acts">
          <button className="quiet" onClick={() => act("status", { status: "skipped" }, "Skipped")}>Skip</button>
          <button onClick={() => setMenu(!menu)} aria-expanded={menu}>{row.later ? "Later ✓" : "Later…"}</button>
          <button onClick={() => act("status", { status: "shortlisted" }, "Shortlisted")}>Shortlist</button>
        </div>
      )}
      {stagePicker && (
        <select aria-label={`Stage for ${row.title}`} value={row.status}
                onChange={e => act("status", { status: e.target.value }, `Marked ${e.target.value}`)}>
          {STAGES.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      )}
      {!actions && !stagePicker && <span className="status">{row.status}</span>}
      {menu && (
        <div className="menu row">
          <span className="muted small">Bring it back to Today in</span>
          {[[3, "3 days"], [7, "1 week"], [14, "2 weeks"]].map(([n, l]) => (
            <button key={n} className="quiet" onClick={() => act("later", { days: n }, `Back on Today in ${l}`)}>{l}</button>
          ))}
          <button className="quiet" onClick={() => act("later", { days: null }, "Saved for later, no date")}>No date</button>
          {row.later && <button className="quiet" onClick={() => act("later", { clear: true }, "Back on your list")}>Bring back now</button>}
        </div>
      )}
    </div>
  );
}

/** Start an action and follow its output until it ends. */
export function useJob(onDone: () => void) {
  const [lines, setLines] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<"" | "ok" | "failed">("");
  const timer = useRef<number>(0);
  const toast = useToast();
  useEffect(() => () => window.clearInterval(timer.current), []);

  const start = useCallback(async (action: string, opts: Record<string, unknown> = {}) => {
    setLines([]); setResult("");
    try { await post("/api/run", { action, ...opts }); }
    catch (e) { toast((e as Error).message); return; }
    setRunning(true);
    let since = 0;
    window.clearInterval(timer.current);
    timer.current = window.setInterval(async () => {
      let s: JobSnapshot;
      try { s = await get<JobSnapshot>(`/api/job?since=${since}`); } catch { return; }
      const job = s.current;
      if (!job) return;
      if (job.lines.length) { setLines(prev => [...prev, ...job.lines]); since = job.total_lines; }
      if (!job.done) return;
      window.clearInterval(timer.current);
      setRunning(false);
      setResult(job.ok ? "ok" : "failed");
      onDone();
    }, 1000);
  }, [onDone, toast]);

  return { start, lines, running, result };
}
