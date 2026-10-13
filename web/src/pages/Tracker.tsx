// The applied tracker: every posting you've acted on, by stage, and every
// referral ask.

import { useCallback, useEffect, useState } from "react";
import { get } from "../api";
import { JobRow } from "../jobs";
import { AskRow } from "../referrals";
import type { OutreachDraft, Row } from "../types";
import { go } from "../ui";

const TABS = ["shortlisted", "applied", "interviewing", "offer", "closed", "skipped"];

export function Tracker({ query }: { query: URLSearchParams }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [asks, setAsks] = useState<OutreachDraft[] | null>(null);
  const [err, setErr] = useState("");
  const referrals = query.get("stage") === "referrals";
  const stage = TABS.includes(query.get("stage") || "") ? query.get("stage")! : "applied";
  const load = useCallback(() => {
    get<Row[]>("/api/postings").then(setRows).catch(e => setErr((e as Error).message));
    get<OutreachDraft[]>("/api/outreach").then(setAsks).catch(e => setErr((e as Error).message));
  }, []);
  useEffect(load, [load]);

  if (err) return <p className="fail">{err}</p>;
  if (!rows || !asks) return <p className="muted">Loading…</p>;
  const kept = rows.filter(r => r.verdict === "keep");
  const count = (s: string) => kept.filter(r => r.status === s).length;
  const shown = kept.filter(r => r.status === stage);
  const due = asks.filter(a => a.need).length;

  return (
    <>
      <h1>Applied</h1>
      <p className="sub">Where each job you've acted on stands, and who you've asked for a referral. You apply and send messages yourself; mark them here.</p>
      <div className="tabs" role="tablist" aria-label="Stage">
        {TABS.map(s => (
          <button key={s} role="tab" aria-selected={!referrals && s === stage} className={!referrals && s === stage ? "on" : ""}
                  onClick={() => go(`#/applied?stage=${s}`)}>
            {s} {count(s) > 0 && <b>· {count(s)}</b>}
          </button>
        ))}
        <button role="tab" aria-selected={referrals} className={referrals ? "on" : ""} onClick={() => go("#/applied?stage=referrals")}>
          referral asks {asks.length > 0 && <b>· {asks.length}{due ? ` (${due} to act on)` : ""}</b>}
        </button>
      </div>
      {referrals ? (
        asks.length
          ? <div className="list">{asks.map(d => <AskRow key={d.id} d={d} onChange={load} />)}</div>
          : <p className="empty">No referral asks yet. Open a job, build its resume, then use “Ask for a referral”.</p>
      ) : shown.length
        ? <div className="list">{shown.map(r => <JobRow key={r.id} row={r} stagePicker onChange={load} />)}</div>
        : <p className="empty">Nothing here yet.{stage === "applied" ? " Open a job and choose “Mark applied” once you've sent it." : ""}</p>}
    </>
  );
}
