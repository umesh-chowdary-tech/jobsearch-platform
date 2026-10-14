// Your profile on one page - who you are, skills, experience (work, then
// personal projects) and what you're looking for - each part with Edit or
// Add. Every change is saved as you make it (onboard.resave). Checking what
// the AI found from your files is in Setup, one card at a time; this page
// only says how many are left. Skill gaps are on Market.

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { get, post } from "../api";
import { deck, EditItem, Fields, FIELDS, IDENTITY, PrefsForm, type Step } from "../checking";
import type { ProfileEntry, ProfileView, SetupItem, SetupState } from "../types";
import { Icon, useToast } from "../ui";

const KINDS: [string, string][] = [["work", "Work"], ["project", "Personal projects"], ["education", "Education"],
                                  ["certification", "Certifications"], ["award", "Awards"]];
const TIERS: [string, string, string][] = [
  ["core", "Strong", "Your main skills. Resumes lead with these."],
  ["working", "Used them", "Skills you've used in your work or projects."],
  ["scoped", "Used in part", "Used in a narrower way than the name suggests."],
  ["incidental", "Touched", "Part of your work, but never listed as a skill on a resume."],
];
const TIER_PICK: [string, string][] = [["core", "Strong - resumes lead with it"], ["working", "Used it"],
  ["incidental", "Touched it - never listed"], ["remove", "Not a skill of mine"]];
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

/** What editing needs: the draft, a way to change it, and which part is open. */
interface Ed { s: SetupState; step: Step; editing: string; open: (part: string) => void }

export function Profile() {
  const [s, setS] = useState<SetupState | null>(null);
  const [p, setP] = useState<ProfileView | null>(null);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState("");
  const toast = useToast();

  const loadView = useCallback(() => get<ProfileView>("/api/profile").then(setP).catch(e => setErr((e as Error).message)), []);
  useEffect(() => {
    get<SetupState>("/api/setup").then(x => { setS(x); if (x.exists) loadView(); }).catch(e => setErr((e as Error).message));
  }, [loadView]);
  const step: Step = useCallback(async (name, body = {}) => {
    try {
      const next = await post<SetupState>(`/api/setup/${name}`, body);
      setS(next);
      if (next.exists) await loadView();
      return next;
    } catch (e) { toast((e as Error).message); return null; }
  }, [toast, loadView]);

  if (err) return <p className="fail">{err}</p>;
  if (!s || (s.exists && !p)) return <p className="muted">Loading…</p>;
  if (!s.exists || !p) {
    return (
      <>
        <h1>Your profile</h1>
        <p className="empty">Your profile isn't saved yet. <a href="#/setup">Finish setup</a>: add your resume, check what the
          AI found, and save.</p>
      </>
    );
  }
  const drafted = s.items.length > 0;
  // Changes here write the profile once setup has saved it from this draft
  // (onboard.resave); after "Start over" in Setup, not until it's saved again.
  const ed: Ed | null = drafted && s.saved_at ? { s, step, editing, open: setEditing } : null;
  const left = drafted ? deck(s).length : 0;

  return (
    <>
      {left > 0 && (
        <a className="remind" href="#/setup">{left} {left === 1 ? "thing" : "things"} from your resume to check <span aria-hidden="true">→</span></a>
      )}
      <Head v={p} ed={ed} />
      {ed && s.blocked && <div className="notice">Your latest changes aren't in your profile yet: {s.blocked}.</div>}
      {!drafted && (
        <p className="small muted">This profile wasn't made from your files in Setup, so it's shown as it is. <a href="#/setup">Add
          your resume in Setup</a> to change it here.</p>
      )}
      {drafted && !s.saved_at && (
        <p className="small muted">You started setup over. <a href="#/setup">Finish it in Setup</a> and save, and you can change
          your profile here again.</p>
      )}
      <SkillsPart v={p} ed={ed} />
      <Experience v={p} ed={ed} />
      <PrefsPart v={p} ed={ed} />
    </>
  );
}

function EditButton({ onClick, label = "Edit" }: { onClick: () => void; label?: string }) {
  return <button className="quiet" onClick={onClick}><Icon name="edit" />{label}</button>;
}

// ── who you are ────────────────────────────────────────────────────────────

function Head({ v, ed }: { v: ProfileView; ed: Ed | null }) {
  if (ed?.editing === "head") return <><h1>{v.identity.name || "Your profile"}</h1><HeadEditor ed={ed} /></>;
  const links = Object.entries(v.links).map(([k, u]) => [k, href(u)] as const).filter(([, u]) => u);
  return (
    <div className="spread profile-head">
      <div>
        <h1>{v.identity.name || "Your profile"}</h1>
        <p className="sub">{[v.identity.title, v.identity.location].filter(Boolean).join(" · ") || " "}</p>
        {(links.length > 0 || v.languages.length > 0) && (
          <div className="links">
            {links.map(([k, u]) => (
              <a key={k} href={u!} target="_blank" rel="noopener noreferrer"><Icon name="external" />{LINK_NAMES[k] || k}</a>
            ))}
            {v.languages.length > 0 && <span className="muted">Speaks {v.languages.join(", ")}</span>}
          </div>
        )}
      </div>
      {ed && <button onClick={() => ed.open("head")}><Icon name="edit" />Edit</button>}
    </div>
  );
}

function HeadEditor({ ed }: { ed: Ed }) {
  const { s, step } = ed;
  const mine = (f: string) => s.items.find(i => i.kind === "identity" && i.field === f && i.state === "confirmed");
  const keys = Object.keys(IDENTITY);
  const [vals, setVals] = useState<Record<string, string>>(() => Object.fromEntries(keys.map(k => [k, String(mine(k)?.value ?? "")])));
  const [lang, setLang] = useState("");
  const [busy, setBusy] = useState(false);
  const langs = s.items.filter(i => i.kind === "language" && i.state === "confirmed");
  const save = async () => {
    setBusy(true);
    for (const k of keys) {
      const it = mine(k), now = vals[k].trim();
      if (now === String(it?.value ?? "")) continue;
      const ok = now ? await step("add", { kind: "identity", fields: { field: k, value: now } })
        : it ? await step("item", { id: it.id, state: "removed" }) : true;
      if (!ok) { setBusy(false); return; }
    }
    setBusy(false);
    ed.open("");
  };
  const addLang = async () => { if (await step("add", { kind: "language", fields: { value: lang } })) setLang(""); };
  return (
    <div className="card stack edit-card">
      <Fields fields={keys.map(k => [k, IDENTITY[k]])} vals={vals} set={setVals} />
      <p className="small muted">Email and phone go on your resumes; the Claude connector never sees them. Your
        title is your current job's title - change it under Experience.</p>
      <div>
        <div className="small muted">Languages you speak</div>
        <div className="row">
          {langs.map(l => (
            <span key={l.id} className="chip confirmed">{String(l.value)}
              <button aria-label={`Remove ${l.value}`} onClick={() => step("item", { id: l.id, state: "removed" })}>✕</button>
            </span>
          ))}
          <input type="text" value={lang} placeholder="Add a language" aria-label="Add a language" style={{ width: 160 }}
                 onChange={e => setLang(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && lang.trim()) addLang(); }} />
          <button disabled={!lang.trim()} onClick={addLang}>Add</button>
        </div>
      </div>
      <div className="row">
        <button className="primary" disabled={busy || !vals.name.trim()} onClick={save}><Icon name="check" />Save</button>
        <button className="quiet" onClick={() => ed.open("")}>Cancel</button>
      </div>
    </div>
  );
}

// ── skills ─────────────────────────────────────────────────────────────────

function SkillsPart({ v, ed }: { v: ProfileView; ed: Ed | null }) {
  const editing = ed?.editing === "skills";
  return (
    <>
      <div className="h2row">
        <h2>Skills</h2>
        {ed && !editing && ed.s.skills.length > 0 && <EditButton onClick={() => ed.open("skills")} />}
      </div>
      {editing ? <SkillsEditor ed={ed!} /> : <Skills v={v} editable={!!ed} />}
    </>
  );
}

function Skills({ v, editable }: { v: ProfileView; editable: boolean }) {
  const shown = TIERS.filter(([k]) => v.tiers[k]?.length);
  return (
    <div className="card">
      {shown.length ? shown.map(([k, label, help]) => (
        <div key={k} className="tier">
          <div className="lbl">{label} · {v.tiers[k].length}</div>
          <div className="help">{help}</div>
          <div>
            {v.tiers[k].map(s => (
              <span key={s.skill} className={`pill${k === "core" ? " c" : ""}`} title={s.scope || undefined}>
                {s.skill}{k === "scoped" && s.scope ? ` (${s.scope})` : ""}
              </span>
            ))}
          </div>
        </div>
      )) : <p className="empty">No skills yet.</p>}
      <p className="small muted tier">Skills come only from work you added. {editable
        ? "To add one, edit the work where you used it, under Experience."
        : <>To change one, <a href="#/setup">update it in Setup</a>.</>}</p>
    </div>
  );
}

function SkillsEditor({ ed }: { ed: Ed }) {
  const { s, step } = ed;
  const [tiers, setTiers] = useState<Record<string, string>>(() => Object.fromEntries(s.skills.map(k => [k.name, k.tier])));
  const changed = s.skills.some(k => tiers[k.name] !== k.tier);
  return (
    <div className="card stack edit-card">
      <p className="small">How well you know each. “Touched it” is never listed on a resume; “Not a skill of mine” takes it
        out. To add a skill, edit the work where you used it.</p>
      <div className="scroll-x"><table><tbody>
        {s.skills.map(k => (
          <tr key={k.name}>
            <td>{k.name}</td>
            <td className="muted small">{k.backed_by === 1 ? "1 piece of work" : `${k.backed_by} pieces of work`}</td>
            <td><select aria-label={`How well you know ${k.name}`} value={tiers[k.name]}
                        onChange={e => setTiers({ ...tiers, [k.name]: e.target.value })}>
              {TIER_PICK.map(([val, l]) => <option key={val} value={val}>{l}</option>)}
            </select></td>
          </tr>
        ))}
      </tbody></table></div>
      <div className="row">
        <button className="primary" disabled={!changed} onClick={async () => { if (await step("skills", { tiers })) ed.open(""); }}>
          <Icon name="check" />Save</button>
        <button className="quiet" onClick={() => ed.open("")}>Cancel</button>
      </div>
    </div>
  );
}

// ── experience ─────────────────────────────────────────────────────────────

function Experience({ v, ed }: { v: ProfileView; ed: Ed | null }) {
  const known = new Set(KINDS.map(([k]) => k));
  const groups: [string, ProfileEntry[]][] = [
    ...KINDS.map(([k, label]) => [label, v.entries.filter(e => (e.type || "work") === k)] as [string, ProfileEntry[]]),
    ["Other", v.entries.filter(e => !known.has(e.type || "work"))],
  ];
  const draftOf = (id: string) => ed?.s.items.find(i => i.kind === "entry" && i.id === id && i.state === "confirmed");
  const open = ed?.editing || "";
  return (
    <>
      <div className="h2row">
        <h2>Experience</h2>
        {ed && open !== "add" && <button className="quiet" onClick={() => ed.open("add")}><Icon name="plus" />Add work</button>}
      </div>
      {ed && open === "add" && <AddWork ed={ed} />}
      {ed && open === "jobs" ? <JobsEditor ed={ed} /> : v.employment.length > 0 ? (
        <div className="card history">
          <div className="spread card-top">
            <span className="lbl">Work history</span>
            {ed && <EditButton onClick={() => ed.open("jobs")} />}
          </div>
          {v.employment.map((j, n) => (
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
      ) : ed && <div><button onClick={() => ed.open("jobs")}><Icon name="plus" />Add a job</button></div>}
      {!v.employment.length && !v.entries.length && !ed && (
        <p className="empty">Nothing added yet. <a href="#/setup">Add your resumes in Setup</a>.</p>
      )}
      {groups.filter(([, es]) => es.length).map(([label, es]) => (
        <section key={label}>
          <h3 className="group">{label} · {es.length}</h3>
          <div className="stack">
            {es.map(e => {
              const d = draftOf(e.id);
              return ed && d && open === e.id ? <EntryEditor key={e.id} e={d} ed={ed} />
                : <Entry key={e.id} e={e} onEdit={ed && d ? () => ed.open(e.id) : undefined} />;
            })}
          </div>
        </section>
      ))}
    </>
  );
}

function Entry({ e, onEdit }: { e: ProfileEntry; onEdit?: () => void }) {
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
          {onEdit && <EditButton onClick={onEdit} />}
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

/** A new item in your own words: a job, a piece of work. */
function NewItem({ kind, ed, done, label }: { kind: string; ed: Ed; done: (next: SetupState) => void; label: string }) {
  const fields = FIELDS[kind];
  const [vals, setVals] = useState<Record<string, string>>(() => Object.fromEntries(fields.map(([k]) => [k, k === "type" ? "work" : ""])));
  const [busy, setBusy] = useState(false);
  const add = async () => {
    setBusy(true);
    const next = await ed.step("add", { kind, fields: Object.fromEntries(Object.entries(vals).filter(([, x]) => x.trim())) });
    setBusy(false);
    if (next) done(next);
  };
  return (
    <div className="stack">
      <Fields fields={fields} vals={vals} set={setVals} />
      <div className="row">
        <button className="primary" disabled={busy} onClick={add}><Icon name="plus" />{label}</button>
      </div>
    </div>
  );
}

function AddWork({ ed }: { ed: Ed }) {
  const before = new Set(ed.s.items.map(i => i.id));
  return (
    <div className="card stack edit-card">
      <div className="lbl">Add work, a personal project, education, a certification or an award</div>
      <p className="small muted">In your own words - it goes in as you write it. Then add its facts, results and skills.</p>
      <NewItem kind="entry" ed={ed} label="Add it" done={next => {
        const made = next.items.find(i => i.kind === "entry" && !before.has(i.id));
        ed.open(made ? made.id : "");
      }} />
      <div><button className="quiet" onClick={() => ed.open("")}>Cancel</button></div>
    </div>
  );
}

function JobsEditor({ ed }: { ed: Ed }) {
  const jobs = ed.s.items.filter(i => i.kind === "job" && i.state === "confirmed");
  const [adding, setAdding] = useState(!jobs.length);
  return (
    <div className="card stack edit-card">
      <div className="lbl">Work history</div>
      <p className="small muted">Your title on resumes is your current job's title.</p>
      {jobs.map(j => (
        <div key={`${j.id}-${JSON.stringify(j)}`} className="setup-item confirmed">
          <EditItem it={j} step={ed.step}
                    onRemove={() => { if (window.confirm(`Remove ${j.title || "this job"} at ${j.employer} from your profile?`)) ed.step("item", { id: j.id, state: "removed" }); }} />
        </div>
      ))}
      {adding ? (
        <div className="setup-item"><div className="small muted">A job in your own words</div>
          <NewItem kind="job" ed={ed} label="Add the job" done={() => setAdding(false)} /></div>
      ) : <div><button onClick={() => setAdding(true)}><Icon name="plus" />Add a job</button></div>}
      <div><button className="primary" onClick={() => ed.open("")}>Done</button></div>
    </div>
  );
}

/** One line under a piece of work - a fact, a result, a note - to reword or remove. */
function Line({ it, field, step, extra }: { it: SetupItem; field: string; step: Step; extra?: ReactNode }) {
  const [v, setV] = useState(String(it[field] ?? ""));
  const changed = v.trim() !== String(it[field] ?? "");
  return (
    <div className="line">
      <input type="text" value={v} aria-label="Edit this line" onChange={e => setV(e.target.value)} />
      {changed && <button onClick={() => step("item", { id: it.id, fields: { [field]: v } })}>Save</button>}
      {extra}
      <button className="quiet" aria-label="Remove this line" title="Remove" onClick={() => step("item", { id: it.id, state: "removed" })}>✕</button>
    </div>
  );
}

function AddLine({ placeholder, onAdd, children }: {
  placeholder: string; onAdd?: (text: string) => Promise<unknown>; children?: (text: string, clear: () => void) => ReactNode;
}) {
  const [v, setV] = useState("");
  const clear = () => setV("");
  const go = async () => { if (v.trim() && onAdd && await onAdd(v)) clear(); };
  return (
    <div className="line">
      <input type="text" value={v} placeholder={placeholder} aria-label={placeholder} onChange={e => setV(e.target.value)}
             onKeyDown={e => { if (e.key === "Enter" && onAdd) go(); }} />
      {children ? children(v, clear) : <button disabled={!v.trim()} onClick={go}><Icon name="plus" />Add</button>}
    </div>
  );
}

function Part({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <div className="part">
      <div className="lbl">{title}</div>
      <div className="help">{hint}</div>
      <div className="stack">{children}</div>
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

function EntryEditor({ e, ed }: { e: SetupItem; ed: Ed }) {
  const { s, step } = ed;
  const under = (k: string) => s.items.filter(i => i.kind === k && i.entry === e.id && i.state === "confirmed");
  const waiting = s.items.filter(i => i.entry === e.id && i.state === "pending").length;
  const add = (kind: string, fields: object) => step("add", { kind, entry: e.id, fields });
  const notes = under("note");
  return (
    <div className="card stack entry-edit">
      <EditItem key={JSON.stringify(e)} it={e} step={step} />
      <Part title="Facts" hint="What you did, in your own words. Numbers only if they're true.">
        {under("fact").map(f => <Line key={`${f.id}-${f.text}`} it={f} field="text" step={step} />)}
        <AddLine placeholder="Add a fact" onAdd={text => add("fact", { text })} />
      </Part>
      <Part title="Results" hint="Only a measured result is ever printed as a number; an estimate stays here, for you.">
        {under("metric").map(m => (
          <Line key={`${m.id}-${m.claim}`} it={m} field="claim" step={step} extra={
            <span className="row toggle">
              <button className={m.measured === true ? "primary" : ""} onClick={() => step("item", { id: m.id, fields: { measured: true } })}>Measured</button>
              <button className={m.measured === false ? "primary" : ""} onClick={() => step("item", { id: m.id, fields: { measured: false } })}>Estimate</button>
            </span>} />
        ))}
        <AddLine placeholder="Add a result, like “cut page load from 4 s to 1 s”">
          {(text, clear) => (
            <span className="row">
              <button disabled={!text.trim()} onClick={async () => { if (await add("metric", { claim: text, measured: true })) clear(); }}>Add · measured</button>
              <button disabled={!text.trim()} onClick={async () => { if (await add("metric", { claim: text, measured: false })) clear(); }}>Add · my estimate</button>
            </span>
          )}
        </AddLine>
      </Part>
      <Part title="Skills you used here" hint="Your Skills come from these.">
        {under("tech").length > 0 && (
          <div className="row">
            {under("tech").map(t => (
              <span key={t.id} className="chip confirmed">{String(t.name)}
                <button aria-label={`Remove ${t.name}`} onClick={() => step("item", { id: t.id, state: "removed" })}>✕</button>
              </span>
            ))}
          </div>
        )}
        <AddLine placeholder="Add a skill you used in this work" onAdd={name => add("tech", { name })} />
      </Part>
      <Part title="Note for your resumes" hint="Context for whoever words your resume, like what someone else built. Never printed.">
        {notes.map(n => <Line key={`${n.id}-${n.text}`} it={n} field="text" step={step} />)}
        {!notes.length && <AddLine placeholder="Add a note" onAdd={text => add("note", { text })} />}
      </Part>
      <Labels e={e} step={step} angles={s.interview.angles} />
      {waiting > 0 && <p className="small muted">{waiting} more from your files {waiting === 1 ? "is" : "are"} waiting
        under “Not in your profile yet”, above.</p>}
      <div className="row">
        <button className="primary" onClick={() => ed.open("")}>Done</button>
        <button className="quiet" onClick={async () => {
          if (window.confirm("Remove this from your profile? You can put it back from “You left out”.")) {
            if (await step("item", { id: e.id, state: "removed" })) ed.open("");
          }
        }}>Remove this work</button>
      </div>
    </div>
  );
}

// ── what you're looking for ────────────────────────────────────────────────

function PrefsPart({ v, ed }: { v: ProfileView; ed: Ed | null }) {
  const editing = ed?.editing === "prefs";
  return (
    <>
      <div className="h2row">
        <h2>What you're looking for</h2>
        {ed && !editing && <EditButton onClick={() => ed.open("prefs")} />}
      </div>
      {editing ? <PrefsEditor ed={ed!} /> : <Prefs v={v} editable={!!ed} />}
    </>
  );
}

function Prefs({ v, editable }: { v: ProfileView; editable: boolean }) {
  const p = v.prefs;
  if (!p) return <p className="empty">No preferences saved yet. <a href="#/setup">Set them in Setup</a>.</p>;
  const list = (xs: string[]) => (xs.length ? xs.join(", ") : "None");
  return (
    <div className="card">
      <dl className="prefs">
        <dt>Cities you prefer</dt><dd>{list(p.preferred)}</dd>
        <dt>Also fine</dt><dd>{list(p.acceptable)}</dd>
        <dt>Remote jobs</dt><dd>{p.remote ? "Yes" : "No"}</dd>
        <dt>Your years of experience</dt><dd>{p.years ?? "Not given"}</dd>
        <dt>Most years a job may ask</dt><dd>{p.max_years ?? "Not given"}</dd>
        <dt>Level</dt><dd>{p.level || "Not given"}</dd>
        <dt>Companies to watch</dt><dd>{list(p.watch)}</dd>
      </dl>
      <p className="small muted" style={{ marginBottom: 0 }}>Jobs outside these aren't kept for you.{!editable && <> <a href="#/setup">Change
        them in Setup</a>.</>}</p>
    </div>
  );
}

function PrefsEditor({ ed }: { ed: Ed }) {
  return (
    <div className="card edit-card">
      <PrefsForm s={ed.s} label="Save" onCancel={() => ed.open("")}
                 onSave={async v => { if (await ed.step("prefs", { prefs: v })) ed.open(""); }} />
    </div>
  );
}
