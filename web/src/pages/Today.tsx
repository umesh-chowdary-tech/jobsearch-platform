import { useCallback, useEffect, useState } from "react";
import { get } from "../api";
import { JobRow, useJob } from "../jobs";
import type { Today as TodayData } from "../types";
import { Icon, type IconName } from "../ui";

/** When the person's day began, so "today" is theirs and not the server's UTC. */
export function startOfDay(now = new Date()): string {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
}

const FITS = [
  { key: "strong", label: "Strong fit" },
  { key: "possible", label: "Possible fit" },
  { key: "weak", label: "Weak fit" },
  { key: "unread", label: "Not read yet" },
] as const;

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
    get<TodayData>(`/api/today?since=${encodeURIComponent(startOfDay())}`)
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
        <p className="sub">{t.fetched_today.total} live posting{t.fetched_today.total === 1 ? "" : "s"} fetched today.</p>
      </div>
      <div className="fits" aria-label="Fetched today, by fit">
        {FITS.map(f => (
          <div key={f.key} className={`fit ${f.key}`}>
            <div className="n">{t.fetched_today[f.key]}</div>
            <div className="l">{f.label}</div>
          </div>
        ))}
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
        <h2>Fetched today, needs a decision · {t.decide.length}</h2>
        <a href="#/jobs">All jobs</a>
      </div>
      {t.decide.length
        ? (["strong", "possible"] as const).map(fit => {
            const rows = t.decide.filter(r => r.fit === fit);
            return rows.length > 0 && (
              <div key={fit}>
                <h3 className={`group fit-head ${fit}`}>{fit === "strong" ? "Strong fits" : "Possible fits"} · {rows.length}</h3>
                <div className="list">{rows.map(r => <JobRow key={r.id} row={r} actions onChange={load} />)}</div>
              </div>
            );
          })
        : t.caught_up === false
          ? <p className="empty">SwitchKaro is reading every job already open on the company boards it watches, against your
              profile. That takes a few minutes after you first save your profile; the matches show here, then ranked.</p>
          : <p className="empty">Nothing strong or possible came in today. New postings are checked every two hours.</p>}
      {t.older_waiting > 0 && (
        <p className="muted">
          {t.older_waiting} older {t.older_waiting === 1 ? "job still needs" : "jobs still need"} a decision:{" "}
          <a href="#/jobs">see them under Jobs</a>.
        </p>
      )}
      {t.unread > 0 && (
        <p className="muted">
          {t.unread} more {t.unread === 1 ? "posting" : "postings"} fetched today {t.unread === 1 ? "hasn't" : "haven't"} been read by a model yet.{" "}
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
