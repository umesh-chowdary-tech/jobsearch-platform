// Referral asks (src/jobsearch/outreach.py): who to ask, a message built from
// your confirmed facts, and what's due. You send everything yourself; this
// only drafts and remembers.

import { useEffect, useState } from "react";
import { get, post } from "./api";
import type { Leads as LeadsData, OutreachDraft, ProfileView } from "./types";
import { day, Icon, useToast } from "./ui";

export const HOW: [string, string][] = [["warm", "Knows you"], ["ex-colleague", "Worked where you worked"],
                                        ["alumni", "Same college"], ["cold", "No connection yet"]];
export const HOW_WORD: Record<string, string> = Object.fromEntries(HOW);
const MARKS: [string, string][] = [["sent", "I sent it"], ["followed-up", "I followed up"], ["replied", "They replied"],
                                   ["referred", "They referred me"], ["declined", "They said no"], ["closed", "Close it"]];
const STATUS_WORD: Record<string, string> = { drafted: "draft", "followed-up": "followed up" };

function copy(text: string, toast: (m: string) => void) {
  navigator.clipboard.writeText(text).then(() => toast("Copied"), () => toast("Select the text to copy it"));
}

/** Search links to open yourself, strongest connection first. */
export function Leads({ id }: { id: string }) {
  const [l, setL] = useState<LeadsData | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const find = () => {
    setBusy(true);
    get<LeadsData>(`/api/postings/${encodeURIComponent(id)}/leads`)
      .then(setL).catch(e => setErr((e as Error).message)).finally(() => setBusy(false));
  };
  if (!l) {
    return (
      <>
        <button onClick={find} disabled={busy}>{busy ? "Looking…" : "Find people to ask"}</button>
        {err && <p className="fail small">{err}</p>}
      </>
    );
  }
  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>Each opens a LinkedIn search in your browser. SwitchKaro never reads LinkedIn.</p>
      <ul className="leads">
        {l.leads.map(x => (
          <li key={x.url}>
            <a href={x.url} target="_blank" rel="noopener noreferrer"><Icon name="external" /><span>{x.who}</span></a>
            <div className="muted small">{x.why}</div>
          </li>
        ))}
      </ul>
      {l.already_asked.length > 0 && (
        <p className="small">Already asked at {l.company}: {l.already_asked.map(a => `${a.to} (${STATUS_WORD[a.status] || a.status})`).join(", ")}</p>
      )}
    </div>
  );
}

/** Draft one ask. Nothing is sent: the person copies and sends it. */
export function DraftForm({ posting, onDone }: { posting: string; onDone: () => void }) {
  const [to, setTo] = useState("");
  const [how, setHow] = useState("");
  const [employer, setEmployer] = useState("");
  const [employers, setEmployers] = useState<string[] | null>(null);
  const [context, setContext] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  useEffect(() => {
    if (how !== "ex-colleague" || employers) return;
    get<ProfileView>("/api/profile").then(p => {
      const names = [...new Set(p.employment.map(j => j.employer).filter(Boolean))];
      setEmployers(names);
      setEmployer(e => e || names[0] || "");
    }).catch(() => setEmployers([]));
  }, [how, employers]);

  const submit = async () => {
    setErr("");
    if (!to.trim()) { setErr("Enter their name first"); return; }
    if (!how) { setErr("Choose how you know them"); return; }
    setBusy(true);
    try {
      await post("/api/outreach/draft", { posting, to: to.trim(), how, context: context.trim(),
                                          ...(how === "ex-colleague" && employer ? { employer } : {}) });
      toast("Drafted - copy it and send it yourself");
      setTo(""); setHow(""); setContext("");
      onDone();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <div className="card stack">
      <div className="fields">
        <label>Their name<input value={to} maxLength={80} onChange={e => setTo(e.target.value)} placeholder="e.g. Priya Shah" /></label>
        <label>How you know them
          {/* No default: a stranger mustn't get "good to reconnect" from an untouched dropdown. */}
          <select value={how} onChange={e => setHow(e.target.value)}>
            <option value="">Choose…</option>
            {HOW.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        {how === "ex-colleague" && (
          <label>Where you both worked
            <select value={employer} onChange={e => setEmployer(e.target.value)} disabled={!employers?.length}>
              {(employers || []).map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        )}
        <label className="wide">A line about how you know them (optional)
          <textarea rows={2} value={context} maxLength={300} onChange={e => setContext(e.target.value)}
                    placeholder="e.g. we were on the same project team at college" />
        </label>
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        The message is built from your confirmed facts. Only this line goes to an AI, to write one opening sentence,
        and never their name.
      </p>
      <div className="row">
        <button className="primary" onClick={submit} disabled={busy}>{busy ? "Drafting…" : "Draft the message"}</button>
        {err && <span className="fail small">{err}</span>}
      </div>
    </div>
  );
}

function Part({ label, text }: { label: string; text: string | null }) {
  const toast = useToast();
  if (!text) return null;
  return (
    <div className="part">
      <div className="spread">
        <span className="muted small">{label}</span>
        <button className="quiet" onClick={() => copy(text, toast)} aria-label={`Copy ${label}`}>Copy</button>
      </div>
      <pre className="box">{text}</pre>
    </div>
  );
}

export function MarkSelect({ d, onChange }: { d: OutreachDraft; onChange: () => void }) {
  const toast = useToast();
  return (
    <select aria-label={`Record what happened with ${d.to}`} value=""
            onChange={async e => {
              const v = e.target.value;
              if (!v) return;
              try { await post(`/api/outreach/${encodeURIComponent(d.id)}/mark`, { status: v }); toast("Saved"); onChange(); }
              catch (err) { toast((err as Error).message); }
            }}>
      <option value="">Record what happened…</option>
      {MARKS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select>
  );
}

export function DraftCard({ d, onChange }: { d: OutreachDraft; onChange: () => void }) {
  const problems = (d.checks || []).filter(c => !c.ok);
  return (
    <div className="card stack">
      <div className="spread">
        <div>
          <b>{d.to}</b>
          <div className="muted small">{[HOW_WORD[d.how] || d.how, STATUS_WORD[d.status] || d.status, d.by].join(" · ")}</div>
        </div>
        <MarkSelect d={d} onChange={onChange} />
      </div>
      {problems.map((c, n) => (
        <p key={n} className={`small ${c.level === "fail" ? "fail" : "muted"}`} style={{ margin: 0 }}>
          {c.level === "fail" ? "Fix before sending" : "Check"}: {c.detail}
        </p>
      ))}
      <Part label={`Connection note (${(d.note || "").length}/200)`} text={d.note} />
      <Part label="Message" text={d.message} />
      <Part label="Email subject" text={d.subject} />
      <Part label="For their company's referral form" text={d.blurb} />
      {d.history.some(h => h.status !== "drafted") && (
        <p className="muted small" style={{ margin: 0 }}>
          {d.history.map(h => `${STATUS_WORD[h.status] || h.status} ${day(h.at)}`).join(" → ")}
        </p>
      )}
    </div>
  );
}

/** One ask as a list row, for Applied and Today. */
export function AskRow({ d, onChange }: { d: OutreachDraft; onChange: () => void }) {
  return (
    <div className="item">
      <div>
        <div className="t"><a href={`#/p/${encodeURIComponent(d.posting_id)}`}>{d.to} · {d.role} at {d.company}</a></div>
        <div className="co">{HOW_WORD[d.how] || d.how} · {STATUS_WORD[d.status] || d.status}
          {d.need && <span className="badge possible" style={{ marginLeft: 8 }}>{d.need}</span>}</div>
      </div>
      <MarkSelect d={d} onChange={onChange} />
    </div>
  );
}
