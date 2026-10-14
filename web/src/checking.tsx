// Checking what the AI found, on the profile page: where each item came
// from, the versions to pick from where files disagree, editing an item in
// your own words, and the "not in your profile yet" box - everything left out
// of the profile, and why (docs/setup.md).

import { useState, type ReactNode } from "react";
import type { SetupItem, SetupState } from "./types";
import { Icon } from "./ui";

/** One setup step; the new state, or null when it failed (a toast says why). */
export type Step = (step: string, body?: unknown) => Promise<SetupState | null>;
export type Names = Record<string, string>;

export const IDENTITY: Record<string, string> = { name: "Name", location: "Location", email: "Email", phone: "Phone",
  linkedin: "LinkedIn", github: "GitHub", portfolio: "Portfolio" };
export const ENTRY_KINDS: [string, string][] = [["work", "Work"], ["project", "Personal project"],
  ["education", "Education"], ["certification", "Certification"], ["award", "Award"]];
const KIND_WORD: Record<string, string> = { job: "Job", fact: "Fact", tech: "Skill", metric: "Result", note: "Note",
  language: "Language" };

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

/** What an item says, in a line. */
export function said(it: SetupItem): string {
  switch (it.kind) {
    case "identity": case "language": return String(it.value ?? "");
    case "job": return [[it.title, it.employer].filter(Boolean).join(" at "), [it.start, it.end].filter(Boolean).join(" – ")]
      .filter(Boolean).join(" · ");
    case "entry": return [it.title || it.headline, it.where, it.period].filter(Boolean).join(" · ");
    case "tech": return String(it.name ?? "");
    case "metric": return String(it.claim ?? "");
    default: return String(it.text ?? "");
  }
}

function kindWord(it: SetupItem): string {
  if (it.kind === "identity") return IDENTITY[it.field || ""] || it.field || "";
  if (it.kind === "entry") return (ENTRY_KINDS.find(([k]) => k === it.type) || ["", "Work"])[1];
  return KIND_WORD[it.kind] || it.kind;
}

export function From({ it, names }: { it: SetupItem; names: Names }) {
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
    </div>
  );
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

/** One thing left out of the profile: what it is, where it came from, why
 * it's out, and what you can do about it. */
function Check({ it, step, names, why }: { it: SetupItem; step: Step; names: Names; why?: string }) {
  const [editing, setEditing] = useState(false);
  const add = (extra: object = {}) => step("item", { id: it.id, state: "confirmed", ...extra });
  return (
    <div className={`check${it.conflict ? " disputed" : ""}`}>
      <div><span className="kind">{kindWord(it)}</span>{said(it) || "(blank)"}</div>
      {why && <div className="small reason">{why}</div>}
      {it.conflict ? <Choices it={it} names={names} step={step} /> : <From it={it} names={names} />}
      {editing ? <EditItem it={it} step={step} add done={() => setEditing(false)} /> : (
        <div className="row">
          {!it.conflict && (it.kind === "metric" ? (
            <>
              <button className="primary" onClick={() => add({ fields: { measured: true } })}><Icon name="check" />Add · measured</button>
              <button onClick={() => add({ fields: { measured: false } })}>Add · my estimate</button>
            </>
          ) : <button className="primary" onClick={() => add()}><Icon name="check" />{it.state === "removed" ? "Put it back" : "Add"}</button>)}
          <button onClick={() => setEditing(true)}><Icon name="edit" />{it.conflict ? "Write my own" : "Edit"}</button>
          {it.state !== "removed" && <button className="quiet" onClick={() => step("item", { id: it.id, state: "removed" })}>Leave out</button>}
        </div>
      )}
    </div>
  );
}

function Why({ title, why, children }: { title: string; why: string; children: ReactNode }) {
  return (
    <div className="why-group">
      <div className="lbl">{title}</div>
      <div className="help">{why}</div>
      <div className="stack">{children}</div>
    </div>
  );
}

const LISTED = "Only in a skills list on your resume - no line of this work shows you using it. Add it only if you did.";

/** Everything the AI found that isn't in the profile, and why: not checked
 * yet, files that disagree, lines it couldn't match, files not read, answers
 * not used, and what you left out yourself. */
export function Missing({ s, step, names }: { s: SetupState; step: Step; names: Names }) {
  const entries = s.items.filter(i => i.kind === "entry");
  const entryOf = Object.fromEntries(entries.map(e => [e.id, e]));
  const live = (i: SetupItem) => !i.entry || entryOf[i.entry]?.state !== "removed";
  const differ = s.items.filter(i => i.conflict && i.state !== "removed" && live(i));
  const pending = s.items.filter(i => i.state === "pending" && !i.conflict && live(i));
  const left = s.items.filter(i => i.state === "removed" && live(i));
  const loose = pending.filter(i => !i.entry && i.kind !== "entry");
  const works = entries.filter(e => e.state !== "removed"
    && ((e.state === "pending" && !e.conflict) || pending.some(i => i.entry === e.id)));
  const unread = s.sources.filter(x => !x.drafted);
  const unused = s.interview.questions.filter(q => q.answer && !q.applied);
  const counts = [
    [pending.length, "not checked yet"], [differ.length, `where your files differ`],
    [s.dropped.length, "the AI couldn't match"], [unread.length, `file${unread.length === 1 ? "" : "s"} not read`],
  ].filter(([n]) => n) as [number, string][];

  if (!counts.length && !unused.length) {
    return (
      <div className="card missing done">
        <Icon name="check" /><span>Everything from your files is checked.{left.length > 0 && <> You left out {left.length} - see below.</>}</span>
        {left.length > 0 && <LeftOut left={left} step={step} names={names} />}
      </div>
    );
  }
  return (
    <section className="card missing">
      <div className="spread">
        <h3>Not in your profile yet</h3>
        <span className="small muted">{counts.map(([n, w]) => `${n} ${w}`).join(" · ")}</span>
      </div>
      <p className="small">Only what you add goes into your profile and your resumes. Here is everything that's out, and why.</p>

      {unread.length > 0 && (
        <Why title="Files not read yet" why="The AI hasn't read these, so nothing from them is here.">
          <div className="row">{unread.map(x => <span key={x.id} className="pill">{x.name}</span>)}
            <a href="#/setup">Read them in Setup</a></div>
        </Why>
      )}
      {unused.length > 0 && (
        <Why title="Answers not used yet" why={`You answered ${unused.length} question${unused.length === 1 ? "" : "s"}, but nothing was taken from ${unused.length === 1 ? "it" : "them"} yet.`}>
          <div><a href="#/setup">Use them in Setup</a></div>
        </Why>
      )}
      {differ.length > 0 && (
        <Why title="Your files say different things" why="It stays out until you pick the true version, or write it in your own words.">
          {differ.map(i => <Check key={`${i.id}-${i.state}`} it={i} step={step} names={names} />)}
        </Why>
      )}
      {loose.length > 0 && (
        <Why title="Found in your files, not checked yet" why="Add what's true. Leave out what isn't.">
          {loose.map(i => <Check key={`${i.id}-${i.state}`} it={i} step={step} names={names} />)}
        </Why>
      )}
      {works.map(e => <Work key={e.id} e={e} kids={pending.filter(i => i.entry === e.id)} step={step} names={names} />)}
      {s.dropped.length > 0 && (
        <Why title="The AI couldn't match these to your files"
             why="Everything the AI finds has to quote a line of what you added, word for word where it matters. These didn't, so they were kept out.">
          {s.dropped.map((d, n) => (
            <div key={n} className="check">
              <div>{d.what}</div>
              <div className="small reason">{d.why}{d.src && names[d.src] ? ` (${names[d.src]})` : ""}</div>
              {d.source && <div className="src"><q>{d.source}</q></div>}
              {d.entry && d.source && entryOf[d.entry] && (
                <div className="row"><button onClick={() => step("restore", { index: n })}>Use the line itself</button></div>
              )}
            </div>
          ))}
        </Why>
      )}
      {left.length > 0 && <LeftOut left={left} step={step} names={names} />}
    </section>
  );
}

/** A piece of work with what's still to check under it. */
function Work({ e, kids, step, names }: { e: SetupItem; kids: SetupItem[]; step: Step; names: Names }) {
  const [editing, setEditing] = useState(false);
  const sure = kids.filter(k => !k.listed_only && !(k.kind === "metric" && k.measured == null));
  const out = e.state === "pending" && !e.conflict;
  return (
    <div className="why-group work">
      <div className="lbl">{out ? "Not added yet: " : "Under "}{e.title || e.headline}</div>
      <div className="help">{e.conflict ? "Your files differ on this work itself - pick its version above. What's under it:"
        : out ? "This work isn't in your profile. Add it if it's true - what's listed under it goes in only if you add that too."
        : "This work is in your profile; these from your files aren't yet."}</div>
      {out && (
        <div className="check">
          <div><span className="kind">{kindWord(e)}</span>{said(e)}</div>
          <From it={e} names={names} />
          {editing ? <EditItem it={e} step={step} add done={() => setEditing(false)} /> : (
            <div className="row">
              {sure.length > 0 && <button className="primary" onClick={() => step("items", { ids: [e.id, ...sure.map(k => k.id)], state: "confirmed" })}>
                <Icon name="check" />Add it and the {sure.length} below</button>}
              <button className={sure.length ? "" : "primary"} onClick={() => step("item", { id: e.id, state: "confirmed" })}>
                {sure.length ? "Add just the work" : <><Icon name="check" />Add</>}</button>
              <button onClick={() => setEditing(true)}><Icon name="edit" />Edit</button>
              <button className="quiet" onClick={() => step("item", { id: e.id, state: "removed" })}>Leave it out</button>
            </div>
          )}
        </div>
      )}
      {!out && sure.length > 1 && (
        <div><button onClick={() => step("items", { ids: sure.map(k => k.id), state: "confirmed" })}>
          <Icon name="check" />{sure.length < kids.length ? `Add ${sure.length} at once - the rest need a choice` : `Add all ${sure.length}`}</button></div>
      )}
      <div className="stack kids">
        {kids.map(k => <Check key={`${k.id}-${k.state}`} it={k} step={step} names={names}
          why={k.listed_only ? LISTED : k.kind === "metric" ? "Say whether it was measured: only a measured result is ever printed as a number." : undefined} />)}
      </div>
    </div>
  );
}

function LeftOut({ left, step, names }: { left: SetupItem[]; step: Step; names: Names }) {
  return (
    <details className="left-out">
      <summary>You left out · {left.length}</summary>
      <p className="small muted">You said these aren't true, or not something to show. Put one back any time.</p>
      <div className="stack">{left.map(i => <Check key={`${i.id}-${i.state}`} it={i} step={step} names={names} />)}</div>
    </details>
  );
}
