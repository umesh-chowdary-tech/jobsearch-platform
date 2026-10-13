// The applied tracker: SwitchKaro's jobs by stage, every application however
// it was made (a pasted job email, or added by hand), and every referral ask.

import { useCallback, useEffect, useState } from "react";
import { get } from "../api";
import { AddForm, AppRow, PasteForm, type Tracked } from "../applications";
import { JobRow } from "../jobs";
import { AskRow } from "../referrals";
import type { OutreachDraft, Row } from "../types";
import { go, Icon } from "../ui";

const TABS = ["shortlisted", "applied", "interviewing", "offer", "closed", "skipped"];

export function Tracker({ query }: { query: URLSearchParams }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [asks, setAsks] = useState<OutreachDraft[] | null>(null);
  const [apps, setApps] = useState<Tracked | null>(null);
  const [form, setForm] = useState<"" | "paste" | "add">("");
  const [err, setErr] = useState("");
  const asked = query.get("stage") || "";
  const view = asked === "referrals" || asked === "all" ? asked : "stage";
  const stage = TABS.includes(asked) ? asked : "applied";
  const load = useCallback(() => {
    const fail = (e: unknown) => setErr((e as Error).message);
    get<Row[]>("/api/postings").then(setRows).catch(fail);
    get<OutreachDraft[]>("/api/outreach").then(setAsks).catch(fail);
    get<Tracked>("/api/applications").then(setApps).catch(fail);
  }, []);
  useEffect(load, [load]);

  if (err) return <p className="fail">{err}</p>;
  if (!rows || !asks || !apps) return <p className="muted">Loading…</p>;
  const kept = rows.filter(r => r.verdict === "keep");
  const count = (s: string) => kept.filter(r => r.status === s).length;
  const shown = kept.filter(r => r.status === stage);
  const due = asks.filter(a => a.need).length;
  const check = apps.apps.filter(a => a.look.length).length;
  const done = () => { setForm(""); load(); go("#/applied?stage=all"); };
  const tab = (key: string, label: string, n: number, extra = "") => {
    const on = key === "referrals" || key === "all" ? view === key : view === "stage" && stage === key;
    return (
      <button key={key} role="tab" aria-selected={on} className={on ? "on" : ""} onClick={() => go(`#/applied?stage=${key}`)}>
        {label} {n > 0 && <b>· {n}{extra}</b>}
      </button>
    );
  };

  return (
    <>
      <div className="spread">
        <div>
          <h1>Applied</h1>
          <p className="sub">Where every application stands, and who you've asked for a referral. You apply and send messages yourself; record them here.</p>
        </div>
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <button className={form === "paste" ? "primary" : ""} onClick={() => setForm(form === "paste" ? "" : "paste")}>
          <Icon name="upload" />Paste a job email
        </button>
        <button className={form === "add" ? "primary" : ""} onClick={() => setForm(form === "add" ? "" : "add")}>
          <Icon name="edit" />Add an application
        </button>
      </div>
      {form === "paste" && <div style={{ marginBottom: 16 }}><PasteForm kinds={apps.kinds} onDone={done} /></div>}
      {form === "add" && <div style={{ marginBottom: 16 }}><AddForm onDone={done} /></div>}

      <div className="tabs" role="tablist" aria-label="Stage">
        {TABS.map(s => tab(s, s, count(s)))}
        {tab("all", "all applications", apps.apps.length, check ? ` (${check} to check)` : "")}
        {tab("referrals", "referral asks", asks.length, due ? ` (${due} to act on)` : "")}
      </div>
      {view === "referrals" ? (
        asks.length
          ? <div className="list">{asks.map(d => <AskRow key={d.id} d={d} onChange={load} />)}</div>
          : <p className="empty">No referral asks yet. Open a job, build its resume, then use “Ask for a referral”.</p>
      ) : view === "all" ? (
        apps.apps.length
          ? <div className="list">{apps.apps.map(a => <AppRow key={a.id} a={a} statuses={apps.statuses} onChange={load} />)}</div>
          : <p className="empty">Nothing yet. Paste a job email - a receipt, an interview invite, a rejection - or add an
              application you made anywhere, and it's tracked here. Jobs you apply to from SwitchKaro move along with it.</p>
      ) : shown.length
        ? <div className="list">{shown.map(r => <JobRow key={r.id} row={r} stagePicker onChange={load} />)}</div>
        : <p className="empty">Nothing here yet.{stage === "applied" ? " Open a job and choose “Mark applied” once you've sent it." : ""}</p>}
    </>
  );
}
