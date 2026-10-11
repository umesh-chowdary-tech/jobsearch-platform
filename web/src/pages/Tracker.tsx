// The applied tracker: every posting you've acted on, by stage.

import { useCallback, useEffect, useState } from "react";
import { get } from "../api";
import { JobRow } from "../jobs";
import type { Row } from "../types";
import { go } from "../ui";

const TABS = ["shortlisted", "applied", "interviewing", "offer", "closed", "skipped"];

export function Tracker({ query }: { query: URLSearchParams }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [err, setErr] = useState("");
  const stage = TABS.includes(query.get("stage") || "") ? query.get("stage")! : "applied";
  const load = useCallback(() => {
    get<Row[]>("/api/postings").then(setRows).catch(e => setErr((e as Error).message));
  }, []);
  useEffect(load, [load]);

  if (err) return <p className="fail">{err}</p>;
  if (!rows) return <p className="muted">Loading…</p>;
  const kept = rows.filter(r => r.verdict === "keep");
  const count = (s: string) => kept.filter(r => r.status === s).length;
  const shown = kept.filter(r => r.status === stage);

  return (
    <>
      <h1>Applied</h1>
      <p className="sub">Where each job you've acted on stands. You apply on the company's site; mark it here.</p>
      <div className="tabs" role="tablist" aria-label="Stage">
        {TABS.map(s => (
          <button key={s} role="tab" aria-selected={s === stage} className={s === stage ? "on" : ""}
                  onClick={() => go(`#/applied?stage=${s}`)}>
            {s} {count(s) > 0 && <b>· {count(s)}</b>}
          </button>
        ))}
      </div>
      {shown.length
        ? <div className="list">{shown.map(r => <JobRow key={r.id} row={r} stagePicker onChange={load} />)}</div>
        : <p className="empty">Nothing here yet.{stage === "applied" ? " Open a job and choose “Mark applied” once you've sent it." : ""}</p>}
    </>
  );
}
