// Every job kept for you: search, filter, sort. The filters live in the
// address, so a reload or the back button keeps them.

import { useCallback, useEffect, useState } from "react";
import { get } from "../api";
import { AskCompany } from "../companies";
import { JobRow, STAGES } from "../jobs";
import type { Row } from "../types";
import { fresh, Icon } from "../ui";

const FITS: [string, string][] = [["any", "All"], ["strong", "Strong"], ["possible", "Possible"],
                                  ["weak", "Weak"], ["unread", "Not read yet"]];
const STAGE_CHOICES: [string, string][] = [["open", "Still open"], ["any", "Any stage"],
  ...STAGES.map(s => [s, s[0].toUpperCase() + s.slice(1)] as [string, string])];
const PAGE = 50;

interface Filters {
  fit: string; stage: string; q: string; fav: boolean; later: boolean; fresh: boolean; closed: boolean;
  sort: "match" | "newest";
}

function fromQuery(q: URLSearchParams): Filters {
  return { fit: q.get("fit") || "any", stage: q.get("stage") || "open", q: q.get("q") || "",
           fav: q.get("fav") === "1", later: q.get("later") === "1", fresh: q.get("new") === "1",
           closed: q.get("closed") === "1", sort: q.get("sort") === "newest" ? "newest" : "match" };
}

function toHash(f: Filters): string {
  const p = new URLSearchParams();
  if (f.fit !== "any") p.set("fit", f.fit);
  if (f.stage !== "open") p.set("stage", f.stage);
  if (f.q.trim()) p.set("q", f.q.trim());
  if (f.fav) p.set("fav", "1");
  if (f.later) p.set("later", "1");
  if (f.fresh) p.set("new", "1");
  if (f.closed) p.set("closed", "1");
  if (f.sort !== "match") p.set("sort", f.sort);
  const s = p.toString();
  return `#/jobs${s ? `?${s}` : ""}`;
}

const fitOf = (r: Row) => r.fit || "unread";

/** Every filter but fit, so each fit chip can say how many it would show. */
function passes(r: Row, f: Filters, words: string[]): boolean {
  if (!f.closed && r.closed) return false;
  if (f.stage === "open" ? ["closed", "skipped"].includes(r.status) : f.stage !== "any" && r.status !== f.stage) return false;
  if (f.fav && !r.favorite) return false;
  if (f.later && !r.later) return false;
  if (f.fresh && !fresh(r.first_seen)) return false;
  if (words.length) {
    const text = `${r.title} ${r.company} ${r.location} ${r.skills.join(" ")}`.toLowerCase();
    if (!words.every(w => text.includes(w))) return false;
  }
  return true;
}

export function Jobs({ query }: { query: URLSearchParams }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [err, setErr] = useState("");
  const [f, setF] = useState<Filters>(() => fromQuery(query));
  const [shown, setShown] = useState(PAGE);
  const load = useCallback(() => {
    get<Row[]>("/api/postings").then(setRows).catch(e => setErr((e as Error).message));
  }, []);
  useEffect(load, [load]);
  // Into the address without a navigation, so typing never jumps the page.
  useEffect(() => { window.history.replaceState(null, "", toHash(f)); setShown(PAGE); }, [f]);

  if (err) return <p className="fail">{err}</p>;
  if (!rows) return <p className="muted">Loading…</p>;
  const set = (patch: Partial<Filters>) => setF(prev => ({ ...prev, ...patch }));
  const clear = () => setF(fromQuery(new URLSearchParams()));
  const kept = rows.filter(r => r.verdict === "keep");
  const words = f.q.toLowerCase().split(/\s+/).filter(Boolean);
  const base = kept.filter(r => passes(r, f, words));
  const count = (fit: string) => (fit === "any" ? base : base.filter(r => fitOf(r) === fit)).length;
  let list = f.fit === "any" ? base : base.filter(r => fitOf(r) === f.fit);
  if (f.sort === "newest") list = [...list].sort((a, b) => String(b.first_seen || "").localeCompare(String(a.first_seen || "")));
  const recent = kept.filter(r => fresh(r.first_seen) && !r.closed).length;

  if (!kept.length) {
    return (
      <>
        <h1>Jobs</h1>
        <div className="card stack">
          <p><b>No jobs kept for you yet.</b></p>
          <p className="small muted">Company job boards are checked a few times a day. A job is kept for you when it's in a city
            you chose (or remote), at your level, and asks for skills your profile backs. If nothing shows up within a day,
            check your <a href="#/profile?tab=prefs">preferences</a>.</p>
        </div>
        <h2>Ask for a company</h2>
        <AskCompany />
      </>
    );
  }

  return (
    <>
      <h1>Jobs</h1>
      <p className="sub">Every job kept for you: {kept.length} in all{recent ? `, ${recent} new in the last 3 days` : ""}.</p>
      <div className="filters">
        <div className="search">
          <Icon name="search" />
          <input type="search" value={f.q} placeholder="Search title, company, city or skill" aria-label="Search jobs"
                 onChange={e => set({ q: e.target.value })} />
        </div>
        <div className="chips" role="group" aria-label="Fit">
          {FITS.map(([v, l]) => (
            <button key={v} className={f.fit === v ? "on" : ""} aria-pressed={f.fit === v} onClick={() => set({ fit: v })}>
              {l} <span className="n">{count(v)}</span>
            </button>
          ))}
        </div>
        <div className="row">
          <select aria-label="Stage" value={f.stage} onChange={e => set({ stage: e.target.value })}>
            {STAGE_CHOICES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select aria-label="Order" value={f.sort} onChange={e => set({ sort: e.target.value as Filters["sort"] })}>
            <option value="match">Best match first</option>
            <option value="newest">Newest first</option>
          </select>
          <label className="inline"><input type="checkbox" checked={f.fav} onChange={e => set({ fav: e.target.checked })} />Favorites</label>
          <label className="inline"><input type="checkbox" checked={f.fresh} onChange={e => set({ fresh: e.target.checked })} />New</label>
          <label className="inline"><input type="checkbox" checked={f.later} onChange={e => set({ later: e.target.checked })} />Saved for later</label>
          <label className="inline"><input type="checkbox" checked={f.closed} onChange={e => set({ closed: e.target.checked })} />Closed on the board</label>
          {toHash(f) !== "#/jobs" && <button className="quiet" onClick={clear}>Clear filters</button>}
        </div>
      </div>
      {list.length ? (
        <>
          <p className="muted small" aria-live="polite">
            {list.length > shown ? `Showing ${shown} of ${list.length}` : `${list.length} ${list.length === 1 ? "job" : "jobs"}`}
          </p>
          <div className="list">
            {list.slice(0, shown).map(r => <JobRow key={r.id} row={r} actions={r.status === "new"} onChange={load} />)}
          </div>
          {list.length > shown && (
            <button style={{ marginTop: 12 }} onClick={() => setShown(shown + PAGE)}>
              Show {Math.min(PAGE, list.length - shown)} more
            </button>
          )}
        </>
      ) : (
        <p className="empty">Nothing matches these filters. <button className="quiet" onClick={clear}>Clear filters</button></p>
      )}
      <h2>Ask for a company</h2>
      <AskCompany />
    </>
  );
}
