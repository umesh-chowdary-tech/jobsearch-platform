// Checking what the AI found, one plain question at a time: "Is this
// right?", "Which is right?", "Did you use Go here?", "Measured, or your
// guess?". Only a yes puts something in the profile (docs/setup.md). Also
// the editing pieces the profile page shares.

import { useState } from "react";
import type { SetupItem, SetupState } from "./types";
import { Icon } from "./ui";

/** One setup step; the new state, or null when it failed (a toast says why). */
export type Step = (step: string, body?: unknown) => Promise<SetupState | null>;
export type Names = Record<string, string>;

export const IDENTITY: Record<string, string> = { name: "Name", location: "Location", email: "Email", phone: "Phone",
  linkedin: "LinkedIn", github: "GitHub", portfolio: "Portfolio" };
export const ENTRY_KINDS: [string, string][] = [["work", "Work"], ["project", "Personal project"],
  ["education", "Education"], ["certification", "Certification"], ["award", "Award"]];

/** The fields a person can change on each kind of item: [key, label, long?]. */
export const FIELDS: Record<string, [string, string, boolean?][]> = {
  job: [["title", "Title"], ["employer", "Employer"], ["client", "Client, if you worked for one through your employer"],
        ["location", "Location"], ["start", "From (YYYY-MM)"], ["end", "To (YYYY-MM, or present)"]],
  entry: [["type", "Kind"], ["title", "Name"], ["where", "Where"], ["period", "When"], ["headline", "What it was", true]],
  fact: [["text", "Fact", true]],
  tech: [["name", "Skill"]],
  metric: [["claim", "Result", true]],
  note: [["text", "Note for whoever words your resume - never printed", true]],
  language: [["value", "Language"]],
};

export function sourceNames(s: SetupState): Names {
  return { ...Object.fromEntries(s.sources.map(x => [x.id, x.name])),
    ...Object.fromEntries(s.interview.questions.map(q => [q.id, "your answer"])) };
}

/** The boxes for a kind of item's fields. */
export function Fields({ fields, vals, set }: {
  fields: [string, string, boolean?][]; vals: Record<string, string>; set: (v: Record<string, string>) => void;
}) {
  return (
    <div className="fields">
      {fields.map(([k, lbl, long]) => (
        <label key={k} className={long ? "wide" : ""}>{lbl}
          {k === "type" ? (
            <select value={vals[k]} onChange={e => set({ ...vals, [k]: e.target.value })}>
              {ENTRY_KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          ) : long ? <textarea rows={2} value={vals[k]} onChange={e => set({ ...vals, [k]: e.target.value })} />
            : <input type="text" value={vals[k]} onChange={e => set({ ...vals, [k]: e.target.value })} />}
        </label>
      ))}
    </div>
  );
}

const shown = (it: SetupItem, k: string) => String(it[k] ?? (k === "type" ? "work" : ""));

/** An item's fields in your own words. Saving sends only what changed; with
 * `add`, it also puts the item in your profile. */
export function EditItem({ it, step, done, add, label, onRemove }: {
  it: SetupItem; step: Step; done?: () => void; add?: boolean; label?: string; onRemove?: () => void;
}) {
  const fields = it.kind === "identity" ? [["value", IDENTITY[it.field || ""] || "Value"] as [string, string]] : FIELDS[it.kind] || [];
  const [vals, setVals] = useState<Record<string, string>>(() => Object.fromEntries(fields.map(([k]) => [k, shown(it, k)])));
  const [busy, setBusy] = useState(false);
  const changed = Object.fromEntries(Object.entries(vals).filter(([k, v]) => v.trim() !== shown(it, k)));
  const save = async () => {
    setBusy(true);
    const ok = await step("item", { id: it.id, fields: changed, ...(add ? { state: "confirmed" } : {}) });
    setBusy(false);
    if (ok) done?.();
  };
  return (
    <div className="stack edit-item">
      <Fields fields={fields} vals={vals} set={setVals} />
      <div className="row">
        <button className="primary" disabled={busy || (!add && !Object.keys(changed).length)} onClick={save}>
          <Icon name="check" />{label || (add ? "Save and add" : "Save")}</button>
        {done && <button className="quiet" onClick={done}>Cancel</button>}
        {onRemove && <button className="quiet" onClick={onRemove}>Remove</button>}
      </div>
    </div>
  );
}

export interface PrefsDraft {
  preferred: string; acceptable: string; remote: boolean; years: number; max_years: number; level: string;
  skip: string[]; watch: string;
}

/** What jobs the person wants: cities, remote, years, level, roles to skip,
 * companies to watch. */
export function PrefsForm({ s, label, onSave, onCancel }: {
  s: SetupState; label: string; onSave: (v: PrefsDraft) => Promise<unknown>; onCancel?: () => void;
}) {
  const p = s.prefs;
  const [v, setV] = useState<PrefsDraft>({
    preferred: (p.preferred || []).join(", "), acceptable: (p.acceptable || []).join(", "), remote: p.remote !== false,
    years: p.years ?? 0, max_years: p.max_years ?? 2, level: p.level, skip: p.skip || [], watch: (p.watch || []).join(", "),
  });
  const [busy, setBusy] = useState(false);
  return (
    <div className="stack">
      <label className="field">Cities you'd most like (comma-separated)
        <input value={v.preferred} onChange={e => setV({ ...v, preferred: e.target.value })} /></label>
      <label className="field">Also fine - cities or a country
        <input value={v.acceptable} onChange={e => setV({ ...v, acceptable: e.target.value })} /></label>
      <label className="inline"><input type="checkbox" checked={v.remote} onChange={e => setV({ ...v, remote: e.target.checked })} /> Remote jobs are fine</label>
      <div className="row">
        <label className="field">Your years of experience
          <input type="number" min={0} max={50} value={v.years} onChange={e => setV({ ...v, years: Number(e.target.value) })} /></label>
        <label className="field">Skip jobs asking more than (years)
          <input type="number" min={0} max={60} value={v.max_years} onChange={e => setV({ ...v, max_years: Number(e.target.value) })} /></label>
      </div>
      <label className="field">You are
        <select value={v.level} onChange={e => setV({ ...v, level: e.target.value })}>
          {Object.entries(s.levels).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select></label>
      <div>
        <div className="muted small">Kinds of jobs to skip</div>
        {s.skip_options.map(k => (
          <label key={k} className="inline">
            <input type="checkbox" checked={v.skip.includes(k)}
                   onChange={e => setV({ ...v, skip: e.target.checked ? [...v.skip, k] : v.skip.filter(x => x !== k) })} /> {k}
          </label>
        ))}
      </div>
      <label className="field">Companies you'd like watched (comma-separated)
        <input value={v.watch} onChange={e => setV({ ...v, watch: e.target.value })} /></label>
      <div className="row">
        <button className="primary" disabled={busy} onClick={async () => { setBusy(true); await onSave(v); setBusy(false); }}>
          <Icon name="check" />{label}</button>
        {onCancel && <button className="quiet" onClick={onCancel}>Cancel</button>}
      </div>
    </div>
  );
}

// ── the cards ──────────────────────────────────────────────────────────────

type Card =
  | { key: string; kind: "differ"; it: SetupItem; under?: SetupItem }
  | { key: string; kind: "ask"; it: SetupItem; under?: SetupItem }
  | { key: string; kind: "work"; e: SetupItem; facts: SetupItem[]; skills: SetupItem[] }
  | { key: string; kind: "line"; index: number; text: string; under: SetupItem };

/** Every question still to ask, in the order a person would think of them:
 * where the files disagree, about you, jobs, each piece of work (with what
 * it says), then what's under work already added, languages, and lines the
 * AI couldn't use. What's under a piece of work waits until it's added. */
export function deck(s: SetupState): Card[] {
  const entries = Object.fromEntries(s.items.filter(i => i.kind === "entry").map(e => [e.id, e]));
  const added = (id?: string) => !!id && entries[id]?.state === "confirmed";
  const live = (i: SetupItem) => !i.entry || entries[i.entry]?.state !== "removed";
  const cards: Card[] = [];
  for (const it of s.items.filter(i => i.conflict && i.state !== "removed" && live(i))) {
    cards.push({ key: `d-${it.id}`, kind: "differ", it, under: entries[it.entry || ""] });
  }
  const plain = s.items.filter(i => i.state === "pending" && !i.conflict);
  for (const kind of ["identity", "job"]) {
    for (const it of plain.filter(i => i.kind === kind)) cards.push({ key: it.id, kind: "ask", it });
  }
  for (const e of plain.filter(i => i.kind === "entry")) {
    const under = plain.filter(i => i.entry === e.id);
    cards.push({ key: e.id, kind: "work", e, facts: under.filter(i => i.kind === "fact"),
                 skills: under.filter(i => i.kind === "tech" && !i.listed_only) });
  }
  for (const it of plain.filter(i => i.entry && added(i.entry))) {
    cards.push({ key: it.id, kind: "ask", it, under: entries[it.entry!] });
  }
  // A result already in, but nobody said whether it was measured.
  for (const it of s.items.filter(i => i.kind === "metric" && i.state === "confirmed" && i.measured == null && added(i.entry))) {
    cards.push({ key: `m-${it.id}`, kind: "ask", it, under: entries[it.entry!] });
  }
  for (const it of plain.filter(i => i.kind === "language")) cards.push({ key: it.id, kind: "ask", it });
  s.dropped.forEach((d, index) => {
    if (d.entry && d.source && added(d.entry)) {
      cards.push({ key: `l-${index}-${d.source}`, kind: "line", index, text: d.source, under: entries[d.entry] });
    }
  });
  return cards;
}

const nameOf = (e?: SetupItem) => (e ? String(e.title || e.headline || "this work") : "");
const workKind = (e: SetupItem) => (ENTRY_KINDS.find(([k]) => k === e.type) || ["", "Work"])[1];

function Quote({ it, names }: { it: SetupItem; names: Names }) {
  const first = (it.sources || [])[0];
  if (it.by === "you") return null;
  if (!first) return it.source ? <div className="from">From your resume: <q>{it.source}</q></div> : null;
  return <div className="from">From {names[first.src] || "your files"}: <q>{first.quote}</q></div>;
}

/** The question and the thing it's about, in plain words. */
function ask(it: SetupItem, under?: SetupItem): [string, string, string?] {
  const where = under ? nameOf(under) : "";
  switch (it.kind) {
    case "identity": return ["Is this right?", String(it.value ?? ""), `Your ${(IDENTITY[it.field || ""] || it.field || "").toLowerCase()}`];
    case "job": return ["Did you work here?", [it.title, it.employer].filter(Boolean).join(" at "),
      [[it.start, it.end === "present" ? "now" : it.end].filter(Boolean).join(" – "), it.location].filter(Boolean).join(" · ")];
    case "tech": return [`Did you use ${it.name} in ${where}?`, "", it.listed_only
      ? "Your resume lists it as a skill, but doesn't say where you used it." : undefined];
    case "metric": return ["Is this number measured, or your guess?", String(it.claim ?? ""),
      "Only a measured number is ever printed on your resume."];
    case "note": return ["Keep this note?", String(it.text ?? ""), `About ${where} - for whoever words your resume. Never printed.`];
    case "language": return [`Do you speak ${it.value}?`, ""];
    default: return ["Is this right?", String(it.text ?? ""), where ? `About ${where}` : undefined];
  }
}

function AskCard({ it, under, step, names }: { it: SetupItem; under?: SetupItem; step: Step; names: Names }) {
  const [editing, setEditing] = useState(false);
  const [q, big, sub] = ask(it, under);
  const below = it.kind === "job" || it.kind === "metric";     // a detail under it, not a label over it
  const yes = (extra: object = {}) => step("item", { id: it.id, state: "confirmed", ...extra });
  const no = () => step("item", { id: it.id, state: "removed" });
  return (
    <>
      <div className="ask">{q}</div>
      {sub && !below && <div className="sub-line">{sub}</div>}
      {big && <div className="big">{big}</div>}
      {sub && below && <div className="sub-line">{sub}</div>}
      <Quote it={it} names={names} />
      {editing ? <EditItem it={it} step={step} add done={() => setEditing(false)} /> : (
        <div className="answers">
          {it.kind === "metric" ? (
            <>
              <button className="primary" onClick={() => yes({ fields: { measured: true } })}>Measured</button>
              <button className="primary" onClick={() => yes({ fields: { measured: false } })}>My guess</button>
              <button onClick={no}>Not true</button>
            </>
          ) : it.kind === "tech" || it.kind === "language" ? (
            <>
              <button className="primary" onClick={() => yes()}><Icon name="check" />Yes</button>
              <button onClick={no}>No</button>
            </>
          ) : (
            <>
              <button className="primary" onClick={() => yes()}><Icon name="check" />Yes, add it</button>
              <button onClick={() => setEditing(true)}><Icon name="edit" />Change</button>
              <button onClick={no}>No</button>
            </>
          )}
        </div>
      )}
    </>
  );
}

const ALT: Record<string, string[]> = { fact: ["text"], identity: ["value"], language: ["value"],
  job: ["title", "start", "end"], entry: ["title", "period", "headline"] };

function DifferCard({ it, under, step, names }: { it: SetupItem; under?: SetupItem; step: Step; names: Names }) {
  const [editing, setEditing] = useState(false);
  const keys = ALT[it.kind] || ["text"];
  return (
    <>
      <div className="ask">Your files say different things. Which is right?</div>
      {under && <div className="sub-line">About {nameOf(under)}</div>}
      {editing ? <EditItem it={it} step={step} add label="Use my words" done={() => setEditing(false)} /> : (
        <div className="stack">
          {(it.alternatives || []).map((a, i) => (
            <button key={i} className="choice-btn" onClick={() => step("item", { id: it.id, choose: i, state: "confirmed" })}>
              <span>{keys.map(k => String(a[k] ?? "")).filter(Boolean).join(" · ") || "(blank)"}</span>
              <span className="from">{[...new Set(a.sources.map(x => names[x.src] || "your files"))].join(", ")}</span>
            </button>
          ))}
          <div className="answers">
            <button onClick={() => setEditing(true)}><Icon name="edit" />Neither - I'll write it</button>
            <button className="quiet" onClick={() => step("item", { id: it.id, state: "removed" })}>Leave it out</button>
          </div>
        </div>
      )}
    </>
  );
}

function WorkCard({ e, facts, skills, step, names }: {
  e: SetupItem; facts: SetupItem[]; skills: SetupItem[]; step: Step; names: Names;
}) {
  const [editing, setEditing] = useState(false);
  const [off, setOff] = useState<Set<string>>(new Set());
  const flip = (id: string) => setOff(o => { const n = new Set(o); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const yes = async () => {
    const kept = [...facts, ...skills].filter(i => !off.has(i.id)).map(i => i.id);
    if (off.size && !(await step("items", { ids: [...off], state: "removed" }))) return;
    await step("items", { ids: [e.id, ...kept], state: "confirmed" });
  };
  const where = [e.where, e.period].filter(Boolean).join(" · ");
  return (
    <>
      <div className="ask">Is this right?</div>
      <div className="sub-line">{workKind(e)}{where ? ` · ${where}` : ""}</div>
      <div className="big">{e.title || e.headline}</div>
      {e.title && e.headline && <div>{e.headline}</div>}
      <Quote it={e} names={names} />
      {editing ? <EditItem it={e} step={step} add done={() => setEditing(false)} /> : (
        <>
          {facts.length > 0 && (
            <div className="ticks">
              <div className="sub-line">What you did - untick anything that isn't true</div>
              {facts.map(f => (
                <label key={f.id}><input type="checkbox" checked={!off.has(f.id)} onChange={() => flip(f.id)} />{f.text}</label>
              ))}
            </div>
          )}
          {skills.length > 0 && (
            <div>
              <div className="sub-line">Skills you used - tap any you didn't</div>
              <div className="row">
                {skills.map(t => (
                  <button key={t.id} className={`chip-btn${off.has(t.id) ? " off" : ""}`} aria-pressed={!off.has(t.id)}
                          onClick={() => flip(t.id)}>{off.has(t.id) ? "" : "✓ "}{t.name}</button>
                ))}
              </div>
            </div>
          )}
          <div className="answers">
            <button className="primary" onClick={yes}><Icon name="check" />Yes, add it</button>
            <button onClick={() => setEditing(true)}><Icon name="edit" />Change</button>
            <button onClick={() => step("item", { id: e.id, state: "removed" })}>No</button>
          </div>
        </>
      )}
    </>
  );
}

function LineCard({ index, text, under, step }: { index: number; text: string; under: SetupItem; step: Step }) {
  return (
    <>
      <div className="ask">Add this line from your resume, word for word?</div>
      <div className="sub-line">About {nameOf(under)} - the AI couldn't use it the way it wrote it</div>
      <div className="big">{text}</div>
      <div className="answers">
        <button className="primary" onClick={() => step("restore", { index, add: true })}><Icon name="check" />Yes, add it</button>
        <button onClick={() => step("dismiss", { index })}>No</button>
      </div>
    </>
  );
}

/** One card at a time, with how many are left. "Skip for now" moves on
 * without answering; skipped cards come round again. */
export function CheckCards({ s, step }: { s: SetupState; step: Step }) {
  const [skipped, setSkipped] = useState<string[]>([]);
  const [done, setDone] = useState(0);
  const cards = deck(s);
  const names = sourceNames(s);
  const answer: Step = async (name, body) => {
    const next = await step(name, body);
    if (next) setDone(d => d + 1);
    return next;
  };
  if (!cards.length) {
    return <div className="card all-checked"><Icon name="check" /><span>Everything the AI found is checked.</span></div>;
  }
  const card = cards.find(c => !skipped.includes(c.key));
  return (
    <div className="deck">
      <div className="deck-top">
        <div className="progress" role="progressbar" aria-label="Checked" aria-valuemin={0} aria-valuemax={done + cards.length}
             aria-valuenow={done}><i style={{ width: `${Math.round((100 * done) / (done + cards.length))}%` }} /></div>
        <span className="small muted">{cards.length} left</span>
      </div>
      {card ? (
        <div className="card qcard" key={card.key}>
          {card.kind === "differ" ? <DifferCard it={card.it} under={card.under} step={answer} names={names} />
            : card.kind === "work" ? <WorkCard e={card.e} facts={card.facts} skills={card.skills} step={answer} names={names} />
            : card.kind === "line" ? <LineCard index={card.index} text={card.text} under={card.under} step={answer} />
            : <AskCard it={card.it} under={card.under} step={answer} names={names} />}
          <div className="skip"><button className="quiet" onClick={() => setSkipped([...skipped, card.key])}>Skip for now</button></div>
        </div>
      ) : (
        <div className="card qcard">
          <div className="ask">You skipped {cards.length === 1 ? "one" : `all ${cards.length}`}.</div>
          <div className="sub-line">They stay out of your profile until you answer them.</div>
          <div className="answers"><button className="primary" onClick={() => setSkipped([])}>Go through them again</button></div>
        </div>
      )}
    </div>
  );
}

/** What the person said no to, to ask again. */
export function SaidNo({ s, step }: { s: SetupState; step: Step }) {
  const entries = Object.fromEntries(s.items.filter(i => i.kind === "entry").map(e => [e.id, e]));
  const no = s.items.filter(i => i.state === "removed" && (!i.entry || entries[i.entry]?.state !== "removed"));
  if (!no.length) return null;
  const said = (i: SetupItem) => (i.kind === "entry" ? nameOf(i) : i.kind === "job" ? [i.title, i.employer].filter(Boolean).join(" at ")
    : String(i.text ?? i.name ?? i.claim ?? i.value ?? ""));
  return (
    <details className="said-no">
      <summary>You said no to {no.length}</summary>
      <div className="stack">
        {no.map(i => (
          <div key={i.id} className="spread">
            <span className="grow">{said(i)}</span>
            <button onClick={() => step("item", { id: i.id, state: "pending" })}>Ask me again</button>
          </div>
        ))}
      </div>
    </details>
  );
}
