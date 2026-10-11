"""A self-contained HTML dashboard for one profile, rendered from `var/`.

No server, no build step, no CDN. One file you open in a browser, regenerated
by `jobsearch dashboard`. It is read-only on purpose: acting still happens at
the CLI, so there is exactly one place where things change.

Colours are the validated default data-viz palette. Blue/orange clears the
all-pairs CVD and normal-vision floors in both light and dark against their own
surfaces - checked with the validator, not by eye.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .models import utcnow
from .profile import Profile
from .rank import sort_key
from .triage import top_gaps

# ── template ────────────────────────────────────────────────────────────────

_CSS = """
:root {
  color-scheme: light;
  --surface: #fcfcfb;
  --card: #ffffff;
  --border: #e3e2de;
  --ink: #0b0b0b;
  --ink-2: #52514e;
  --ink-3: #85837c;
  --series-1: #2a78d6;
  --series-2: #eb6834;
  --good: #1baf7a;
  --warn: #eda100;
  --shadow: 0 1px 2px rgba(0,0,0,.05);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface: #1a1a19; --card: #232322; --border: #35352f;
    --ink: #ffffff; --ink-2: #c3c2b7; --ink-3: #8f8e85;
    --series-1: #3987e5; --series-2: #d95926;
    --good: #199e70; --warn: #c98500;
    --shadow: none;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface: #1a1a19; --card: #232322; --border: #35352f;
  --ink: #ffffff; --ink-2: #c3c2b7; --ink-3: #8f8e85;
  --series-1: #3987e5; --series-2: #d95926;
  --good: #199e70; --warn: #c98500;
  --shadow: none;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 16px 64px;
  background: var(--surface); color: var(--ink);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 980px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -.01em; }
h2 { font-size: 15px; margin: 40px 0 12px; letter-spacing: .02em;
     text-transform: uppercase; color: var(--ink-3); font-weight: 600; }
.sub { color: var(--ink-2); margin: 0 0 28px; font-size: 13px; }
.kpis { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(128px, 1fr)); }
.kpi { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
       padding: 14px 16px; box-shadow: var(--shadow); }
.kpi .n { font-size: 30px; font-weight: 650; letter-spacing: -.02em; line-height: 1.1; }
.kpi .l { font-size: 12px; color: var(--ink-2); margin-top: 2px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 10px;
        padding: 16px; box-shadow: var(--shadow); }
.job { border-bottom: 1px solid var(--border); padding: 14px 0; }
.job:last-child { border-bottom: 0; }
.job .t { font-weight: 600; }
.job .m { color: var(--ink-2); font-size: 13px; margin-top: 2px; }
.job a { color: var(--series-1); text-decoration: none; font-size: 13px; }
.job a:hover { text-decoration: underline; }
.pill { display: inline-block; font-size: 11px; padding: 2px 7px; border-radius: 999px;
        border: 1px solid var(--border); color: var(--ink-2); margin: 3px 4px 0 0; }
.pill.c { border-color: var(--series-1); color: var(--series-1); }
.pill.g { border-color: var(--series-2); color: var(--series-2); }
.fit { font-size: 11px; font-weight: 700; letter-spacing: .06em;
       text-transform: uppercase; margin-right: 8px; }
.fit.strong   { color: var(--good); }
.fit.possible { color: var(--ink-2); }
.fit.weak     { color: var(--ink-3); }
.why { color: var(--ink-2); font-size: 13px; margin-top: 4px;
       border-left: 2px solid var(--border); padding-left: 10px; }
.ai-off { color: var(--ink-3); font-size: 12px; }
.score { float: right; font-variant-numeric: tabular-nums; color: var(--ink-2); font-size: 13px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; font-weight: 600; color: var(--ink-3); font-size: 11px;
     text-transform: uppercase; letter-spacing: .04em; padding: 0 8px 8px 0; }
td { padding: 7px 8px 7px 0; border-top: 1px solid var(--border);
     font-variant-numeric: tabular-nums; }
.legend { display: flex; gap: 16px; font-size: 12px; color: var(--ink-2); margin-bottom: 14px; }
.swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block;
          margin-right: 6px; vertical-align: -1px; }
.bar { display: grid; grid-template-columns: 148px 1fr 62px; gap: 10px;
       align-items: center; margin-bottom: 7px; }
.bar .name { font-size: 13px; text-align: right; color: var(--ink-2);
             overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.track { display: flex; height: 18px; }
.track i { display: block; height: 100%; }
.track i.a { background: var(--series-1); border-radius: 3px 0 0 3px; }
.track i.b { background: var(--series-2); border-radius: 0 3px 3px 0;
             margin-left: 2px; }
.track i.a:only-child { border-radius: 3px; }
.bar .v { font-size: 12px; color: var(--ink-3); font-variant-numeric: tabular-nums; }
.note { color: var(--ink-3); font-size: 12px; margin-top: 14px; max-width: 62ch; }
.empty { color: var(--ink-3); font-size: 13px; padding: 10px 0; }
.dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; margin-right: 7px; }
footer { margin-top: 48px; color: var(--ink-3); font-size: 12px; }
@media (max-width: 560px) {
  .bar { grid-template-columns: 96px 1fr 52px; }
  .score { float: none; display: block; margin-top: 4px; }
}
"""


def _e(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _read(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _kpi(n: Any, label: str) -> str:
    return f'<div class="kpi"><div class="n">{_e(n)}</div><div class="l">{_e(label)}</div></div>'


def _gap_chart(rows: list[tuple[str, str, int, int]]) -> str:
    """Horizontal stacked bars: required vs nice-to-have, per skill.

    Two series, so a legend is mandatory and every bar is directly labelled -
    identity is never carried by colour alone.
    """
    if not rows:
        return '<div class="empty">No gaps recorded yet — run <code>triage</code>.</div>'
    top = max((r[2] + r[3]) for r in rows) or 1
    out = [
        '<div class="legend">'
        '<span><i class="swatch" style="background:var(--series-1)"></i>Required</span>'
        '<span><i class="swatch" style="background:var(--series-2)"></i>Nice to have</span>'
        "</div>"
    ]
    for bucket, skill, req, nice in rows:
        wa = req / top * 100
        wb = nice / top * 100
        seg = f'<i class="a" style="width:{wa:.1f}%" title="{_e(skill)}: {req} required"></i>'
        if nice:
            seg += f'<i class="b" style="width:{wb:.1f}%" title="{_e(skill)}: {nice} nice-to-have"></i>'
        label = f"{req}" + (f" + {nice}" if nice else "")
        # The shallow marker rides on the NAME, not the value column. As a
        # suffix on the number it wrapped to a second line and made one row
        # taller than its neighbours, which reads as a data difference.
        mark = "°" if bucket == "shallow" else ""
        hint = "already touched - deepen it" if bucket == "shallow" else "not backed at all"
        out.append(
            f'<div class="bar"><div class="name" title="{_e(skill)} - {hint}">'
            f'{_e(skill)}{mark}</div>'
            f'<div class="track">{seg}</div>'
            f'<div class="v">{label}</div></div>'
        )
    return "".join(out)


def _ai_line(results: dict) -> str:
    """Always says which. "No AI involved" is information, not an absence.

    A dashboard that silently drops to keyword-only ranking when a key expires
    looks identical to one that is working, and you would trust the ordering
    exactly as much either way. You should not.
    """
    ai = results.get("ai") or {}
    ranked = ai.get("ranked") or 0
    if not ranked:
        return ("Keyword ranking only &mdash; <b>No AI involved</b>. "
                "Run <code>jobsearch rank</code> to have a model read the shortlist.")
    bits = [f"Top {ranked} read by <b>{_e(ai.get('attribution'))}</b>"]
    if ai.get("unranked"):
        bits.append(f"{len(ai['unranked'])} got no judgement")
    if ai.get("failures"):
        bits.append(f"{len(ai['failures'])} provider failure(s) recovered")
    return " &middot; ".join(bits)


def _hidden_line(closed: int, rejected: int) -> str:
    """Postings left off the list are counted, never just dropped."""
    bits = []
    if closed:
        bits.append(f"{closed} closed on their board")
    if rejected:
        bits.append(f"{rejected} rejected by <code>triage --recheck</code>")
    if not bits:
        return ""
    return f'<p class="note">Not shown: {" &middot; ".join(bits)}.</p>'


def _jobs(postings: list[dict]) -> str:
    if not postings:
        return ('<div class="empty">Nothing ranked yet. Run '
                "<code>watch</code> then <code>triage</code>.</div>")
    out = []
    for p in postings:
        matched = p.get("matched") or {}
        pills = "".join(
            # A `scoped` skill is matched AND narrow - Redshift is real but only
            # at query level. Marked with a degree sign rather than shown twice
            # in two colours, which is what it did before and read as a
            # contradiction.
            f'<span class="pill c">{_e(k)}{"°" if v == "scoped" else ""}</span>'
            for k, v in sorted(matched.items())
        )
        gaps = "".join(
            f'<span class="pill g">{_e(g)}</span>'
            for g in (p.get("required_gaps") or []) if g not in matched
        )
        url = _e(p.get("url", ""))
        link = f'<a href="{url}" target="_blank" rel="noopener">{url[:78]}</a>' if url else ""
        note = ""
        if p.get("verdict") != "keep":
            note = f'<div class="m">{_e("; ".join(p.get("reasons") or []))}</div>'
        ai = p.get("ai") or {}
        fit = ""
        if ai.get("fit"):
            # The word is always present, so the judgement is never carried by
            # colour alone.
            fit = f'<span class="fit {_e(ai["fit"])}">{_e(ai["fit"])}</span>'
        why = f'<div class="why">{_e(ai.get("why"))}</div>' if ai.get("why") else ""
        out.append(
            f'<div class="job"><span class="score">score {_e(p.get("score", 0))}'
            f' · {_e(p.get("coverage", ""))}</span>'
            f'<div class="t">{fit}{_e(p.get("company"))} — {_e(p.get("title"))}</div>'
            f'<div class="m">{_e(p.get("location") or "—")}'
            f' · {_e(p.get("board"))}</div>'
            f"{note}{why}<div>{pills}{gaps}</div><div>{link}</div></div>"
        )
    return "".join(out)


def _boards(boards: dict, seen_dir: Path) -> str:
    if not boards:
        return '<div class="empty">No boards. Run <code>verify</code>.</div>'
    rows = []
    for key in sorted(boards):
        b = boards[key]
        polls = 0
        sp = seen_dir / f"{b.get('provider')}__{b.get('slug')}.json"
        if sp.exists():
            polls = (_read(sp, {}) or {}).get("poll_count", 0)
        colour = "var(--good)" if polls else "var(--warn)"
        rows.append(
            f'<tr><td><i class="dot" style="background:{colour}"></i>{_e(key)}</td>'
            f"<td>{_e(b.get('board_name') or '—')}</td>"
            f"<td>{_e(b.get('posting_count', 0))}</td>"
            f"<td>{_e(polls) if polls else 'not polled'}</td>"
            f"<td>{_e((b.get('last_ok') or '')[:16].replace('T', ' '))}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Board</th><th>Name</th><th>Live</th>"
        "<th>Polls</th><th>Last checked</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>"
    )


def _escalations(rows: list[dict]) -> str:
    resolved = {(r.get("provider"), r.get("slug")) for r in rows if r.get("kind") == "resolved"}
    open_rows, seen = [], set()
    for r in rows:
        if r.get("kind") == "resolved":
            continue
        key = (r.get("kind"), r.get("provider"), r.get("slug"))
        if key in seen or (r.get("provider"), r.get("slug")) in resolved:
            continue
        seen.add(key)
        open_rows.append(r)
    if not open_rows:
        return '<div class="empty">No open escalations.</div>'
    return "".join(
        f'<div class="job"><div class="t">{_e(r.get("provider"))}:{_e(r.get("slug"))}'
        f' <span class="pill">{_e(r.get("kind"))}</span></div>'
        f'<div class="m">{_e(r.get("reason"))}</div>'
        f'<div class="m"><code>jobsearch escalations --resolve '
        f'{_e(r.get("provider"))}:{_e(r.get("slug"))} --note "..."</code></div></div>'
        for r in open_rows
    )


def render(prof: Profile) -> Path:
    """The person's results (var/<id>/) over the shared board state."""
    var, shared = prof.var, prof.shared
    boards = _read(shared / "boards.json", {})
    results = _read(var / "triage-results.json", {})
    escalations = _read_lines(shared / "escalations.jsonl")
    new_postings = _read_lines(shared / "new-postings.jsonl")
    gaps = top_gaps(var, limit=14)

    # Model judgement first, keyword score as the tiebreak. An un-ranked
    # posting keeps its keyword place rather than sinking below things the
    # model judged weak.
    stored = list((results.get("postings") or {}).values())
    # Closed on their board, or rejected by `triage --recheck`: kept in the file
    # for outreach and the audit trail, never shown as something to apply to.
    closed = [p for p in stored if p.get("closed_at")]
    rejected = [p for p in stored if p.get("verdict") == "reject" and not p.get("closed_at")]
    postings = sorted(
        (p for p in stored if not p.get("closed_at") and p.get("verdict") != "reject"),
        key=sort_key,
    )
    keeps = [p for p in postings if p.get("verdict") == "keep"]
    tracked = 0
    seen_dir = shared / "seen"
    if seen_dir.exists():
        for f in seen_dir.glob("*.json"):
            tracked += len((_read(f, {}) or {}).get("ids", []))
    open_esc = len([
        r for r in escalations if r.get("kind") != "resolved"
    ]) - len([r for r in escalations if r.get("kind") == "resolved"])

    body = f"""<div class="wrap">
<h1>Job search · {_e(prof.id)}</h1>
<p class="sub">Generated {_e(utcnow().replace("T", " ").replace("+00:00", " UTC"))}
 · read-only — act at the CLI</p>

<div class="kpis">
{_kpi(len(boards), "boards live")}
{_kpi(f"{tracked:,}", "postings tracked")}
{_kpi(len(new_postings), "caught by watcher")}
{_kpi(len(keeps), "worth applying to")}
{_kpi(max(open_esc, 0), "open escalations")}
</div>

<h2>Ranked postings</h2>
<div class="card">
<p class="ai-off">{_ai_line(results)}</p>
{_jobs(postings[:25])}{_hidden_line(len(closed), len(rejected))}</div>

<h2>What postings keep asking for that nothing backs</h2>
<div class="card">{_gap_chart(gaps)}
<p class="note">Required counts only include skills named above a
&ldquo;nice to have&rdquo; heading. Table stakes — Git, Agile, Linux — are
excluded. A <b>&deg;</b> marks a skill already touched &mdash; deepening one of
those is cheaper than starting from zero. This is what the market asks for most
often, which is not the same as what makes you worth hiring.</p></div>

<h2>Boards</h2>
<div class="card">{_boards(boards, seen_dir)}</div>

<h2>Escalations</h2>
<div class="card">{_escalations(escalations)}</div>

<footer>jobsearch · regenerate with <code>python -m jobsearch.cli --profile {_e(prof.id)} dashboard</code></footer>
</div>"""

    page = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Job search</title><style>" + _CSS + "</style></head><body>"
        + body + "</body></html>"
    )
    var.mkdir(parents=True, exist_ok=True)
    out = var / "dashboard.html"
    out.write_text(page, encoding="utf-8")
    return out
