// The admin page: how many use the service and how far they get, what the AI
// costs, whether the job updates run - and the switches. Counts and emails
// only: nobody's resume, profile or jobs are shown here. Every change goes to
// the log tab.

import { REQUEST_WORD } from "../companies";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { get, post } from "../api";
import type { AdminOverview, AdminPerson } from "../types";
import { ago, day, Icon, useToast, type IconName } from "../ui";

type Tab = "overview" | "people" | "ai" | "jobs" | "log";
const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" }, { id: "people", label: "People" }, { id: "ai", label: "AI & budget" },
  { id: "jobs", label: "Jobs & health" }, { id: "log", label: "Log" },
];
const JOB_NAMES: Record<string, string> = {
  rank: "Ranking jobs", resume: "Writing resumes", setup: "Reading resumes", outreach: "Message openers",
  mail: "Inbox", other: "Other",
};

const usd = (n: number | null | undefined) =>
  n == null ? "—" : `$${n < 0.1 && n > 0 ? n.toFixed(3) : n.toFixed(2)}`;
const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0);

type Act = (body: Record<string, unknown>, done?: string) => Promise<boolean>;

export function Admin() {
  const [o, setO] = useState<AdminOverview | null>(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<Tab>("overview");
  const toast = useToast();
  const load = useCallback(() => {
    get<AdminOverview>("/api/admin/overview").then(setO).catch(e => setErr((e as Error).message));
  }, []);
  useEffect(load, [load]);

  const act: Act = async (body, done) => {
    try {
      const r = await post<{ overview: AdminOverview }>("/api/admin/do", body);
      setO(r.overview);
      if (done) toast(done);
      return true;
    } catch (e) { toast((e as Error).message); return false; }
  };

  if (err) return <p className="fail">{err}</p>;
  if (!o) return <p className="muted">Loading…</p>;
  const s = o.settings;
  return (
    <>
      <div className="spread">
        <div>
          <h1>Admin</h1>
          <p className="sub">{o.people.length} {o.people.length === 1 ? "person" : "people"} · {usd(o.ai.month_usd)} of AI
            this month · {o.invites_used} invites used</p>
        </div>
        <button className="quiet" onClick={load}>Refresh</button>
      </div>

      {s.ai_paused && <Notice text="AI is paused for everyone. Ranking, resumes and setup wait until you resume it."
                             action={<button onClick={() => act({ action: "ai_resume" }, "AI resumed")}>Resume AI</button>} />}
      {o.health.cycle_paused && <Notice text="Scheduled job updates are paused."
                                       action={<button onClick={() => act({ action: "cycle_resume" }, "Job updates resumed")}>Resume</button>} />}
      {!!o.missing_settings?.length && (
        <Notice text={<>The <a href="#/privacy">privacy notice</a> has blanks. Set these in the service's settings
          (on Render: Environment), then restart it: <b>{o.missing_settings.join(", ")}</b>.</>} />
      )}

      <div className="tabs plain" role="tablist" aria-label="Admin sections">
        {TABS.map(t => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "on" : ""}
                  onClick={() => setTab(t.id)}>{t.label}</button>
        ))}
      </div>

      {tab === "overview" && <OverviewTab o={o} />}
      {tab === "people" && <PeopleTab o={o} act={act} />}
      {tab === "ai" && <AiTab o={o} act={act} />}
      {tab === "jobs" && <JobsTab o={o} act={act} />}
      {tab === "log" && <LogTab o={o} />}
    </>
  );
}

function Notice({ text, action }: { text: ReactNode; action?: ReactNode }) {
  return <div className="notice"><span>{text}</span>{action}</div>;
}

function Kpi({ icon, tone, n, label }: { icon: IconName; tone?: string; n: ReactNode; label: string }) {
  return (
    <div className="kpi">
      <span className={`ic ${tone || ""}`}><Icon name={icon} /></span>
      <div><div className="n">{n}</div><div className="l">{label}</div></div>
    </div>
  );
}

/** A bar for each day or week, with an optional cap drawn across. */
function Bars({ data, format, cap, label }: {
  data: { label: string; value: number; bad?: boolean }[]; format: (n: number) => string; cap?: number | null; label: string;
}) {
  const max = Math.max(cap || 0, ...data.map(d => d.value)) || 1;
  const w = 600, h = 120, top = 6;
  const bw = w / Math.max(data.length, 1);
  const y = (v: number) => h - (v / max) * (h - top);
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" role="img" aria-label={label}>
        {data.map((d, i) => (
          <rect key={i} x={i * bw + 1} y={y(d.value)} width={Math.max(bw - 2, 1)} height={h - y(d.value)}
                className={d.bad ? "bad" : ""}><title>{`${d.label}: ${format(d.value)}`}</title></rect>
        ))}
        {cap ? <line x1="0" x2={w} y1={y(cap)} y2={y(cap)} className="cap" /> : null}
      </svg>
      <figcaption className="small muted spread">
        <span>{data[0]?.label}</span>
        <span>{cap ? `dashed: ${format(cap)} cap · ` : ""}top {format(Math.max(...data.map(d => d.value), 0))}</span>
        <span>{data[data.length - 1]?.label}</span>
      </figcaption>
    </figure>
  );
}

function OverviewTab({ o }: { o: AdminOverview }) {
  const top = o.funnel[0]?.n || 0;
  const failedRuns = o.jobs.runs ? `${o.jobs.failed} of ${o.jobs.runs}` : "none yet";
  return (
    <>
      <div className="kpis">
        <Kpi icon="list" n={o.people.length} label="people joined" />
        <Kpi icon="activity" tone="cool" n={o.active.week} label={`active this week (${o.active.month} this month)`} />
        <Kpi icon="target" n={usd(o.ai.month_usd)} label={`AI this month, of ${usd(o.ai.budget_usd)}`} />
        <Kpi icon="clock" tone={o.jobs.failed ? "warm" : "cool"} n={failedRuns} label="job updates failed, 30 days" />
      </div>

      <h2>How far people get</h2>
      <div className="card">
        <div className="funnel">
          {o.funnel.map(f => (
            <div key={f.step} className="fstep">
              <span>{f.step}</span>
              <span className="bar"><i style={{ width: `${pct(f.n, top)}%` }} /></span>
              <b>{f.n}</b>
            </div>
          ))}
        </div>
        <p className="small muted">“Good match?” answers: {o.totals.answers ? `${o.totals.good} of ${o.totals.answers} said yes (${pct(o.totals.good, o.totals.answers)}%)` : "none yet"}.
          Across everyone: {o.totals.ranked} jobs ranked, {o.totals.strong} strong, {o.totals.resumes} resumes, {o.totals.applied} applications.</p>
      </div>

      <div className="grid2">
        <div>
          <h2>AI spend per day</h2>
          <div className="card">
            <Bars label="AI spend per day, last 30 days" format={usd} cap={o.ai.day_cap_usd}
                  data={o.ai.days.map(d => ({ label: day(d.day), value: d.usd }))} />
            <p className="small muted">Month so far {usd(o.ai.month_usd)} · on this pace {usd(o.ai.forecast_usd)} by month end
              · budget {usd(o.ai.budget_usd)}</p>
          </div>
        </div>
        <div>
          <h2>People joining per week</h2>
          <div className="card">
            <Bars label="People joining per week, last 8 weeks" format={n => String(n)}
                  data={o.joins.map(w => ({ label: day(w.week), value: w.n }))} />
          </div>
        </div>
      </div>
    </>
  );
}

function PeopleTab({ o, act }: { o: AdminOverview; act: Act }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [n, setN] = useState(5);
  const [note, setNote] = useState("");
  const [made, setMade] = useState<string[]>([]);
  const toast = useToast();
  const shown = useMemo(() => o.people.filter(p => p.email.toLowerCase().includes(q.trim().toLowerCase())), [o.people, q]);

  const make = async () => {
    try { setMade((await post<{ codes: string[] }>("/api/admin/invites", { n, note })).codes); setNote(""); }
    catch (e) { toast((e as Error).message); }
  };

  return (
    <>
      <div className="card scroll-x">
        <div className="spread">
          <input type="search" placeholder="Search by email" value={q} onChange={e => setQ(e.target.value)}
                 aria-label="Search people by email" style={{ maxWidth: 280 }} />
          <span className="small muted">{shown.length} shown</span>
        </div>
        <table>
          <thead><tr><th>Email</th><th>Joined</th><th>Last active</th><th>Jobs ranked</th><th>Resumes</th>
            <th>Applied</th><th>AI this month</th><th>Status</th><th /></tr></thead>
          <tbody>
            {shown.map(p => (
              <PersonRow key={p.profile} p={p} open={open === p.profile} act={act}
                         onToggle={() => setOpen(open === p.profile ? null : p.profile)} defaultCap={o.settings.caps.person_usd_per_day}
                         ceiling={o.settings.ceiling.person_usd_per_day} />
            ))}
          </tbody>
        </table>
        {!o.people.length && <p className="empty">Nobody has joined yet.</p>}
      </div>

      <h2>Invite codes</h2>
      <div className="card stack">
        <div className="row">
          <label className="field" style={{ width: 90 }}>How many
            <input type="number" min={1} max={100} value={n} onChange={e => setN(Number(e.target.value))} /></label>
          <label className="field" style={{ flex: 1, minWidth: 200 }}>For (only you see this)
            <input value={note} onChange={e => setNote(e.target.value)} placeholder="College friends" /></label>
        </div>
        <div><button className="primary" onClick={make}>Make codes</button></div>
        {made.length > 0 && <pre className="box">{made.join("\n")}</pre>}
        {o.invites_unused.length > 0 && (
          <details><summary>{o.invites_unused.length} unused</summary>
            <table><tbody>
              {o.invites_unused.map(i => (
                <tr key={i.code}><td><code>{i.code}</code></td><td>{i.note}</td><td>{day(i.created)}</td>
                  <td><button className="quiet" onClick={() => act({ action: "revoke_invite", code: i.code }, "Code cancelled")}>Cancel</button></td></tr>
              ))}
            </tbody></table>
          </details>
        )}
        {o.invites_used_by.length > 0 && (
          <details><summary>{o.invites_used_by.length} used</summary>
            <table><tbody>
              {o.invites_used_by.map(i => (
                <tr key={i.code}><td><code>{i.code}</code></td><td>{i.email}</td><td>{i.used_at ? day(i.used_at) : ""}</td></tr>
              ))}
            </tbody></table>
          </details>
        )}
      </div>
    </>
  );
}

function PersonRow({ p, open, onToggle, act, defaultCap, ceiling }: {
  p: AdminPerson; open: boolean; onToggle: () => void; act: Act; defaultCap: number | null; ceiling: number;
}) {
  const [cap, setCap] = useState(String(p.own_usd_per_day ?? ""));
  const [confirm, setConfirm] = useState("");
  const c = p.counts;
  const status = p.suspended ? <span className="pill g">paused</span>
    : p.profile_ready ? <span className="pill c">set up</span> : <span className="pill">not set up</span>;
  return (
    <>
      <tr>
        <td>{p.email}</td>
        <td>{p.joined ? day(p.joined) : ""}</td>
        <td>{p.last_seen ? day(p.last_seen) : "—"}</td>
        <td>{c ? `${c.ranked} (${c.strong} strong)` : "—"}</td>
        <td>{c ? c.resumes : "—"}</td>
        <td>{c ? c.applied : "—"}</td>
        <td>{usd(p.month.usd)}{p.own_usd_per_day != null && <span className="small muted"> · own cap {usd(p.own_usd_per_day)}/day</span>}</td>
        <td>{status}</td>
        <td><button className="quiet" aria-expanded={open} onClick={onToggle}>{open ? "Close" : "Manage"}</button></td>
      </tr>
      {open && (
        <tr className="manage"><td colSpan={9}>
          <div className="row">
            {p.suspended
              ? <button onClick={() => act({ action: "unsuspend", profile: p.profile }, "Account resumed")}>Resume account</button>
              : <button onClick={() => act({ action: "suspend", profile: p.profile }, "Account paused")}>Pause account</button>}
            <span className="small muted">A paused person can still download or delete their data.</span>
          </div>
          <div className="row">
            <label className="field" style={{ width: 230 }}>Their AI limit a day (USD, max {ceiling})
              <input type="number" min={0} max={ceiling} step="0.05" value={cap} placeholder={`default ${usd(defaultCap)}`}
                     onChange={e => setCap(e.target.value)} /></label>
            <button onClick={() => act({ action: "person_limit", profile: p.profile, usd_per_day: Number(cap) }, "Limit saved")}
                    disabled={cap === ""}>Save</button>
            {p.own_usd_per_day != null && (
              <button className="quiet" onClick={async () => { if (await act({ action: "person_limit", profile: p.profile, usd_per_day: null }, "Back to the default")) setCap(""); }}>
                Use the default</button>
            )}
          </div>
          <div className="row">
            <label className="field" style={{ width: 230 }}>Type “delete” to delete this account
              <input value={confirm} onChange={e => setConfirm(e.target.value)} /></label>
            <button className="danger" disabled={confirm.trim().toLowerCase() !== "delete"}
                    onClick={() => act({ action: "delete", profile: p.profile, confirm }, "Account and its data deleted")}>
              Delete account and data</button>
          </div>
        </td></tr>
      )}
    </>
  );
}

function AiTab({ o, act }: { o: AdminOverview; act: Act }) {
  const s = o.settings;
  const [caps, setCaps] = useState({
    usd_per_month: String(s.caps.usd_per_month ?? ""), usd_per_day: String(s.caps.usd_per_day ?? ""),
    person_usd_per_day: String(s.caps.person_usd_per_day ?? ""),
    person_usd_per_month: String(s.caps.person_usd_per_month ?? ""),
  });
  const g = o.ai.groq_today;
  const saveCaps = () => act({
    action: "caps", usd_per_month: Number(caps.usd_per_month), usd_per_day: Number(caps.usd_per_day),
    person_usd_per_day: Number(caps.person_usd_per_day), person_usd_per_month: Number(caps.person_usd_per_month),
  }, "Limits saved");
  const field = (key: keyof typeof caps, label: string, max: number) => (
    <label className="field">{label} (max {max})
      <input type="number" min={0} max={max} step="0.05" value={caps[key]}
             onChange={e => setCaps({ ...caps, [key]: e.target.value })} /></label>
  );

  return (
    <>
      <div className="kpis">
        <Kpi icon="target" n={usd(o.ai.month_usd)} label={`this month, of ${usd(o.ai.budget_usd)}`} />
        <Kpi icon="activity" tone="cool" n={usd(o.ai.forecast_usd)} label="by month end, at this pace" />
        <Kpi icon="sun" tone="cool" n={usd(o.ai.today_usd)} label={`today, of ${usd(o.ai.day_cap_usd)}`} />
        <Kpi icon="list" tone="cool" n={`${g.requests} / ${g.requests_cap ?? "—"}`} label="Groq requests today (free tier)" />
      </div>

      <h2>Which model does each job</h2>
      <div className="card stack">
        {s.routes.map(r => (
          <label key={r.job} className="field route">{r.label}
            <select value={r.current} onChange={e => act({ action: "route", job: r.job, choice: e.target.value }, `${r.label}: model changed`)}>
              {!r.choices.some(c => c.id === r.current) && <option value={r.current}>{r.current}</option>}
              {r.choices.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
          </label>
        ))}
        <p className="small muted">Ranking: Groq's free tier reads new postings first; the model chosen here re-reads the
          promising ones. Reading an uploaded resume stays with Claude, as the privacy notice says.</p>
      </div>

      <h2>Spending limits (Claude)</h2>
      <div className="card stack">
        <div className="fields">
          {field("usd_per_month", "Everyone, a month (USD)", s.ceiling.usd_per_month)}
          {field("usd_per_day", "Everyone, a day (USD)", s.ceiling.usd_per_day)}
          {field("person_usd_per_day", "Each person, a day (USD)", s.ceiling.person_usd_per_day)}
          {field("person_usd_per_month", "Each person, a month (USD)", s.ceiling.person_usd_per_month)}
        </div>
        <div className="row">
          <button className="primary" onClick={saveCaps}>Save limits</button>
          {s.ai_paused
            ? <button onClick={() => act({ action: "ai_resume" }, "AI resumed")}>Resume AI</button>
            : <button className="danger" onClick={() => act({ action: "ai_pause" }, "AI paused for everyone")}>Pause all AI</button>}
        </div>
        <p className="small muted">When a limit is reached, the work waits until the next day; it never moves to another
          model. Also set a monthly limit in the Anthropic console as a backstop.</p>
      </div>

      <div className="grid2">
        <div>
          <h2>This month, by job</h2>
          <div className="card scroll-x">
            <table>
              <thead><tr><th>Job</th><th>Tokens</th><th>Cost</th></tr></thead>
              <tbody>{o.ai.by_job.map(r => <tr key={r.job}><td>{JOB_NAMES[r.job] || r.job}</td><td>{r.tokens.toLocaleString()}</td><td>{usd(r.usd)}</td></tr>)}</tbody>
            </table>
            {!o.ai.by_job.length && <p className="empty">No AI requests this month.</p>}
          </div>
        </div>
        <div>
          <h2>This month, by model</h2>
          <div className="card scroll-x">
            <table>
              <thead><tr><th>Model</th><th>Requests</th><th>Cost</th></tr></thead>
              <tbody>{o.ai.by_model.map(r => <tr key={r.model}><td>{r.model}</td><td>{r.requests}</td><td>{usd(r.usd)}</td></tr>)}</tbody>
            </table>
            {!o.ai.by_model.length && <p className="empty">No AI requests this month.</p>}
          </div>
        </div>
      </div>

      <h2>This month, by person</h2>
      <div className="card scroll-x">
        <table>
          <thead><tr><th>Email</th><th>Requests</th><th>Cost</th></tr></thead>
          <tbody>{[...o.people].sort((a, b) => b.month.usd - a.month.usd).map(p => (
            <tr key={p.profile}><td>{p.email}</td><td>{p.month.requests}</td><td>{usd(p.month.usd)}</td></tr>
          ))}</tbody>
        </table>
      </div>
    </>
  );
}

function JobsTab({ o, act }: { o: AdminOverview; act: Act }) {
  const h = o.health, j = o.jobs;
  const db = h.database;
  const checks: { ok: boolean; text: ReactNode }[] = [
    { ok: !!h.keys.claude, text: h.keys.claude ? "Claude key is set" : "Claude key is missing (ANTHROPIC_API_KEY)" },
    { ok: !!h.keys.groq, text: h.keys.groq ? "Groq key is set" : "Groq key is missing (GROQ_API_KEY)" },
    { ok: !!db && !db.last_error, text: !db ? "No database: everything is lost when the service restarts (DATABASE_URL)"
        : db.last_error ? `Saving to the database failed ${ago(db.last_error.at)} (${db.last_error.kind})`
        : `Saved to the database ${db.last_ok ? ago(db.last_ok) : "—"}` },
    { ok: !o.missing_settings?.length, text: o.missing_settings?.length ? `Settings missing: ${o.missing_settings.join(", ")}` : "Privacy notice settings are filled in" },
    { ok: !!j.boards.total && !j.boards.stale_count, text: j.boards.total ? `${j.boards.total} job boards, ${j.boards.live_postings} open postings`
        + (j.boards.stale_count ? `; ${j.boards.stale_count} not read for 2 days` : "")
        : "No job boards yet - press “Check job boards” once, so job updates have boards to watch" },
  ];
  const newTotal = j.days.reduce((a, d) => a + d.new, 0);
  return (
    <>
      <div className="kpis">
        <Kpi icon="clock" tone="cool" n={j.runs} label="job updates, 30 days" />
        <Kpi icon="activity" tone={j.failed ? "warm" : "cool"} n={j.failed} label="of them failed" />
        <Kpi icon="list" n={newTotal} label="new postings found, 30 days" />
        <Kpi icon="sun" tone="cool" n={j.avg_took != null ? `${j.avg_took}s` : "—"} label="average time an update takes" />
      </div>

      <h2>Job updates</h2>
      <div className="card stack">
        <div className="row">
          <button className="primary" disabled={h.running} onClick={() => act({ action: "cycle_run" }, "Job update started")}>
            {h.running ? "Running…" : "Run now"}</button>
          {h.cycle_paused
            ? <button onClick={() => act({ action: "cycle_resume" }, "Scheduled updates resumed")}>Resume schedule</button>
            : <button onClick={() => act({ action: "cycle_pause" }, "Scheduled updates paused")}>Pause schedule</button>}
          <button disabled={h.running} title="Check every company board in the registry and keep the ones that answer"
                  onClick={() => act({ action: "boards_check" }, "Checking job boards - this takes a few minutes")}>
            Check job boards</button>
          {h.busy && <span className="small muted">Busy: {h.busy.what}{h.busy.since ? `, ${ago(h.busy.since)}` : ""}</span>}
        </div>
        {j.last && <p className={`small ${j.last.failed ? "fail" : "muted"}`}>Last run {ago(j.last.at)}: {j.last.text}</p>}
        <Bars label="New postings found per day, last 30 days" format={n => String(n)}
              data={j.days.map(d => ({ label: day(d.day), value: d.new, bad: d.failed > 0 }))} />
        <p className="small muted">Red: a day with a failed update.</p>
      </div>

      <Requests o={o} act={act} />

      <h2>Health</h2>
      <div className="card">
        <ul className="checks">
          {checks.map((c, i) => <li key={i} className={c.ok ? "ok" : "bad"}><Icon name={c.ok ? "check" : "clock"} />{c.text}</li>)}
        </ul>
        {j.boards.stale.length > 0 && (
          <details><summary>Boards not read for 2 days</summary>
            <ul className="small">{j.boards.stale.map(b => <li key={b.name}>{b.name} — last read {b.last_ok ? ago(b.last_ok) : "never"}</li>)}</ul>
          </details>
        )}
      </div>

      <h2>Recent errors</h2>
      <div className="card scroll-x">
        {h.errors.length ? (
          <table><tbody>{h.errors.map((e, i) => <tr key={i}><td>{ago(e.at)}</td><td className="small muted">{e.where}</td><td>{e.what}</td></tr>)}</tbody></table>
        ) : <p className="empty">None since the service started.</p>}
      </div>
    </>
  );
}

function Requests({ o, act }: { o: AdminOverview; act: Act }) {
  const reqs = o.requests || [];
  const waiting = reqs.filter(r => r.status === "asked").length;
  const running = o.health.running;
  const note = (what: string) => window.prompt(what) ?? null;
  return (
    <>
      <h2>Companies people asked for</h2>
      <div className="card stack">
        {reqs.length ? (
          <>
            <div className="row">
              <button className="primary" disabled={!waiting || running}
                      onClick={() => act({ action: "company_search" }, "Looking for their job boards - a minute or two")}>
                Find job boards for all {waiting} waiting</button>
              <span className="small muted">A few polite requests per company to the job-board providers we read.</span>
            </div>
            <div className="scroll-x">
              <table>
                <thead><tr><th>Company</th><th>Asked</th><th>Where it stands</th><th></th></tr></thead>
                <tbody>{reqs.map(r => (
                  <tr key={r.id}>
                    <td><b>{r.company}</b>{r.url && /^https?:\/\//.test(r.url) && (
                      <div className="small"><a href={r.url} target="_blank" rel="noopener noreferrer nofollow">careers page</a></div>)}</td>
                    <td>{r.asked ?? 0}</td>
                    <td>{REQUEST_WORD[r.status] || r.status}{r.board ? ` · ${r.board}` : ""}{r.postings != null && r.status === "watching" ? ` · ${r.postings} open` : ""}
                      {r.note && <div className="small muted">{r.note}</div>}</td>
                    <td className="row">
                      {(r.status === "asked" || r.status === "not_found") && (
                        <button disabled={running} onClick={() => act({ action: "company_search", id: r.id }, `Looking for ${r.company}'s job board`)}>Find its board</button>
                      )}
                      {r.status === "watching" && (
                        <button onClick={() => { const n = note(`Stop watching ${r.company}'s board? Say why (people who asked see it):`);
                                                 if (n !== null) act({ action: "company_stop", id: r.id, note: n }, "Stopped watching it"); }}>Stop watching</button>
                      )}
                      {r.status !== "declined" && r.status !== "watching" && (
                        <button className="quiet" onClick={() => { const n = note(`Decline ${r.company}? Say why (people who asked see it):`);
                                                                   if (n !== null) act({ action: "company_decline", id: r.id, note: n }, "Declined"); }}>Decline</button>
                      )}
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </>
        ) : <p className="empty">No requests yet. People ask on their Jobs page.</p>}
      </div>
    </>
  );
}

const ACTIONS: Record<string, string> = {
  suspend: "Paused an account", unsuspend: "Resumed an account", delete: "Deleted an account",
  person_limit: "Changed a person's AI limit", revoke_invite: "Cancelled an invite", ai_pause: "Paused all AI",
  ai_resume: "Resumed AI", caps: "Changed spending limits", route: "Changed a job's model",
  cycle_run: "Ran a job update", cycle_pause: "Paused job updates", cycle_resume: "Resumed job updates",
  boards_check: "Checked the job boards", company_search: "Looked for companies' job boards",
  company_decline: "Declined a company request", company_stop: "Stopped watching a company's board",
};

function LogTab({ o }: { o: AdminOverview }) {
  return (
    <div className="card scroll-x">
      <table>
        <thead><tr><th>When</th><th>Who</th><th>What</th><th>Detail</th></tr></thead>
        <tbody>{o.log.map((e, i) => (
          <tr key={i}><td>{ago(e.at)}</td><td>{e.by}</td><td>{ACTIONS[e.action] || e.action}</td>
            <td className="small muted">{[e.target, e.detail].filter(Boolean).join(" · ")}</td></tr>
        ))}</tbody>
      </table>
      {!o.log.length && <p className="empty">No admin changes yet.</p>}
    </div>
  );
}
