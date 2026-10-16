"""Everything in var/, shaped for the screens. Read-only.

Each function answers one screen's question. None of them computes anything
the CLI does not: company names, due follow-ups, sort order and the gap chart
all come from the modules that own them, so the app and the CLI cannot drift.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .. import feedback, outreach, pipeline
from ..assemble import load_library, load_store
from ..busy import holder, stop_requested
from ..cycle import paused
from ..dashboard import _gap_chart
from ..latex import find_engine
from ..pool import within_window
from ..llm import LLM
from ..rank import sort_key
from ..profile import Profile
from ..skills import load_index
from ..registry import open_escalations
from ..triage import load_profile as load_search_profile, top_gaps

RESUME_EXT = (".pdf", ".html", ".txt", ".tex")


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _postings(prof: Profile) -> dict[str, dict[str, Any]]:
    return (_read(prof.var / "triage-results.json", {}) or {}).get("postings") or {}


def raw_posting(prof: Profile, pid: str) -> dict[str, Any] | None:
    """The posting as triage stored it."""
    return _postings(prof).get(str(pid))


def check_open(prof: Profile, pid: str) -> bool | None:
    """Ask the posting's board whether it is still open - the watcher's last
    poll can be up to two hours old. A posting found closed is closed in the
    pool and in this person's results, so no list shows it again. None when
    the board can't say plainly; the watcher's word stands."""
    from .. import pool
    from ..triage import mark_closed
    p = raw_posting(prof, pid)
    if p is None:
        return None
    if p.get("closed_at"):
        return False
    board = str(p.get("board") or "")
    live = pool.check_live(prof.shared, pool.quick_fetcher(prof.shared), board, str(pid),
                           company=p.get("company"), last_poll=pool.last_poll(prof.shared))
    if live is False:
        mark_closed(prof.var, pid)
    return live


def _row(prof: Profile, p: dict[str, Any], pipe: dict, ledger: dict, variants: dict) -> dict:
    """One posting, as a list row. Company names go through the same function
    outreach uses, so the app never shows "razorpaysoftwareprivatelimited"."""
    pid = str(p.get("id"))
    company, confirmed = outreach.company_name(prof, str(p.get("board") or ""), p)
    asks = [d for d in ledger.values() if str(d.get("posting_id")) == pid]
    ai = p.get("ai") or {}
    entry = pipe.get(pid) or {}
    later = entry.get("later")
    matched = p.get("matched") or {}
    return {
        "favorite": bool(entry.get("favorite")),
        "later": ({"state": pipeline.later_state(entry), "until": later.get("until"),
                   "since": later.get("since")} if later else None),
        "id": pid,
        "company": company,
        "company_confirmed": confirmed,
        "title": outreach._squash(p.get("title")),
        "location": p.get("location") or "",
        "board": p.get("board") or "",
        "url": p.get("url") or "",
        "verdict": p.get("verdict"),
        "fit": ai.get("fit"),
        "coverage": p.get("coverage") or "",
        "score": p.get("score", 0),
        # A few of each for the list row; the posting's own page has them all.
        "skills": list(matched)[:5],
        "gaps": [g for g in p.get("required_gaps") or [] if g not in matched][:3],
        "closed": bool(p.get("closed_at")),
        "first_seen": p.get("first_seen"),
        "status": pipeline.status(pipe, pid),
        "variant": variants.get(pid),
        "asks": [{"to": d["to"], "status": d["status"]} for d in asks],
    }


def _variants_by_posting(prof: Profile) -> dict[str, str]:
    out: dict[str, tuple[str, str]] = {}
    for v in load_library(prof):
        for s in v.sent_to:
            pid, date = str(s.get("posting_id")), str(s.get("date"))
            if pid not in out or date >= out[pid][0]:
                out[pid] = (date, v.id)
    return {k: v for k, (_, v) in out.items()}


def ats_for(prof: Profile, pid: str) -> dict[str, Any] | None:
    """The ATS match recorded when this posting's resume was last built."""
    best: tuple[str, dict] | None = None
    for v in load_library(prof):
        for s in v.sent_to:
            if str(s.get("posting_id")) == str(pid) and s.get("ats"):
                if best is None or str(s.get("date")) >= best[0]:
                    best = (str(s.get("date")), s["ats"])
    return best[1] if best else None


def _touched(row: dict[str, Any]) -> bool:
    """Something the person did with a posting: it stays listed at any age."""
    return bool(row["status"] != "new" or row["favorite"] or row["later"] or row["variant"] or row["asks"])


def postings(prof: Profile) -> list[dict[str, Any]]:
    """Every posting listed for this person. One posted before the pool's
    window (60 days) is left out unless they have done something with it;
    its own page still opens."""
    pipe, ledger, variants = pipeline.load(prof), outreach.load_ledger(prof), _variants_by_posting(prof)
    rows = []
    for p in sorted(_postings(prof).values(), key=sort_key):
        row = _row(prof, p, pipe, ledger, variants)
        if within_window(p) or _touched(row):
            rows.append(row)
    # Favorites first; within each group the model-then-keyword order stands.
    # sorted() is stable, so this only lifts favorites, never reshuffles.
    return sorted(rows, key=lambda r: not r["favorite"])


def posting(prof: Profile, pid: str) -> dict[str, Any] | None:
    p = _postings(prof).get(str(pid))
    if p is None:
        return None
    pipe, ledger = pipeline.load(prof), outreach.load_ledger(prof)
    row = _row(prof, p, pipe, ledger, _variants_by_posting(prof))
    ai = p.get("ai") or {}
    row.update({
        "why": ai.get("why") or "",
        "judged_by": ai.get("by") or "",
        "matched": p.get("matched") or {},
        "required_gaps": [g for g in p.get("required_gaps") or [] if g not in (p.get("matched") or {})],
        "nice_gaps": p.get("nice_gaps") or [],
        "reasons": p.get("reasons") or [],
        "description": (p.get("description") or "").strip(),
        "live": outreach.posting_live(prof, row["board"], row["id"]),
        "history": (pipe.get(row["id"]) or {}).get("history", []),
        "drafts": [d for d in ledger.values() if str(d.get("posting_id")) == row["id"]],
        "files": resume_files(prof, row["variant"]) if row["variant"] else [],
        "ats": ats_for(prof, row["id"]) if row["variant"] else None,
        "emails": posting_emails(prof, row["id"]),
        "feedback": feedback.latest(prof).get(row["id"]),
    })
    return row


def resume_files(prof: Profile, variant: str) -> list[dict[str, str]]:
    d = prof.var / "resumes"
    return [{"name": f"{variant}{ext}", "url": f"/files/resumes/{variant}{ext}"}
            for ext in RESUME_EXT if (d / f"{variant}{ext}").exists()]


def today(prof: Profile) -> dict[str, Any]:
    rows = postings(prof)
    live = [r for r in rows if r["verdict"] == "keep" and not r["closed"]]
    undecided = [r for r in live if r["status"] == "new"]

    def later(r: dict) -> str | None:
        return (r["later"] or {}).get("state")

    # Saved for later is off Today until its date. When the date passes it
    # comes back in its own section, so it reads as "you asked me to remind
    # you", not as a new posting.
    back = [r for r in undecided if later(r) == "due"]
    waiting = [r for r in undecided if later(r) in ("waiting", "someday")]
    fresh = [r for r in undecided if later(r) is None]
    decide = [r for r in fresh if r["fit"] in ("strong", "possible")]
    unread = [r for r in fresh if not r["fit"]]
    ledger = outreach.load_ledger(prof)
    due = []
    for d in ledger.values():
        need = outreach.due(d)
        if need:
            due.append({"id": d["id"], "to": d["to"], "company": d["company"],
                        "role": d["role"], "need": need, "posting_id": d["posting_id"]})
    name = str((load_store(prof).get("identity") or {}).get("name") or "")
    return {
        "first_name": name.split()[0] if name.split() else "",
        "decide": decide,
        "back_from_later": back,
        "saved_for_later": len(waiting),
        "favorites": sum(1 for r in live if r["favorite"] and r["status"] not in pipeline.DONE),
        "unread": len(unread),
        "in_progress": [r for r in rows if r["status"] in pipeline.ACTIVE],
        "outreach_due": due,
        "escalations": open_escalations(prof.shared / "escalations.jsonl"),
        "stages": pipeline.counts(pipeline.load(prof), [r["id"] for r in live]),
        "inbox": inbox_summary(prof),
        # Read what was already open on the boards (cycle.py) - until then
        # only postings that appeared since the boards were first read show.
        "caught_up": (prof.var / "caught-up.json").exists(),
    }


# ── health ───────────────────────────────────────────────────────────────────

_LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}T[\d:]+\+00:00)\s{2}(.*)$")


def cycle_log(prof: Profile, n: int = 12) -> list[dict[str, str]]:
    logs = sorted((prof.shared / "logs").glob("cycle-*.log"))
    out: list[dict[str, str]] = []
    for f in reversed(logs):
        for line in reversed(f.read_text(encoding="utf-8").splitlines()):
            m = _LOG_LINE.match(line)
            if m and "traceback" not in m.group(2):
                out.append({"at": m.group(1), "line": m.group(2)})
            if len(out) >= n:
                return out
    return out


_SCHED: dict[str, Any] = {"at": 0.0, "value": None}


def scheduler() -> dict[str, str] | None:
    """The Windows task's own view of itself. Cached a minute: schtasks is slow."""
    if os.name != "nt":
        return None
    if time.time() - _SCHED["at"] < 60:
        return _SCHED["value"]
    value = None
    try:
        r = subprocess.run(["schtasks", "/query", "/tn", "JobSearch Cycle", "/fo", "LIST", "/v"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            fields = dict(l.split(":", 1) for l in r.stdout.splitlines() if ":" in l)
            value = {k: fields.get(k, "").strip()
                     for k in ("Status", "Next Run Time", "Last Run Time", "Last Result")}
    except (OSError, subprocess.SubprocessError):
        value = None
    _SCHED.update(at=time.time(), value=value)
    return value


def overview(prof: Profile) -> dict[str, Any]:
    log = cycle_log(prof, 1)
    return {
        "busy": holder(prof.shared),
        "stopping": stop_requested(prof.shared),
        "paused": paused(prof.shared),
        "last_cycle": log[0] if log else None,
        "scheduler": scheduler(),
        "now": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def system(prof: Profile) -> dict[str, Any]:
    boards = _read(prof.shared / "boards.json", {}) or {}
    rows = []
    for key in sorted(boards):
        b = boards[key]
        seen = _read(prof.shared / "seen" / f"{b.get('provider')}__{b.get('slug')}.json", {}) or {}
        rows.append({"key": key, "name": b.get("board_name") or "",
                     "live": b.get("posting_count", 0), "polls": seen.get("poll_count", 0),
                     "last_ok": b.get("last_ok") or ""})
    return {
        "boards": rows,
        "escalations": open_escalations(prof.shared / "escalations.jsonl"),
        "log": cycle_log(prof, 20),
        "tex_engine": find_engine(),
        # Provider names and whether a key is set. Never the key.
        "models": LLM(prof).status(),
        "scheduler": scheduler(),
        "paused": paused(prof.shared),
        "busy": holder(prof.shared),
    }


# ── applications, from the inbox ─────────────────────────────────────────────

def _days_ago(n: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat(timespec="seconds")


def applications(prof: Profile) -> dict[str, Any]:
    from .. import applications as A
    from ..mailbox import configured, load_state
    ledger = A.load(prof)
    rows = sorted((a for a in ledger["apps"].values() if not a.get("ignored")),
                  key=lambda a: a["updated"], reverse=True)
    counts: dict[str, int] = {}
    for a in rows:
        counts[a["status"]] = counts.get(a["status"], 0) + 1
    since = (datetime.now(timezone.utc).date().toordinal() - 30)
    recent = {d: v for d, v in ledger["naukri"]["applied"].items()
              if datetime.fromisoformat(d).toordinal() >= since}
    neo = ledger["naukri"]["neo"]
    from ..llm import LLM
    from ..mail_ai import PROVIDERS, last_status
    from ..mail_learn import pending
    return {
        "configured": configured(prof),
        "groq": LLM(prof, providers=PROVIDERS).available,
        "groq_last": last_status(prof),
        "last_sync": load_state(prof).get("synced_at"),
        "apps": rows,
        "counts": counts,
        "look": [a for a in rows if a.get("look") or a.get("suggestion")],
        # Groq's readings applied without a click in the last two weeks - each
        # with an undo. Includes the ones it said were not applications.
        "auto": sorted((a for a in ledger["apps"].values()
                        if a.get("auto") and a["auto"]["at"] >= _days_ago(14)),
                       key=lambda a: a["auto"]["at"], reverse=True),
        "proposals": sorted(pending(prof), key=lambda p: p["proposed_at"]),
        "naukri": {
            "applied_30d": sum(v.get("count", 0) for v in recent.values()),
            "days_30d": len(recent),
            "applied_all": sum(v.get("count", 0) for v in ledger["naukri"]["applied"].values()),
            "recruiter_actions_30d": sum(v.get("recruiter_actions", 0) for d, v in neo.items()
                                         if datetime.fromisoformat(d).toordinal() >= since),
            "status_pings": len(ledger["naukri"]["status_pings"]),
        },
    }


def tracked(prof: Profile) -> dict[str, Any]:
    """Every application, however it was made - the hosted Applied screen.
    What the ledger keeps of an email: its subject, and the sentence that
    decided it."""
    from .. import applications as A
    from ..mail_paste import KINDS
    ledger = A.load(prof)
    rows = sorted((a for a in ledger["apps"].values() if not a.get("ignored")),
                  key=lambda a: a["updated"], reverse=True)
    keep = ("id", "company", "role", "status", "posting_id", "first_seen", "updated", "look")
    return {
        "apps": [{**{k: a.get(k) for k in keep},
                  "events": [{k: e.get(k) for k in ("date", "status", "subject", "evidence")} for e in a["events"]]}
                 for a in rows],
        "kinds": KINDS,
        "statuses": list(A.RANK),
    }


def inbox_summary(prof: Profile) -> dict[str, Any]:
    """For Today: what the inbox says needs the person's attention."""
    from .. import applications as A
    ledger = A.load(prof)
    today = datetime.now(timezone.utc).date().toordinal()

    def age(a: dict) -> int:
        return today - datetime.fromisoformat(a["updated"]).toordinal()
    live = [a for a in ledger["apps"].values() if not a.get("ignored")]
    return {
        "interviews": sorted((a for a in live if a["status"] == "interviewing" and age(a) <= 21),
                             key=lambda a: a["updated"], reverse=True),
        "outcomes": sorted((a for a in live if a["status"] in ("rejected", "cancelled", "offer") and age(a) <= 7),
                           key=lambda a: a["updated"], reverse=True),
        "look": sum(1 for a in live if a.get("look")),
        "open": sum(1 for a in live if a["status"] in A.OPEN),
    }


def posting_emails(prof: Profile, pid: str) -> list[dict[str, Any]]:
    from .. import applications as A
    for a in A.load(prof)["apps"].values():
        if a.get("posting_id") == str(pid):
            return [{"date": e["date"], "status": e["status"], "subject": e["subject"]} for e in a["events"]]
    return []


# ── the other screens ────────────────────────────────────────────────────────

def resumes(prof: Profile) -> dict[str, Any]:
    posts = _postings(prof)

    def used(s: dict[str, Any]) -> dict[str, Any]:
        # The company as the rest of the app names it, and whether the job's
        # page can still be opened.
        p = posts.get(str(s.get("posting_id")))
        name = outreach.company_name(prof, str(p.get("board") or ""), p)[0] if p else s.get("company")
        return {**s, "company": name, "known": p is not None}

    lib = []
    for v in load_library(prof):
        lib.append({"id": v.id, "descriptor": v.title_descriptor, "summary": v.summary,
                    "summary_by": v.summary_by, "surfaces": v.surfaces, "created": v.created,
                    "sent_to": [used(s) for s in v.sent_to], "files": resume_files(prof, v.id),
                    "checks_ok": all(c.get("ok") for c in (v.parse_check or {}).get("checks", []))
                    if v.parse_check else None})
    manual_dir = prof.var / "manual" / "resumes"
    manual = [{"name": f.name, "url": f"/files/manual/{f.name}"}
              for f in sorted(manual_dir.glob("*")) if f.is_file()] if manual_dir.exists() else []
    return {"variants": lib, "manual": manual}


def outreach_view(prof: Profile) -> list[dict[str, Any]]:
    rows = []
    for d in outreach.load_ledger(prof).values():
        rows.append({**d, "need": outreach.due(d)})
    rows.sort(key=lambda d: (d["need"] is None, d.get("created", "")))
    return rows


def market(prof: Profile) -> dict[str, Any]:
    rows = top_gaps(prof.var, limit=16)
    # The local app draws the dashboard's chart as-is: one implementation of
    # the chart and its validated palette, not two that drift apart. The
    # hosted app draws the same rows itself.
    return {"chart_html": _gap_chart(rows),
            "gaps": [{"skill": skill, "bucket": bucket, "required": req, "nice": nice}
                     for bucket, skill, req, nice in rows]}


ENTRY_FIELDS = ("id", "title", "type", "where", "period", "headline", "facts", "metrics", "tech",
                "confidence", "priority", "status", "angles", "evidence", "sources", "notes")


def _prefs_view(prof: Profile) -> dict[str, Any] | None:
    """What the person is looking for, as search-profile.yaml says it."""
    try:
        sp = load_search_profile(prof.dir)
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(sp, dict):
        return None
    loc, sen = sp.get("location") or {}, sp.get("seniority") or {}
    return {"preferred": loc.get("preferred") or [], "acceptable": loc.get("acceptable") or [],
            "remote": bool(loc.get("remote_terms")), "years": sen.get("years_experience"),
            "max_years": sen.get("max_years_required"), "level": sen.get("level") or "",
            "watch": sp.get("companies_to_watch") or []}


def profile(prof: Profile) -> dict[str, Any]:
    store = load_store(prof)
    # The same index triage and assembly gate on - not a second parse of the YAML.
    index = load_index(prof)
    tiers: dict[str, list[dict[str, Any]]] = {}
    for c in sorted(index.claims.values(), key=lambda c: c.skill.lower()):
        tiers.setdefault(c.tier, []).append(
            {"skill": c.skill, "scope": c.scope, "category": index.category.get(c.skill)})
    idn = store.get("identity") or {}
    # Contact details stay out: the connector hands `identity` to a chat.
    return {
        "identity": {"name": idn.get("name"), "title": (idn.get("title") or {}).get("official"),
                     "descriptors": (idn.get("title") or {}).get("approved_descriptors"),
                     "location": idn.get("location")},
        "links": {k: idn[k] for k in ("linkedin", "github", "portfolio") if idn.get(k)},
        "employment": [{k: j.get(k) for k in ("employer", "title", "location", "start", "end", "client") if j.get(k)}
                       for j in idn.get("employment") or [] if isinstance(j, dict)],
        "languages": [str(x) for x in idn.get("languages") or []],
        "entries": [{k: e.get(k) for k in ENTRY_FIELDS}
                    for e in store.get("entries") or [] if isinstance(e, dict)],
        "tiers": tiers,
        "prefs": _prefs_view(prof),
    }
