// A profile from a person's own documents: add resumes (old versions help),
// a portfolio, GitHub and a note; let the AI read each one; confirm every
// fact against the line it came from, and pick the true version wherever the
// documents disagree; mark skills, say what you're after, save. Nothing is in
// the profile until it is confirmed here (docs/setup.md).

import { useCallback, useEffect, useState, type DragEvent } from "react";
import { get, post, upload } from "../api";
import type { SetupItem, SetupQuestion, SetupSource, SetupState } from "../types";
import { go, Icon, useToast, type IconName } from "../ui";

const STEPS = ["Your files", "Read", "Questions", "Check facts", "Skills", "Preferences"];
const TIERS: [string, string][] = [["core", "Strong - lead with it"], ["working", "Used it"],
  ["incidental", "Touched it - never listed"], ["remove", "Not a skill of mine"]];
const KINDS: [string, string][] = [["work", "Work"], ["project", "Projects"], ["education", "Education"],
  ["certification", "Certifications"], ["award", "Awards"]];
const IDENTITY: Record<string, string> = { name: "Name", location: "Location", email: "Email", phone: "Phone",
  linkedin: "LinkedIn", github: "GitHub", portfolio: "Portfolio" };
const SOURCE_ICON: Record<string, IconName> = { resume: "file", portfolio: "external", github: "brief", note: "list" };
const DROP_NOTE = "PDF, Word (.docx) or plain text, up to 5 MB each";

type Step = (step: string, body?: unknown) => Promise<boolean>;
type Names = Record<string, string>;

function Stepper({ s }: { s: SetupState }) {
  const waiting = s.sources.some(x => !x.drafted);
  const qs = s.interview.questions;
  const asking = !qs.length || qs.some(q => (!q.answer && !q.skipped) || (q.answer && !q.applied));
  const now = s.status === "saved" && !waiting ? STEPS.length : !s.sources.length ? 0 : waiting ? 1
    : asking ? 2 : s.progress.pending ? 3 : 4;
  return (
    <ol className="stepper" aria-label="Setup steps">
      {STEPS.map((label, i) => (
        <li key={label} className={i < now ? "done" : i === now ? "now" : ""} aria-current={i === now ? "step" : undefined}>
          <span className="n">{i < now ? <Icon name="check" /> : i + 1}</span>
          <span className="label">{label}{i < now && <span className="sr-only"> (done)</span>}</span>
        </li>
      ))}
    </ol>
  );
}

function Sources({ s, step, onState }: { s: SetupState; step: Step; onState: (s: SetupState) => void }) {
  const [note, setNote] = useState(DROP_NOTE);
  const [over, setOver] = useState(false);
  const [linkedin, setLinkedin] = useState(s.links.linkedin || "");
  const [github, setGithub] = useState("");
  const [page, setPage] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState("");
  const toast = useToast();
  const resumes = s.sources.filter(x => x.kind === "resume").length;
  const pages = s.sources.filter(x => x.kind === "portfolio").length;
  const hasGithub = s.sources.some(x => x.kind === "github");
  const myNote = s.sources.find(x => x.kind === "note");

  const send = async (files: File[]) => {
    for (const file of files) {
      setNote(`Reading ${file.name}…`);
      try { onState(await upload<SetupState>("/api/setup/upload", file)); }
      catch (e) { toast(`${file.name}: ${(e as Error).message}`); }
    }
    setNote(DROP_NOTE);
  };
  const drop = (e: DragEvent) => { e.preventDefault(); setOver(false); send([...e.dataTransfer.files]); };
  const link = async (kind: string, url: string, done: () => void) => {
    if (!url.trim()) return;
    setBusy(kind);
    if (await step("link", { kind, url })) done();
    setBusy("");
  };

  return (
    <div className="card stack">
      <h3>1 · Your files</h3>
      <p className="small">Add every version of your resume you have - older ones often say things the newest leaves out, and
        where they disagree you'll be asked which is true. Only their text is kept, with your contact details and personal
        details (date of birth, ID numbers, family, references) removed before anything is sent to the AI.</p>

      {s.sources.length > 0 && (
        <ul className="sources">
          {s.sources.map(x => <SourceRow key={x.id} x={x} step={step} />)}
        </ul>
      )}

      {resumes < s.limits.files ? (
        <label className={`drop${over ? " over" : ""}`} onDragOver={e => { e.preventDefault(); setOver(true); }}
               onDragLeave={() => setOver(false)} onDrop={drop}>
          <Icon name="upload" />
          <b>{resumes ? "Add another resume" : "Drop your resume here, or choose files"}</b>
          <span>{note} · {resumes} of {s.limits.files}</span>
          <input type="file" className="sr-only" accept=".pdf,.docx,.txt" multiple
                 onChange={e => { send([...(e.target.files || [])]); e.target.value = ""; }} />
        </label>
      ) : <p className="muted small">That's {s.limits.files} resumes - remove one to add another.</p>}

      <div className="fields">
        <label className="wide">LinkedIn profile - kept as a link for your resumes; LinkedIn isn't read
          <span className="row">
            <input type="url" placeholder="https://www.linkedin.com/in/your-name" value={linkedin}
                   onChange={e => setLinkedin(e.target.value)} style={{ flex: 1 }} />
            <button disabled={busy === "linkedin"} onClick={() => link("linkedin", linkedin, () => {})}>Save</button>
          </span>
        </label>
        {!hasGithub && (
          <label className="wide">GitHub - your public repositories and their READMEs are read
            <span className="row">
              <input type="url" placeholder="https://github.com/your-name" value={github}
                     onChange={e => setGithub(e.target.value)} style={{ flex: 1 }} />
              <button disabled={busy === "github"} onClick={() => link("github", github, () => setGithub(""))}>
                {busy === "github" ? "Reading…" : "Add"}</button>
            </span>
          </label>
        )}
        {pages < s.limits.pages && (
          <label className="wide">Portfolio or personal site - one page is read{pages ? "; add another page's address for more" : ""}
            <span className="row">
              <input type="url" placeholder="https://your-site.com/projects" value={page}
                     onChange={e => setPage(e.target.value)} style={{ flex: 1 }} />
              <button disabled={busy === "portfolio"} onClick={() => link("portfolio", page, () => setPage(""))}>
                {busy === "portfolio" ? "Reading…" : "Add"}</button>
            </span>
          </label>
        )}
        <label className="wide">Anything else about your work - in your own words (optional)
          <textarea rows={3} placeholder={myNote ? "Write a new note to replace yours" : "Projects not on your resume, what you did on a team project, tools you used…"}
                    value={text} onChange={e => setText(e.target.value)} />
          <span className="row">
            <button disabled={!text.trim() || busy === "note"} onClick={async () => {
              setBusy("note"); if (await step("note", { text })) setText(""); setBusy("");
            }}>{myNote ? "Replace my note" : "Add my note"}</button>
          </span>
        </label>
      </div>
      {s.sources.length > 0 && (
        <div><button className="quiet" onClick={() => {
          if (window.confirm("Start setup over? What you've added and checked here goes; a saved profile stays.")) step("reset");
        }}>Start over</button></div>
      )}
    </div>
  );
}

function SourceRow({ x, step }: { x: SetupSource; step: Step }) {
  return (
    <li className="source">
      <Icon name={SOURCE_ICON[x.kind] || "file"} />
      <div className="grow">
        <div><b>{x.name}</b> <span className="muted small">· {x.lines} lines · {x.drafted ? `read${x.drafted_by ? ` by ${x.drafted_by}` : ""}` : "not read yet"}</span></div>
        {x.notes.map((n, i) => <div key={i} className="small muted">{n}</div>)}
      </div>
      <button className="quiet" aria-label={`Remove ${x.name}`} onClick={() => {
        if (!x.drafted || window.confirm(`Remove ${x.name}? What only it said goes, unless you've confirmed it.`)) step("remove", { id: x.id });
      }}>Remove</button>
    </li>
  );
}

function Read({ s, step }: { s: SetupState; step: Step }) {
  const [busy, setBusy] = useState("");
  const waiting = s.sources.filter(x => !x.drafted);
  const run = async () => {
    for (const [i, x] of waiting.entries()) {
      setBusy(`Reading ${i + 1} of ${waiting.length}: ${x.name} - about a minute…`);
      if (!(await step("draft", { source: x.id }))) break;
    }
    setBusy("");
  };
  return (
    <div className="card stack">
      <h3>2 · Let the AI read {waiting.length === 1 ? "it" : `these ${waiting.length}`}</h3>
      <p className="small">Claude Opus reads each one carefully, one at a time, and drafts your facts. Every fact it
        returns must quote a line of what you added, or it's left out - and where your files say different things, you'll
        choose. Counted against your AI allowance.</p>
      {waiting.map(x => (
        <details key={x.id}><summary>Exactly what will be sent from {x.name}</summary><pre className="box">{x.will_send}</pre></details>
      ))}
      <button className="primary" disabled={!!busy} onClick={run}>{busy || `Read ${waiting.length === 1 ? "it" : `all ${waiting.length}`}`}</button>
    </div>
  );
}

function From({ it, names }: { it: SetupItem; names: Names }) {
  const srcs = it.sources || [];
  if (!srcs.length) return <div className="src">{it.by === "you" ? "Written by you" : it.source ? <q>{it.source}</q> : ""}</div>;
  const [first, ...rest] = srcs;
  return (
    <div className="src">From {names[first.src] || "a removed file"}: <q>{first.quote}</q>
      {rest.length > 0 && <> · also in {[...new Set(rest.map(r => names[r.src] || "a removed file"))].join(", ")}</>}
    </div>
  );
}

const SHOW: Record<string, string[]> = { fact: ["text"], identity: ["value"], language: ["value"],
  job: ["title", "start", "end"], entry: ["period", "headline"] };

function Choices({ it, names, step }: { it: SetupItem; names: Names; step: Step }) {
  const alts = it.alternatives || [];
  if (alts.length < 2) return null;
  const keys = SHOW[it.kind] || ["text"];
  return (
    <div className={`choices${it.conflict ? " conflict" : ""}`}>
      <div className="small"><b>{it.conflict ? `Which is true? ${it.conflict}.` : "Other wording from your files:"}</b></div>
      {alts.map((a, i) => (
        <div key={i} className="choice">
          <div className="grow">
            <div>{keys.map(k => String(a[k] ?? "")).filter(Boolean).join(" · ") || "(blank)"}</div>
            <div className="src">{[...new Set(a.sources.map(x => names[x.src] || "a removed file"))].join(", ")}</div>
          </div>
          <button onClick={() => step("item", { id: it.id, choose: i, state: "confirmed" })}>This one</button>
        </div>
      ))}
      {it.conflict && <div className="muted small">Neither? Edit it in your own words below, or remove it.</div>}
    </div>
  );
}

function Item({ it, fields, step, names }: { it: SetupItem; fields: [string, string, boolean?][]; step: Step; names: Names }) {
  const [vals, setVals] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map(([k]) => [k, String(it[k] ?? "")])));
  const act = (state: string) => {
    const changed = Object.fromEntries(Object.entries(vals).filter(([k, v]) => v !== String(it[k] ?? "")));
    step("item", { id: it.id, state, fields: changed });
  };
  return (
    <div className={`setup-item ${it.state}${it.conflict ? " disputed" : ""}`}>
      <Choices it={it} names={names} step={step} />
      <div className="fields">
        {fields.map(([k, label, multi]) => (
          <label key={k} className={multi ? "wide" : ""}>{label}
            {multi
              ? <textarea rows={2} value={vals[k]} onChange={e => setVals({ ...vals, [k]: e.target.value })} />
              : <input type="text" value={vals[k]} onChange={e => setVals({ ...vals, [k]: e.target.value })} />}
          </label>
        ))}
      </div>
      <From it={it} names={names} />
      <div className="row">
        {it.state !== "confirmed" && !it.conflict && <button className="primary" onClick={() => act("confirmed")}><Icon name="check" />Confirm</button>}
        {it.state !== "confirmed" && it.conflict && Object.entries(vals).some(([k, v]) => v !== String(it[k] ?? "")) &&
          <button className="primary" onClick={() => act("confirmed")}><Icon name="check" />Use my words</button>}
        {it.state !== "removed" && <button onClick={() => act("removed")}>Remove</button>}
        {it.state !== "pending" && <button className="quiet" onClick={() => act("pending")}>Undo</button>}
        <span className={`state ${it.state}`}>{it.conflict ? "your files differ" : it.state === "pending" ? "to check" : it.state}</span>
        {it.edited && <span className="muted small">edited by you</span>}
      </div>
    </div>
  );
}

function Labels({ e, step, angles }: { e: SetupItem; step: Step; angles: string[] }) {
  const mine = e.angles || [];
  return (
    <div className="row small">
      <span className="muted">What it shows:</span>
      {mine.map(a => (
        <span key={a} className="chip confirmed">{a}
          <button aria-label={`Remove ${a}`} onClick={() => step("labels", { entry: e.id, angles: mine.filter(x => x !== a) })}>✕</button>
        </span>
      ))}
      <select aria-label="Add what this work shows" value="" style={{ width: "auto" }}
              onChange={ev => ev.target.value && step("labels", { entry: e.id, angles: [...mine, ev.target.value] })}>
        <option value="">+ add</option>
        {angles.filter(a => !mine.includes(a)).map(a => <option key={a} value={a}>{a}</option>)}
      </select>
      <label className="inline"><input type="checkbox" checked={e.priority === "low"}
             onChange={ev => step("labels", { entry: e.id, cut_first: ev.target.checked })} /> Cut first when a resume runs long</label>
    </div>
  );
}

function Result({ m, step, names }: { m: SetupItem; step: Step; names: Names }) {
  const label = m.measured === true ? "Result - measured" : m.measured === false
    ? "Result - an estimate (never printed as a number)" : "Result - was this measured, or an estimate?";
  return (
    <div className="stack">
      <Item key={`${m.id}-${m.state}-${m.measured}`} it={m} step={step} names={names} fields={[["claim", label, true]]} />
      <div className="row small" style={{ marginLeft: 17 }}>
        <button className={m.measured === true ? "primary" : ""} onClick={() => step("item", { id: m.id, fields: { measured: true } })}>Measured</button>
        <button className={m.measured === false ? "primary" : ""} onClick={() => step("item", { id: m.id, fields: { measured: false } })}>An estimate</button>
      </div>
    </div>
  );
}

function Entry({ e, items, step, names, angles }: { e: SetupItem; items: SetupItem[]; step: Step; names: Names; angles: string[] }) {
  const [fact, setFact] = useState("");
  const facts = items.filter(i => i.kind === "fact" && i.entry === e.id);
  const results = items.filter(i => i.kind === "metric" && i.entry === e.id);
  const notes = items.filter(i => i.kind === "note" && i.entry === e.id);
  const tech = items.filter(i => i.kind === "tech" && i.entry === e.id);
  const pending = tech.filter(t => t.state === "pending" && !t.listed_only);
  return (
    <div className="card stack" style={{ marginTop: 12 }}>
      <Item key={`${e.state}-${e.conflict}`} it={e} step={step} names={names}
            fields={[["title", "Name"], ["where", "Where"], ["period", "When"], ["headline", "What it was", true]]} />
      {e.state !== "removed" && (
        <div className="stack">
          <Labels e={e} step={step} angles={angles} />
          {facts.map(f => <Item key={`${f.id}-${f.state}-${f.conflict}`} it={f} step={step} names={names} fields={[["text", "", true]]} />)}
          {results.map(m => <Result key={m.id} m={m} step={step} names={names} />)}
          {notes.map(n => <Item key={`${n.id}-${n.state}`} it={n} step={step} names={names}
                                fields={[["text", "Note for whoever words your resume - not printed", true]]} />)}
          <div className="row">
            <input type="text" style={{ flex: 1, minWidth: 200 }} value={fact} onChange={x => setFact(x.target.value)}
                   placeholder="Add a fact in your own words (numbers only if they are true)" />
            <button onClick={async () => { if (await step("fact", { entry: e.id, text: fact })) setFact(""); }}>Add</button>
          </div>
          {tech.length > 0 && (
            <div>
              <div className="muted small">Skills this work names - each from its line. Dashed ones are only in a skills
                list: confirm one only if you used it in this work.</div>
              {tech.map(t => (
                <span key={t.id} className={`chip ${t.state}${t.listed_only ? " listed" : ""}`}
                      title={`${t.listed_only ? "Only in a skills list. " : ""}${t.source || ""}`}>
                  {t.name}
                  {t.state !== "confirmed" && <button aria-label={`Confirm ${t.name}`} onClick={() => step("item", { id: t.id, state: "confirmed" })}>✓</button>}
                  {t.state !== "removed" && <button aria-label={`Remove ${t.name}`} onClick={() => step("item", { id: t.id, state: "removed" })}>✕</button>}
                </span>
              ))}
              {pending.length > 1 && (
                <div style={{ marginTop: 8 }}>
                  <button onClick={async () => {
                    for (const t of pending.slice(0, -1)) await post("/api/setup/item", { id: t.id, state: "confirmed" });
                    await step("item", { id: pending[pending.length - 1].id, state: "confirmed" });
                  }}>
                    Confirm the {pending.length} skills this work shows in use
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function QuestionRow({ q, step }: { q: SetupQuestion; step: Step }) {
  const [text, setText] = useState(q.answer || "");
  if (q.applied) {
    return (
      <div className="question done">
        <div><b>{q.text}</b></div>
        <div className="small">Your answer: {q.answer}</div>
        <div className="muted small">Used - what it added is below, for you to confirm.</div>
      </div>
    );
  }
  return (
    <div className={`question${q.answer ? " answered" : ""}${q.skipped ? " skipped" : ""}`}>
      <div><b>{q.text}</b></div>
      {q.why && <div className="muted small">{q.why}</div>}
      {q.options.length > 0 && (
        <div className="row">{q.options.map(o => (
          <button key={o} className={text === o ? "primary" : ""} onClick={() => setText(o)}>{o}</button>
        ))}</div>
      )}
      <textarea rows={2} value={text} onChange={e => setText(e.target.value)}
                placeholder="In your own words - 'I don't know' is a fine answer" />
      <div className="row">
        <button disabled={!text.trim() || text === q.answer} onClick={() => step("answer", { id: q.id, text })}>
          {q.answer ? "Update my answer" : "Save my answer"}</button>
        {!q.skipped ? <button className="quiet" onClick={() => step("answer", { id: q.id, skip: true })}>Skip</button>
          : <span className="muted small">skipped</span>}
        {q.answer && <span className="state confirmed">answered</span>}
      </div>
    </div>
  );
}

function Questions({ s, step }: { s: SetupState; step: Step }) {
  const [busy, setBusy] = useState("");
  const iv = s.interview;
  const open = iv.questions.filter(q => !q.answer && !q.skipped);
  const ready = iv.questions.filter(q => q.answer && !q.applied);
  const run = async (name: string, label: string) => { setBusy(label); await step(name); setBusy(""); };
  return (
    <div className="card stack" style={{ marginTop: 12 }}>
      <h3>3 · A few questions about your work</h3>
      <p className="small">Claude reads what you added and asks only what would help a recruiter see your real work -
        your own part in team projects, results, where you used a skill. Skip any you like. Your answers are kept with your
        profile, and anything taken from them is shown below for you to confirm.</p>
      {iv.questions.map(q => <QuestionRow key={`${q.id}-${q.answer ?? ""}-${q.skipped ?? ""}`} q={q} step={step} />)}
      <div className="row">
        {!iv.rounds && <button className="primary" disabled={!!busy} onClick={() => run("ask", "Thinking of questions…")}>
          {busy || "Ask me"}</button>}
        {ready.length > 0 && <button className="primary" disabled={!!busy} onClick={() => run("apply", "Using your answers…")}>
          {busy || `Use my ${ready.length === 1 ? "answer" : `${ready.length} answers`}`}</button>}
        {iv.done && <span className="muted small">Nothing more worth asking - what you've added covers it.</span>}
        {iv.rounds > 0 && !iv.done && !open.length && !ready.length && iv.rounds < iv.max_rounds &&
          <button disabled={!!busy} onClick={() => run("ask", "Thinking of more…")}>{busy || "Ask me more"}</button>}
      </div>
    </div>
  );
}

function Review({ s, step, names }: { s: SetupState; step: Step; names: Names }) {
  const of = (kind: string) => s.items.filter(i => i.kind === kind);
  const p = s.progress;
  const total = p.confirmed + p.pending + p.removed;
  const checked = total - p.pending;
  const disputed = s.items.filter(i => i.conflict && i.kind !== "entry" && i.kind !== "fact");
  return (
    <>
      <h3 style={{ marginTop: 24 }}>4 · Check every item</h3>
      <p className="small">{p.confirmed} confirmed · {p.pending} still to check · {p.removed} removed
        {p.conflicts ? <> · <b>{p.conflicts} where your files differ</b></> : null}. Only confirmed items go into your profile.
        Your own edits stand as you write them.</p>
      {s.merge_notes.map((n, i) => <div key={i} className="notice">{n}</div>)}
      <div className="progress" role="progressbar" aria-label="Items checked" aria-valuemin={0} aria-valuemax={total} aria-valuenow={checked}>
        <i style={{ width: `${total ? Math.round((100 * checked) / total) : 0}%` }} />
      </div>
      {disputed.length > 0 && (
        <><h2>Your files differ here</h2><div className="card stack">
          {disputed.map(i => <Item key={`${i.id}-${i.state}-${i.conflict}`} it={i} step={step} names={names}
            fields={i.kind === "job" ? [["title", "Title"], ["employer", "Employer"], ["start", "From (YYYY-MM)"], ["end", "To (YYYY-MM or present)"]]
              : [["value", IDENTITY[i.field || ""] || i.field || ""]]} />)}
        </div></>
      )}
      {of("identity").filter(i => !i.conflict).length > 0 && (
        <><h2>About you</h2><div className="card stack">
          {of("identity").filter(i => !i.conflict).map(i => <Item key={`${i.id}-${i.state}`} it={i} step={step} names={names} fields={[["value", IDENTITY[i.field || ""] || i.field || ""]]} />)}
        </div></>
      )}
      {of("job").filter(i => !i.conflict).length > 0 && (
        <><h2>Jobs</h2><div className="card stack">
          {of("job").filter(i => !i.conflict).map(j => <Item key={`${j.id}-${j.state}`} it={j} step={step} names={names} fields={[["title", "Title"], ["employer", "Employer"], ["client", "Client, if you worked for one through your employer"], ["location", "Location"], ["start", "From (YYYY-MM)"], ["end", "To (YYYY-MM or present)"]]} />)}
        </div></>
      )}
      {KINDS.map(([kind, label]) => {
        const entries = of("entry").filter(e => e.type === kind);
        return entries.length ? <div key={kind}><h2>{label}</h2>{entries.map(e => <Entry key={e.id} e={e} items={s.items} step={step} names={names} angles={s.interview.angles} />)}</div> : null;
      })}
      {of("language").length > 0 && (
        <><h2>Languages</h2><div className="card stack">
          {of("language").map(l => <Item key={`${l.id}-${l.state}`} it={l} step={step} names={names} fields={[["value", ""]]} />)}
        </div></>
      )}
      {s.dropped.length > 0 && (
        <>
          <h2>Left out · {s.dropped.length}</h2>
          <p className="small">The AI said these, but they didn't match what you added, so they weren't kept.</p>
          <div className="card stack">
            {s.dropped.map((d, n) => (
              <div key={n} className="spread">
                <div>
                  <div className="small">{d.what}</div><div className="muted small">{d.why}{d.src && names[d.src] ? ` (${names[d.src]})` : ""}</div>
                  {d.source && <div className="src"><q>{d.source}</q></div>}
                </div>
                {d.entry && d.source && <button onClick={() => step("restore", { index: n })}>Use the line itself</button>}
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}

function Skills({ s, step }: { s: SetupState; step: Step }) {
  const [tiers, setTiers] = useState<Record<string, string>>(() => Object.fromEntries(s.skills.map(k => [k.name, k.tier])));
  useEffect(() => setTiers(Object.fromEntries(s.skills.map(k => [k.name, k.tier]))), [s.skills]);
  return (
    <div className="card stack" style={{ marginTop: 24 }}>
      <h3>5 · Skills</h3>
      {!s.skills.length ? <p className="muted small">Confirm some work and the skills its lines name, and they appear here.</p> : (
        <>
          <p className="small">Only skills your confirmed work names. “Touched it” is never listed on a resume.</p>
          <div className="scroll-x"><table><tbody>
            {s.skills.map(k => (
              <tr key={k.name}>
                <td>{k.name}</td>
                <td className="muted small">{k.backed_by} entr{k.backed_by === 1 ? "y" : "ies"}</td>
                <td><select aria-label={`How well you know ${k.name}`} value={tiers[k.name]}
                            onChange={e => setTiers({ ...tiers, [k.name]: e.target.value })}>
                  {TIERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select></td>
              </tr>
            ))}
          </tbody></table></div>
          <div><button onClick={() => step("skills", { tiers })}>Save skill choices</button></div>
        </>
      )}
    </div>
  );
}

function Prefs({ s, onSaved }: { s: SetupState; onSaved: () => void }) {
  const p = s.prefs;
  const [v, setV] = useState({
    preferred: (p.preferred || []).join(", "), acceptable: (p.acceptable || []).join(", "), remote: p.remote !== false,
    years: p.years ?? 0, max_years: p.max_years ?? 2, level: p.level, skip: p.skip || [], watch: (p.watch || []).join(", "),
  });
  const toast = useToast();
  const save = async () => {
    if (s.progress.conflicts && !window.confirm(`${s.progress.conflicts} item(s) where your files differ aren't settled and will be left out. Save anyway?`)) return;
    if (s.progress.pending && !window.confirm(`${s.progress.pending} item(s) aren't confirmed and will be left out. Save anyway?`)) return;
    if (s.exists && !window.confirm("Update your saved profile with this? What you added to it by hand stays.")) return;
    try {
      const r = await post<SetupState>("/api/setup/save", { prefs: v, replace: s.exists });
      toast(`Saved: ${r.result?.entries ?? 0} entries, ${r.result?.skills ?? 0} skills`);
      onSaved();
      go("#/");
    } catch (e) { toast((e as Error).message); }
  };
  return (
    <div className="card stack" style={{ marginTop: 24 }}>
      <h3>6 · What you're looking for</h3>
      <label className="field">Cities you'd most like (comma-separated)
        <input value={v.preferred} onChange={e => setV({ ...v, preferred: e.target.value })} /></label>
      <label className="field">Also fine - cities or a country
        <input value={v.acceptable} onChange={e => setV({ ...v, acceptable: e.target.value })} /></label>
      <label className="inline"><input type="checkbox" checked={v.remote} onChange={e => setV({ ...v, remote: e.target.checked })} /> Remote roles are fine</label>
      <div className="row">
        <label className="field">Years of experience
          <input type="number" min={0} max={50} value={v.years} onChange={e => setV({ ...v, years: Number(e.target.value) })} /></label>
        <label className="field">Skip roles asking more than (years)
          <input type="number" min={0} max={60} value={v.max_years} onChange={e => setV({ ...v, max_years: Number(e.target.value) })} /></label>
      </div>
      <label className="field">You are
        <select value={v.level} onChange={e => setV({ ...v, level: e.target.value })}>
          {Object.entries(s.levels).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select></label>
      <div>
        <div className="muted small">Roles to skip</div>
        {s.skip_options.map(k => (
          <label key={k} className="inline">
            <input type="checkbox" checked={v.skip.includes(k)}
                   onChange={e => setV({ ...v, skip: e.target.checked ? [...v.skip, k] : v.skip.filter(x => x !== k) })} /> {k}
          </label>
        ))}
      </div>
      <label className="field">Companies you'd like watched (comma-separated)
        <input value={v.watch} onChange={e => setV({ ...v, watch: e.target.value })} /></label>
      <div><button className="primary" onClick={save}>{s.exists ? "Update my profile" : "Save my profile"}</button></div>
    </div>
  );
}

export function Setup({ onSaved }: { onSaved: () => void }) {
  const [s, setS] = useState<SetupState | null>(null);
  const [err, setErr] = useState("");
  const toast = useToast();
  useEffect(() => {
    get<SetupState>("/api/setup").then(setS).catch(e => setErr((e as Error).message));
  }, []);
  const step: Step = useCallback(async (name, body = {}) => {
    try { setS(await post<SetupState>(`/api/setup/${name}`, body)); return true; }
    catch (e) { toast((e as Error).message); return false; }
  }, [toast]);

  if (err) return <p className="fail">{err}</p>;
  if (!s) return <p className="muted">Loading…</p>;
  const names: Names = { ...Object.fromEntries(s.sources.map(x => [x.id, x.name])),
    ...Object.fromEntries(s.interview.questions.map(q => [q.id, "your answer"])) };
  const read = s.sources.some(x => x.drafted);
  return (
    <>
      <h1>Set up from your resumes</h1>
      <p className="sub">The AI drafts; you confirm. Nothing goes into your profile until you've checked it against what you added.</p>
      <Stepper s={s} />
      {s.exists && <div className="card" style={{ marginBottom: 12 }}>Your profile is saved. <a href="#/">See today's jobs</a>, or add
        another resume below - what you've already checked stays.</div>}
      <Sources s={s} step={step} onState={setS} />
      {s.sources.some(x => !x.drafted) && <div style={{ marginTop: 12 }}><Read s={s} step={step} /></div>}
      {read && (
        <>
          <Questions s={s} step={step} />
          <Review s={s} step={step} names={names} />
          <Skills s={s} step={step} />
          <Prefs key={JSON.stringify(s.prefs)} s={s} onSaved={onSaved} />
        </>
      )}
    </>
  );
}
