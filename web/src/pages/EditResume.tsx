// Edit mode for the overall resume: what is on the page, with a control on
// each piece. Every change is saved at once and is undoable: hiding is
// undone by Unhide, a reword by "Put back", a move by moving it back. Nothing
// here deletes anything. A move under another project changes "Part of" on
// the Profile too, so both pages agree.

import { useState } from "react";
import type { OutlineItem, ResumeEdits, ResumeOutline } from "../types";
import { Icon } from "../ui";

type Edit = (body: Record<string, unknown>) => Promise<string | null>;   // null = done, else the error

export function EditResume({ outline, edits, edit }: { outline: ResumeOutline; edits: ResumeEdits; edit: Edit }) {
  const hidden = new Set(edits.hidden);
  return (
    <div className="stack edit-resume">
      {outline.jobs.map(job => (
        <div key={job.employer} className="stack">
          <div className="lbl">{job.employer}</div>
          {job.projects.map((p, i) => (
            <div key={p.id} className="card stack edit-project">
              <Item it={p} hidden={hidden.has(p.id)} edit={edit}
                    order={{ ids: job.projects.map(x => x.id), at: i }}
                    move={p.pieces.length ? undefined : { here: job.employer, outline, self: p.id, under: "" }} />
              {p.pieces.map((c, j) => (
                <div key={c.id} className="edit-piece">
                  <Item it={c} hidden={hidden.has(c.id)} edit={edit}
                        order={{ ids: p.pieces.map(x => x.id), at: j }}
                        move={{ here: job.employer, outline, self: c.id, under: p.id }} />
                </div>
              ))}
            </div>
          ))}
        </div>
      ))}
      {outline.side.length > 0 && (
        <div className="stack">
          <div className="lbl">Side projects</div>
          {outline.side.map(p => (
            <div key={p.id} className="card"><Item it={p} hidden={hidden.has(p.id)} edit={edit} /></div>
          ))}
        </div>
      )}
      {outline.left_out.length > 0 && (
        <div className="stack">
          <div className="lbl">Not on the page</div>
          <div className="help">Left out to fit your length and what you chose. "Always include" puts it back.</div>
          {outline.left_out.map(e => (
            <div key={e.id} className="row small">
              <span className="grow">{e.title}{e.where ? <span className="muted"> · {e.where}</span> : null}</span>
              <Toggle on={edits.pinned.includes(e.id)} label="Always include"
                      act={on => edit({ op: "pin", id: e.id, on })} />
            </div>
          ))}
        </div>
      )}
      {outline.hidden.length > 0 && (
        <div className="stack">
          <div className="lbl">Hidden from your resumes</div>
          <div className="help">Still on your Profile. Unhide puts it back.</div>
          {outline.hidden.map(e => (
            <div key={e.id} className="row small">
              <span className="grow">{e.title}</span>
              <Toggle on={hidden.has(e.id)} label="Hidden" offLabel="Hide again"
                      act={on => edit({ op: "hide", id: e.id, on })} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Item({ it, hidden, edit, order, move }: {
  it: OutlineItem; hidden: boolean; edit: Edit;
  order?: { ids: string[]; at: number };
  move?: { here: string; outline: ResumeOutline; self: string; under: string };
}) {
  const shift = (by: number) => {
    if (!order) return;
    const ids = [...order.ids];
    const j = order.at + by;
    if (j < 0 || j >= ids.length) return;
    [ids[order.at], ids[j]] = [ids[j], ids[order.at]];
    edit({ op: "order", ids });
  };
  // A piece goes under another project at the same job, or stands as one.
  const targets = move ? move.outline.projects.filter(p => p.where === move.here && p.id !== move.self) : [];
  return (
    <div className={`stack ${hidden ? "edit-hidden" : ""}`}>
      <div className="row">
        <b className="grow">{it.title}</b>
        {order && (
          <>
            <button className="quiet small" aria-label={`Move ${it.title} up`} disabled={order.at === 0} onClick={() => shift(-1)}>↑</button>
            <button className="quiet small" aria-label={`Move ${it.title} down`} disabled={order.at === order.ids.length - 1}
                    onClick={() => shift(1)}>↓</button>
          </>
        )}
        <Toggle on={it.pinned} label="Always include" act={on => edit({ op: "pin", id: it.id, on })} />
        <Toggle on={hidden} label="Hidden" offLabel="Hide" act={on => edit({ op: "hide", id: it.id, on })} />
      </div>
      {move && targets.length > 0 && (
        <label className="inline small">Part of
          <select value={move.under} style={{ width: "auto", marginLeft: 6 }} aria-label={`Project ${it.title} is part of`}
                  onChange={e => edit({ op: "move", id: it.id, part_of: e.target.value })}>
            <option value="">no project - it is one</option>
            {targets.map(p => <option key={p.id} value={p.id}>{p.title}</option>)}
          </select>
        </label>
      )}
      {hidden && <p className="small muted" style={{ margin: 0 }}>Hidden - it leaves the page when you rebuild.</p>}
      {it.bullets.length > 0 && (
        <ul className="edit-bullets">
          {it.bullets.map(b => <Bullet key={b.original} id={it.id} b={b} edit={edit} />)}
        </ul>
      )}
    </div>
  );
}

function Bullet({ id, b, edit }: { id: string; b: OutlineItem["bullets"][number]; edit: Edit }) {
  const [text, setText] = useState<string | null>(null);
  const [ask, setAsk] = useState("");             // the server asked: measured, or an estimate?
  const [err, setErr] = useState("");
  const save = async (measured?: boolean) => {
    const why = await edit({ op: "reword", id, original: b.original, text, ...(measured === undefined ? {} : { measured }) });
    if (why === null) { setText(null); setAsk(""); setErr(""); return; }
    if (/measured, or is it your estimate/.test(why)) { setAsk(why); setErr(""); } else { setErr(why); setAsk(""); }
  };
  if (text === null) {
    return (
      <li>
        <span>{b.text}</span>{" "}
        {b.reworded && <span className="pill" title={`Your Profile says: ${b.original}`}>your wording</span>}
        <button className="quiet small" aria-label="Reword this bullet" onClick={() => setText(b.text)}>
          <Icon name="edit" />
        </button>
        {b.reworded && (
          <button className="quiet small" onClick={() => edit({ op: "reword", id, original: b.original, text: "" })}>Put back</button>
        )}
      </li>
    );
  }
  return (
    <li className="stack">
      <textarea value={text} rows={2} onChange={e => setText(e.target.value)} aria-label="Your wording for this bullet" />
      <div className="help">On resumes only - your Profile keeps: "{b.original}"</div>
      {ask ? (
        <div className="row small">
          <span className="grow">{ask}</span>
          <button onClick={() => save(true)}>Measured</button>
          <button className="quiet" onClick={() => save(false)}>My estimate</button>
        </div>
      ) : (
        <div className="row">
          <button className="primary small" onClick={() => save()}>Save</button>
          <button className="quiet small" onClick={() => { setText(null); setErr(""); }}>Cancel</button>
        </div>
      )}
      {err && <p className="small fail" style={{ margin: 0 }}>{err}</p>}
    </li>
  );
}

function Toggle({ on, label, offLabel, act }: { on: boolean; label: string; offLabel?: string; act: (on: boolean) => void }) {
  return (
    <button className={`small ${on ? "chip confirmed" : "quiet"}`} aria-pressed={on} onClick={() => act(!on)}>
      {on ? label : (offLabel || label)}
    </button>
  );
}
