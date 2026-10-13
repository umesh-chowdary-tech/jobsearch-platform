import { useCallback, useEffect, useState } from "react";
import { get } from "../api";
import { JobRow, useJob } from "../jobs";
import type { Today as TodayData } from "../types";
import { Icon, type IconName } from "../ui";

function Kpi({ icon, tone, n, label }: { icon: IconName; tone?: string; n: number; label: string }) {
  return (
    <div className="kpi">
      <span className={`ic ${tone || ""}`}><Icon name={icon} /></span>
      <div><div className="n">{n}</div><div className="l">{label}</div></div>
    </div>
  );
}

export function Today({ onCount }: { onCount: (n: number) => void }) {
  const [t, setT] = useState<TodayData | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    get<TodayData>("/api/today")
      .then(d => { setT(d); onCount(d.decide.length + d.back_from_later.length); })
      .catch(e => setErr((e as Error).message));
  }, [onCount]);
  useEffect(load, [load]);
  const rank = useJob(load);

  if (err) return <p className="fail">{err}</p>;
  if (!t) return <p className="muted">Loading…</p>;
  const hour = new Date().getHours();
  const hello = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  const inProgress = t.in_progress.filter(r => ["applied", "interviewing", "offer"].includes(r.status)).length;

  return (
    <>
      <div className="hero">
        <div className="date">{new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}</div>
        <h1>{t.first_name ? `${hello}, ${t.first_name}` : hello}</h1>
        <p className="sub">{t.stages.new || 0} live postings you haven't decided on yet.</p>
      </div>
      <div className="kpis">
        <Kpi icon="list" n={t.decide.length} label="waiting on a decision" />
        <Kpi icon="target" n={t.decide.filter(r => r.fit === "strong").length} label="strong fits among them" />
        <Kpi icon="activity" tone="cool" n={inProgress} label="applications in progress" />
        <Kpi icon="star" tone="warm" n={t.favorites} label="favorites" />
      </div>

      {t.outreach_due.length > 0 && (
        <>
          <h2>Referral asks to act on · {t.outreach_due.length}</h2>
          <div className="list">
            {t.outreach_due.map(d => (
              <div key={d.id} className="item">
                <div className="t"><a href={`#/p/${encodeURIComponent(d.posting_id)}`}>{d.to} · {d.role} at {d.company}</a></div>
                <span className="badge possible">{d.need}</span>
              </div>
            ))}
          </div>
        </>
      )}

      {t.back_from_later.length > 0 && (
        <>
          <h2>Back from later · {t.back_from_later.length}</h2>
          <div className="list">{t.back_from_later.map(r => <JobRow key={r.id} row={r} actions onChange={load} />)}</div>
        </>
      )}

      <div className="h2row">
        <h2>Needs a decision · {t.decide.length}</h2>
        <a href="#/jobs">All jobs</a>
      </div>
      {t.decide.length
        ? <div className="list">{t.decide.map(r => <JobRow key={r.id} row={r} actions onChange={load} />)}</div>
        : t.caught_up === false
          ? <p className="empty">SwitchKaro is reading every job already open on the company boards it watches, against your
              profile. That takes a few minutes after you first save your profile; the matches show here, then ranked.</p>
          : <p className="empty">Nothing strong or possible is waiting on you. New postings are checked a few times a day;
            every job kept for you is under <a href="#/jobs">Jobs</a>.</p>}
      {t.unread > 0 && (
        <p className="muted">
          {t.unread} more kept postings haven't been read by a model yet.{" "}
          <button className="quiet" disabled={rank.running} onClick={() => rank.start("rank")}>
            {rank.running ? "Reading…" : "Have a model read them"}
          </button>
        </p>
      )}
      {rank.lines.length > 0 && <div className="console" aria-live="polite">{rank.lines.join("\n")}</div>}

      {t.in_progress.length > 0 && (
        <>
          <h2>In progress · {t.in_progress.length}</h2>
          <div className="list">{t.in_progress.map(r => <JobRow key={r.id} row={r} onChange={load} />)}</div>
        </>
      )}
    </>
  );
}
