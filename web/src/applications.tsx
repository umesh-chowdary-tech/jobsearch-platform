// Every application, however it was made (src/jobsearch/mail_paste.py and
// applications.py): paste a job email or add one by hand, and correct what
// the rules read. No inbox is read and no AI sees the emails.

import { useState } from "react";
import { post } from "./api";
import { day, useToast } from "./ui";

export interface TrackedApp {
  id: string;
  company: string;
  role: string;
  status: string;
  posting_id: string | null;
  updated: string;
  look: string[];
  events: { date: string; status: string; subject: string; evidence: string }[];
}

export interface Tracked { apps: TrackedApp[]; kinds: Record<string, string>; statuses: string[] }

export const STATUS_WORD: Record<string, string> = {
  approached: "recruiter reached out", applied: "applied", update_waiting: "update on their site", on_hold: "on hold",
  interviewing: "interviewing", rejected: "rejected", cancelled: "role withdrawn", offer: "offer", checking: "checking",
};
const ADD_STATUSES = ["applied", "interviewing", "offer", "rejected", "on_hold", "cancelled", "approached"];

interface PasteResult { ask?: "kind" | "company"; duplicate?: boolean; company?: string; role?: string; subject?: string;
                        app?: TrackedApp }

export function PasteForm({ kinds, onDone }: { kinds: Record<string, string>; onDone: () => void }) {
  const [text, setText] = useState("");
  const [extra, setExtra] = useState({ subject: "", from: "", date: "", company: "", role: "" });
  const [ask, setAsk] = useState<PasteResult | null>(null);
  const [kind, setKind] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const toast = useToast();
  const set = (k: keyof typeof extra, v: string) => setExtra(e => ({ ...e, [k]: v }));

  const send = async () => {
    setErr("");
    if (!text.trim()) { setErr("Paste the email's text first"); return; }
    if (ask?.ask === "kind" && !kind) { setErr("Choose what the email says"); return; }
    if (ask?.ask === "company" && !extra.company.trim()) { setErr("Which company is it from?"); return; }
    setBusy(true);
    try {
      const r = await post<PasteResult>("/api/applications/paste", { text, ...extra, kind });
      if (r.ask) { setAsk(r); return; }
      toast(r.duplicate ? "That email is already in" : `Added: ${r.app?.company} · ${STATUS_WORD[r.app?.status || ""] || r.app?.status}`);
      setText(""); setExtra({ subject: "", from: "", date: "", company: "", role: "" }); setAsk(null); setKind("");
      onDone();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <div className="card stack">
      <label>The email - copy it from your inbox, with its From and Subject lines if you can
        <textarea rows={7} value={text} maxLength={20000} onChange={e => { setText(e.target.value); setAsk(null); }}
                  placeholder={"From: Talent Team <jobs@company.example>\nSubject: Your application for Backend Engineer\n\nThank you for applying…"} />
      </label>
      {ask?.ask === "kind" && (
        <fieldset className="choices">
          <legend className="small"><b>What does this email say?</b> The rules couldn't tell, and no AI reads it.</legend>
          {Object.entries(kinds).map(([k, label]) => (
            <label key={k} className="inline"><input type="radio" name="kind" checked={kind === k} onChange={() => setKind(k)} />{label}</label>
          ))}
        </fieldset>
      )}
      {ask?.ask === "company" && <p className="small"><b>Which company is it from?</b> The email doesn't say clearly.</p>}
      <details open={ask?.ask === "company"}>
        <summary>Company, role, date</summary>
        <div className="fields" style={{ marginTop: 8 }}>
          <label>Company<input value={extra.company} maxLength={120} onChange={e => set("company", e.target.value)} placeholder={ask?.company || "from the email"} /></label>
          <label>Role<input value={extra.role} maxLength={160} onChange={e => set("role", e.target.value)} placeholder={ask?.role || "from the email"} /></label>
          <label>Date<input type="date" value={extra.date} onChange={e => set("date", e.target.value)} /></label>
          <label>Subject<input value={extra.subject} maxLength={300} onChange={e => set("subject", e.target.value)} placeholder="if not in the text" /></label>
        </div>
      </details>
      <p className="small muted" style={{ margin: 0 }}>Only the subject line, the sender and the sentence that shows the status are kept - never the rest.</p>
      <div className="row">
        <button className="primary" onClick={send} disabled={busy}>{busy ? "Reading…" : "Add it"}</button>
        {err && <span className="fail small">{err}</span>}
      </div>
    </div>
  );
}

export function AddForm({ onDone }: { onDone: () => void }) {
  const [v, setV] = useState({ company: "", role: "", date: "", status: "applied" });
  const [err, setErr] = useState("");
  const toast = useToast();
  const send = async () => {
    setErr("");
    if (!v.company.trim()) { setErr("Name the company"); return; }
    try {
      await post("/api/applications/add", v);
      toast(`Added: ${v.company}`);
      setV({ company: "", role: "", date: "", status: "applied" });
      onDone();
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="card stack">
      <div className="fields">
        <label>Company<input value={v.company} maxLength={120} onChange={e => setV({ ...v, company: e.target.value })} /></label>
        <label>Role<input value={v.role} maxLength={160} onChange={e => setV({ ...v, role: e.target.value })} /></label>
        <label>Date<input type="date" value={v.date} onChange={e => setV({ ...v, date: e.target.value })} /></label>
        <label>Where it stands
          <select value={v.status} onChange={e => setV({ ...v, status: e.target.value })}>
            {ADD_STATUSES.map(s => <option key={s} value={s}>{STATUS_WORD[s]}</option>)}
          </select>
        </label>
      </div>
      <div className="row">
        <button className="primary" onClick={send}>Add it</button>
        {err && <span className="fail small">{err}</span>}
      </div>
    </div>
  );
}

export function AppRow({ a, statuses, onChange }: { a: TrackedApp; statuses: string[]; onChange: () => void }) {
  const toast = useToast();
  const act = async (what: string, body: unknown, done: string) => {
    try { await post(`/api/applications/${encodeURIComponent(a.id)}/${what}`, body); toast(done); onChange(); }
    catch (e) { toast((e as Error).message); }
  };
  return (
    <div className="item">
      <div className="grow">
        <div className="t">
          {a.posting_id ? <a href={`#/p/${encodeURIComponent(a.posting_id)}`}>{a.company || "Company not known"}</a> : <span>{a.company || "Company not known"}</span>}
          <span className={`badge ${a.status === "offer" || a.status === "interviewing" ? "strong" : a.status === "rejected" || a.status === "cancelled" ? "closed" : ""}`}>
            {STATUS_WORD[a.status] || a.status}
          </span>
        </div>
        <div className="co">{[a.role, `updated ${day(a.updated)}`].filter(Boolean).join(" · ")}</div>
        {a.look.length > 0 && (
          <div className="look small">
            Check: {a.look.join("; ")}{" "}
            <button className="quiet" onClick={() => act("confirm", {}, "Marked as right")}>Looks right</button>
          </div>
        )}
        <details>
          <summary className="small">{a.events.length} {a.events.length === 1 ? "update" : "updates"}</summary>
          <ul className="events">
            {a.events.map((e, n) => (
              <li key={n}><span className="muted">{day(e.date)}</span> {STATUS_WORD[e.status] || e.status} - {e.subject}
                {e.evidence && e.evidence !== e.subject && <div className="muted"><q>{e.evidence}</q></div>}
              </li>
            ))}
          </ul>
        </details>
      </div>
      <div className="acts">
        <select aria-label={`Where ${a.company} stands`} value=""
                onChange={e => e.target.value && act("status", { status: e.target.value }, "Saved")}>
          <option value="">Change status…</option>
          {statuses.map(s => <option key={s} value={s}>{STATUS_WORD[s] || s}</option>)}
        </select>
        <button className="quiet" onClick={() => act("ignore", { ignored: true }, "Hidden - not an application")}>Not an application</button>
      </div>
    </div>
  );
}
