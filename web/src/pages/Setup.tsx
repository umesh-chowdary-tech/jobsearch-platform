// A profile from a person's own documents: add resumes (old versions help),
// a portfolio, GitHub and a note; let the AI read each one; answer a few
// questions if you like; check what it found, one card at a time - only a
// yes puts anything in; say what jobs you want; save (docs/setup.md).

import { useCallback, useEffect, useState, type DragEvent } from "react";
import { get, post, upload } from "../api";
import { CheckCards, deck, PrefsForm, SaidNo, type Step } from "../checking";
import type { SetupQuestion, SetupSource, SetupState } from "../types";
import { go, Icon, useToast, type IconName } from "../ui";

const STEPS = ["Your files", "Read", "Check", "Save"];
const SOURCE_ICON: Record<string, IconName> = { resume: "file", portfolio: "external", github: "brief", note: "list" };
const DROP_NOTE = "PDF, Word (.docx) or plain text, up to 5 MB each";

function Stepper({ s }: { s: SetupState }) {
  const waiting = s.sources.some(x => !x.drafted);
  const left = deck(s).length;
  const now = !s.sources.length ? 0 : waiting ? 1 : left ? 2 : !s.exists ? 3 : STEPS.length;
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

/** Once everything is read, the files fold into one line. */
function FilesLine({ s, open }: { s: SetupState; open: () => void }) {
  return (
    <div className="card files-line">
      <Icon name="file" />
      <span className="grow small">{s.sources.length === 1 ? "1 file read" : `${s.sources.length} files read`}: {s.sources.map(x => x.name).join(", ")}</span>
      <button className="quiet" onClick={open}>Add or change files</button>
    </div>
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

function QuestionRow({ q, step }: { q: SetupQuestion; step: Step }) {
  const [text, setText] = useState(q.answer || "");
  if (q.applied) {
    return (
      <div className="question done">
        <div><b>{q.text}</b></div>
        <div className="small">Your answer: {q.answer}</div>
        <div className="muted small">Used - what it added comes up below, for you to check.</div>
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
      <h3>Optional · A few questions about your work</h3>
      <p className="small">Claude reads what you added and asks only what would help a recruiter see your real work -
        your own part in team projects, results, where you used a skill. Skip any you like. Your answers are kept with your
        profile, and anything taken from them comes up below as cards for you to check.</p>
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

/** Saving from setup: the first time, or again after starting over - what
 * jobs the person wants, then the profile. After that, changes save as
 * they're made. */
function SaveFirst({ s, step, onSaved }: { s: SetupState; step: Step; onSaved: () => void }) {
  const toast = useToast();
  const left = deck(s).length;
  const save = async (prefs: unknown) => {
    if (s.exists && !window.confirm("Update your saved profile with this? What you added to it by hand stays.")) return;
    if (!(await step("prefs", { prefs }))) return;
    const r = await step("save", { replace: s.exists });
    if (r) {
      toast(`Saved: ${r.result?.entries ?? 0} pieces of work, ${r.result?.skills ?? 0} skills`);
      onSaved();
      go("#/profile");
    }
  };
  return (
    <div className="card stack" style={{ marginTop: 24 }}>
      <h3>4 · What jobs do you want?</h3>
      <p className="small" style={{ margin: 0 }}>{left
        ? `${left} left to check above. Anything you don't check stays out of your profile - you can check it later.`
        : "Everything is checked. Say what you're looking for, and save."}</p>
      {s.blocked && <p className="small reason" style={{ margin: 0 }}>Before you can save: {s.blocked}.</p>}
      {/* Suggested from the jobs added so far (years, level), until the person saves. */}
      <PrefsForm key={JSON.stringify(s.prefs)} s={s} label={s.exists ? "Update my profile" : "Save my profile"} onSave={save} />
    </div>
  );
}

export function Setup({ onSaved }: { onSaved: () => void }) {
  const [s, setS] = useState<SetupState | null>(null);
  const [err, setErr] = useState("");
  const [files, setFiles] = useState(false);
  const toast = useToast();
  useEffect(() => {
    get<SetupState>("/api/setup").then(setS).catch(e => setErr((e as Error).message));
  }, []);
  const step: Step = useCallback(async (name, body = {}) => {
    try { const next = await post<SetupState>(`/api/setup/${name}`, body); setS(next); return next; }
    catch (e) { toast((e as Error).message); return null; }
  }, [toast]);

  if (err) return <p className="fail">{err}</p>;
  if (!s) return <p className="muted">Loading…</p>;
  const read = s.sources.some(x => x.drafted);
  const waiting = s.sources.some(x => !x.drafted);
  return (
    <>
      <h1>Set up from your resumes</h1>
      <p className="sub">Add your files and the AI reads them. Then you check what it found, one card at a time - only what
        you say yes to goes into your profile.</p>
      <Stepper s={s} />
      {read && !waiting && !files ? <FilesLine s={s} open={() => setFiles(true)} /> : <Sources s={s} step={step} onState={setS} />}
      {waiting && <div style={{ marginTop: 12 }}><Read s={s} step={step} /></div>}
      {read && (
        <>
          <Questions s={s} step={step} />
          <h3 style={{ marginTop: 24 }}>3 · Check what we found</h3>
          <CheckCards s={s} step={step} />
          <SaidNo s={s} step={step} />
          {s.exists && s.saved_at ? (
            <div className="card next-step">
              <span className="grow small">Your profile is saved. Change anything in it on your Profile page.</span>
              <button onClick={() => go("#/profile")}>Go to my profile</button>
            </div>
          ) : <SaveFirst s={s} step={step} onSaved={onSaved} />}
        </>
      )}
    </>
  );
}
