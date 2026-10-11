// A profile from a resume: upload, let the AI draft the facts, confirm each
// one against the line it came from, mark skills, say what you're after, save.
// Nothing is in the profile until it is confirmed here (docs/setup.md).

import { useCallback, useEffect, useState, type DragEvent } from "react";
import { get, post, upload } from "../api";
import type { SetupItem, SetupState } from "../types";
import { go, Icon, useToast } from "../ui";

const STEPS = ["Upload", "Draft", "Check facts", "Skills", "Preferences"];
const TIERS: [string, string][] = [["core", "Strong - lead with it"], ["working", "Used it"],
  ["incidental", "Touched it - never listed"], ["remove", "Not a skill of mine"]];
const KINDS: [string, string][] = [["work", "Work"], ["project", "Projects"], ["education", "Education"],
  ["certification", "Certifications"], ["award", "Awards"]];
const IDENTITY: Record<string, string> = { name: "Name", location: "Location", email: "Email", phone: "Phone",
  linkedin: "LinkedIn", github: "GitHub" };
const DROP_NOTE = "PDF, Word (.docx) or plain text, up to 5 MB";

type Step = (step: string, body?: unknown) => Promise<void>;

function Stepper({ s }: { s: SetupState }) {
  const now = s.status === "saved" ? STEPS.length : s.status === "new" ? 0 : s.status === "uploaded" ? 1
    : s.progress.pending ? 2 : 3;
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

function Upload({ s, onState }: { s: SetupState; onState: (s: SetupState) => void }) {
  const [note, setNote] = useState(DROP_NOTE);
  const [over, setOver] = useState(false);
  const toast = useToast();
  const send = async (file: File) => {
    setNote(`Reading ${file.name}…`);
    try { onState(await upload<SetupState>("/api/setup/upload", file)); setNote(DROP_NOTE); }
    catch (e) { toast((e as Error).message); setNote(DROP_NOTE); }
  };
  const drop = (e: DragEvent) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) send(f); };
  return (
    <div className="card stack">
      <h3>1 · Your resume</h3>
      <p className="small">Only its text, with your email, phone and links removed, is sent to the AI.</p>
      <label className={`drop${over ? " over" : ""}`} onDragOver={e => { e.preventDefault(); setOver(true); }}
             onDragLeave={() => setOver(false)} onDrop={drop}>
        <Icon name="upload" />
        <b>{s.file ? "Drop a new resume to start over" : "Drop your resume here, or choose a file"}</b>
        <span>{note}</span>
        <input type="file" className="sr-only" accept=".pdf,.docx,.txt"
               onChange={e => { const f = e.target.files?.[0]; if (f) send(f); e.target.value = ""; }} />
      </label>
      {s.file && <p className="muted small">Read: {s.lines} lines from {s.file}.</p>}
    </div>
  );
}

function Draft({ s, step }: { s: SetupState; step: Step }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="card stack">
      <h3>2 · Let the AI draft your facts</h3>
      <p className="small">One request, counted against your daily AI limit. Every item it returns must quote a line of your resume, or it's left out.</p>
      <details><summary>Exactly what will be sent</summary><pre className="box">{s.will_send}</pre></details>
      <button className="primary" disabled={busy} onClick={async () => { setBusy(true); await step("draft"); setBusy(false); }}>
        {busy ? "Drafting - about a minute…" : "Draft my facts"}
      </button>
    </div>
  );
}

function Item({ it, fields, step }: { it: SetupItem; fields: [string, string, boolean?][]; step: Step }) {
  const [vals, setVals] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map(([k]) => [k, String(it[k] ?? "")])));
  const act = (state: string) => {
    const changed = Object.fromEntries(Object.entries(vals).filter(([k, v]) => v !== String(it[k] ?? "")));
    step("item", { id: it.id, state, fields: changed });
  };
  return (
    <div className={`setup-item ${it.state}`}>
      <div className="fields">
        {fields.map(([k, label, multi]) => (
          <label key={k} className={multi ? "wide" : ""}>{label}
            {multi
              ? <textarea rows={2} value={vals[k]} onChange={e => setVals({ ...vals, [k]: e.target.value })} />
              : <input type="text" value={vals[k]} onChange={e => setVals({ ...vals, [k]: e.target.value })} />}
          </label>
        ))}
      </div>
      <div className="src">{it.source ? <>From your resume: <q>{it.source}</q></> : it.by === "you" ? "Written by you" : ""}</div>
      <div className="row">
        {it.state !== "confirmed" && <button className="primary" onClick={() => act("confirmed")}><Icon name="check" />Confirm</button>}
        {it.state !== "removed" && <button onClick={() => act("removed")}>Remove</button>}
        {it.state !== "pending" && <button className="quiet" onClick={() => act("pending")}>Undo</button>}
        <span className={`state ${it.state}`}>{it.state === "pending" ? "to check" : it.state}</span>
        {it.edited && <span className="muted small">edited by you</span>}
      </div>
    </div>
  );
}

function Entry({ e, items, step }: { e: SetupItem; items: SetupItem[]; step: Step }) {
  const [fact, setFact] = useState("");
  const facts = items.filter(i => i.kind === "fact" && i.entry === e.id);
  const tech = items.filter(i => i.kind === "tech" && i.entry === e.id);
  const pending = tech.filter(t => t.state === "pending");
  return (
    <div className="card stack" style={{ marginTop: 12 }}>
      <Item key={e.state} it={e} step={step} fields={[["title", "Name"], ["where", "Where"], ["period", "When"], ["headline", "What it was", true]]} />
      {e.state !== "removed" && (
        <div className="stack">
          {facts.map(f => <Item key={`${f.id}-${f.state}`} it={f} step={step} fields={[["text", "", true]]} />)}
          <div className="row">
            <input type="text" style={{ flex: 1, minWidth: 200 }} value={fact} onChange={x => setFact(x.target.value)}
                   placeholder="Add a fact in your own words (numbers only if they are true)" />
            <button onClick={async () => { await step("fact", { entry: e.id, text: fact }); setFact(""); }}>Add</button>
          </div>
          {tech.length > 0 && (
            <div>
              <div className="muted small">Skills this work names - each from its line:</div>
              {tech.map(t => (
                <span key={t.id} className={`chip ${t.state}`} title={t.source}>
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
                    Confirm all {pending.length} skills here
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

function Review({ s, step }: { s: SetupState; step: Step }) {
  const of = (kind: string) => s.items.filter(i => i.kind === kind);
  const p = s.progress;
  const total = p.confirmed + p.pending + p.removed;
  const checked = total - p.pending;
  return (
    <>
      <h3 style={{ marginTop: 24 }}>2 · Drafted{s.drafted_by ? ` by ${s.drafted_by}` : ""}</h3>
      <h3 style={{ marginTop: 16 }}>3 · Check every item</h3>
      <p className="small">{p.confirmed} confirmed · {p.pending} still to check · {p.removed} removed. Only confirmed items go into your profile. Your own edits stand as you write them.</p>
      <div className="progress" role="progressbar" aria-label="Items checked" aria-valuemin={0} aria-valuemax={total} aria-valuenow={checked}>
        <i style={{ width: `${total ? Math.round((100 * checked) / total) : 0}%` }} />
      </div>
      {of("identity").length > 0 && (
        <><h2>About you</h2><div className="card stack">
          {of("identity").map(i => <Item key={`${i.id}-${i.state}`} it={i} step={step} fields={[["value", IDENTITY[i.field || ""] || i.field || ""]]} />)}
        </div></>
      )}
      {of("job").length > 0 && (
        <><h2>Jobs</h2><div className="card stack">
          {of("job").map(j => <Item key={`${j.id}-${j.state}`} it={j} step={step} fields={[["title", "Title"], ["employer", "Employer"], ["location", "Location"], ["start", "From (YYYY-MM)"], ["end", "To (YYYY-MM or present)"]]} />)}
        </div></>
      )}
      {KINDS.map(([kind, label]) => {
        const entries = of("entry").filter(e => e.type === kind);
        return entries.length ? <div key={kind}><h2>{label}</h2>{entries.map(e => <Entry key={e.id} e={e} items={s.items} step={step} />)}</div> : null;
      })}
      {of("language").length > 0 && (
        <><h2>Languages</h2><div className="card stack">
          {of("language").map(l => <Item key={`${l.id}-${l.state}`} it={l} step={step} fields={[["value", ""]]} />)}
        </div></>
      )}
      {s.dropped.length > 0 && (
        <>
          <h2>Left out · {s.dropped.length}</h2>
          <p className="small">The AI said these, but they didn't match your resume, so they weren't kept.</p>
          <div className="card stack">
            {s.dropped.map((d, n) => (
              <div key={n} className="spread">
                <div>
                  <div className="small">{d.what}</div><div className="muted small">{d.why}</div>
                  {d.source && <div className="src"><q>{d.source}</q></div>}
                </div>
                {d.entry && d.source && <button onClick={() => step("restore", { index: n })}>Use the resume line</button>}
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
      <h3>4 · Skills</h3>
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
    if (s.progress.pending && !window.confirm(`${s.progress.pending} item(s) aren't confirmed and will be left out. Save anyway?`)) return;
    if (s.exists && !window.confirm("Replace your saved profile with this one?")) return;
    try {
      const r = await post<SetupState>("/api/setup/save", { prefs: v, replace: s.exists });
      toast(`Saved: ${r.result?.entries ?? 0} entries, ${r.result?.skills ?? 0} skills`);
      onSaved();
      go("#/");
    } catch (e) { toast((e as Error).message); }
  };
  return (
    <div className="card stack" style={{ marginTop: 24 }}>
      <h3>5 · What you're looking for</h3>
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
      <div><button className="primary" onClick={save}>{s.exists ? "Save and replace my profile" : "Save my profile"}</button></div>
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
    try { setS(await post<SetupState>(`/api/setup/${name}`, body)); }
    catch (e) { toast((e as Error).message); }
  }, [toast]);

  if (err) return <p className="fail">{err}</p>;
  if (!s) return <p className="muted">Loading…</p>;
  return (
    <>
      <h1>Set up from your resume</h1>
      <p className="sub">The AI drafts; you confirm. Nothing goes into your profile until you've checked it against your resume.</p>
      <Stepper s={s} />
      {s.exists && <div className="card" style={{ marginBottom: 12 }}>Your profile is saved. <a href="#/">See today's jobs</a>, or upload a resume to set it up again.</div>}
      <Upload s={s} onState={setS} />
      {s.status === "uploaded" && <div style={{ marginTop: 12 }}><Draft s={s} step={step} /></div>}
      {(s.status === "drafted" || s.status === "saved") && (
        <>
          <Review s={s} step={step} />
          <Skills s={s} step={step} />
          <Prefs key={JSON.stringify(s.prefs)} s={s} onSaved={onSaved} />
        </>
      )}
    </>
  );
}
