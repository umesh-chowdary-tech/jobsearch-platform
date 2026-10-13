"""The admin page: who uses the service and how far they get, what the AI
costs, whether the job updates run - and the switches.

Everything here is read from files the service already keeps; the only thing
kept for it is the day each person last used the service (accounts.seen).
Admins see counts and emails, never a person's resume, profile or jobs.
Every change an admin makes is written to var/_shared/admin-log.jsonl.
"""
from __future__ import annotations

import calendar
import json
import logging
import re
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from .. import company_requests, cycle, feedback, pipeline
from ..assemble import load_library
from ..busy import holder
from ..limits import Meter, load_limits, load_overrides, price_for, save_overrides
from ..llm import KNOWN, ROUTES_OVERRIDE, load_routes
from ..models import utcnow
from ..profile import Profile
from . import accounts

LOG = "admin-log.jsonl"
APPLIED = ("applied", "interviewing", "offer")
DAYS = 30

# The jobs whose model the admin page may change. Reading an uploaded resume
# stays with Claude: the privacy notice says that text goes to Anthropic.
JOBS = {
    "rank": {"label": "Ranking jobs", "groq": True},
    "resume": {"label": "Writing resumes", "groq": True},
    "setup": {"label": "Reading an uploaded resume", "groq": False},
}
CLAUDE_MODELS = {"claude-haiku-5-5": "Claude Haiku 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5",
                 "claude-opus-5-5": "Claude Opus 5.5"}
# The most the page may set, so a slip of the keyboard can't spend much.
CEILING = {"usd_per_month": 200.0, "usd_per_day": 20.0, "person_usd_per_day": 5.0, "person_usd_per_month": 50.0}


class AdminError(Exception):
    pass


class Service(Protocol):
    """What this module needs of hosted/app.py's Service."""
    root: Path
    shared: Path
    mirror: Any
    cycle: Any

    def delete_account(self, sub: str, prof: Profile) -> None: ...
    def start_cycle(self, forced: bool = False) -> tuple[int, dict[str, Any]]: ...
    def start_boards_check(self) -> tuple[int, dict[str, Any]]: ...


# ── recent errors, for the health card ──────────────────────────────────────

class _Recent(logging.Handler):
    def __init__(self, n: int = 40) -> None:
        super().__init__(logging.WARNING)
        self.items: deque[dict[str, str]] = deque(maxlen=n)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            what = (record.getMessage().splitlines() or [""])[0][:240]
        except Exception:  # noqa: BLE001 - a bad log call must not break the request that made it
            what = str(record.msg)[:240]
        if record.exc_info and record.exc_info[0]:
            what += f" ({record.exc_info[0].__name__})"
        self.items.append({"at": utcnow(), "where": record.name, "what": what})


RECENT = _Recent()


def watch_errors() -> None:
    lg = logging.getLogger("jobsearch")
    if RECENT not in lg.handlers:
        lg.addHandler(RECENT)


# ── the log of what admins did ──────────────────────────────────────────────

def record(shared: Path, by: str, action: str, target: str = "", detail: str = "") -> None:
    shared.mkdir(parents=True, exist_ok=True)
    entry = {"at": utcnow(), "by": by, "action": action, "target": target, "detail": detail}
    with (shared / LOG).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def history(shared: Path, n: int = 50) -> list[dict[str, Any]]:
    try:
        lines = (shared / LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines[-n:]):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ── models for each job ─────────────────────────────────────────────────────

def choices(root: Path) -> dict[str, list[dict[str, str]]]:
    """The models each job may be given: Claude's priced ones, and Groq where
    the privacy notice allows it."""
    prices = load_limits(root).get("claude") or {}
    claude = [{"id": f"claude:{m}", "label": label} for m, label in CLAUDE_MODELS.items() if price_for(prices, m)]
    return {job: claude + ([{"id": "groq", "label": "Groq (free tier)"}] if spec["groq"] else [])
            for job, spec in JOBS.items()}


def _route_id(route: dict[str, str]) -> str:
    provider, model = next(iter(route.items()), ("", ""))
    if provider == "claude":
        return f"claude:{model or KNOWN['claude'].default_model}"
    return provider


def _set_route(root: Path, shared: Path, job: str, choice: str) -> None:
    if job not in JOBS:
        raise AdminError("no such job")
    if choice not in {c["id"] for c in choices(root)[job]}:
        raise AdminError("that model can't do this job")
    provider, _, model = choice.partition(":")
    try:
        doc = json.loads((shared / ROUTES_OVERRIDE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        doc = {}
    doc = doc if isinstance(doc, dict) else {}
    doc[job] = {provider: model}
    shared.mkdir(parents=True, exist_ok=True)
    tmp = shared / (ROUTES_OVERRIDE + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(shared / ROUTES_OVERRIDE)


# ── reading what is kept ────────────────────────────────────────────────────

def _json(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _counts(prof: Profile) -> dict[str, int]:
    """How far one person has got - counts only, nothing of what they hold."""
    posts = (_json(prof.var / "triage-results.json").get("postings") or {}).values()
    fits = [(p.get("ai") or {}).get("fit") for p in posts if isinstance(p, dict)]
    try:
        pipe = pipeline.load(prof)
    except pipeline.PipelineError:
        pipe = {}
    answers = feedback.latest(prof).values()
    return {"ranked": sum(1 for f in fits if f), "strong": fits.count("strong"), "possible": fits.count("possible"),
            "resumes": len(load_library(prof)),
            "applied": sum(1 for pid in pipe if pipeline.status(pipe, pid) in APPLIED),
            "answers": len(answers), "good": sum(1 for a in answers if a.get("good"))}


_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2})(T[\d:]+\+00:00)\s{2}(.*)$")


def _runs(shared: Path, since: str) -> list[dict[str, Any]]:
    """The scheduled job updates since a day, from var/_shared/logs/: each
    run's line starts with its watch step (cycle.py)."""
    out = []
    for f in sorted((shared / "logs").glob("cycle-*.log"))[-2:]:
        for line in f.read_text(encoding="utf-8").splitlines():
            m = _LINE.match(line)
            if (not m or m.group(1) < since or not m.group(3).startswith("watch ")
                    or m.group(3).endswith(" traceback")):
                continue
            text = m.group(3)
            new = re.search(r"\bnew=(\d+)", text)
            took = re.search(r"\btook (\d+)s", text)
            out.append({"at": m.group(1) + m.group(2), "day": m.group(1), "text": text[:300],
                        "failed": "FAILED" in text or text.startswith("watch SKIPPED"),
                        "new": int(new.group(1)) if new else 0, "took": int(took.group(1)) if took else None})
    return out


def _boards(shared: Path) -> dict[str, Any]:
    boards = _json(shared / "boards.json")
    stale_before = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    rows = [{"name": b.get("board_name") or key, "live": int(b.get("posting_count") or 0),
             "last_ok": b.get("last_ok") or ""} for key, b in sorted(boards.items()) if isinstance(b, dict)]
    stale = [r for r in rows if not r["last_ok"] or r["last_ok"] < stale_before]
    return {"total": len(rows), "live_postings": sum(r["live"] for r in rows), "stale": stale[:30],
            "stale_count": len(stale)}


def _day_range(n: int) -> list[str]:
    today = date.today()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _ai(root: Path) -> dict[str, Any]:
    days = Meter(root).days()
    limits = load_limits(root)
    claude, groq = limits.get("claude") or {}, limits.get("groq") or {}
    today = date.today()
    month = today.isoformat()[:7]
    series, by_job, by_model = [], {}, {}
    for d in _day_range(DAYS):
        provs = days.get(d) or {}
        series.append({"day": d, "usd": round(sum(float(v.get("usd", 0)) for v in provs.values()), 4),
                       "requests": sum(int(v.get("requests", 0)) for v in provs.values())})
    month_usd = 0.0
    for d, provs in days.items():
        if not d.startswith(month):
            continue
        for v in provs.values():
            month_usd += float(v.get("usd", 0))
            for job, tokens in (v.get("by_purpose") or {}).items():
                row = by_job.setdefault(job or "other", {"job": job or "other", "tokens": 0, "usd": 0.0})
                row["tokens"] += int(tokens)
            for job, usd in (v.get("usd_by_purpose") or {}).items():
                row = by_job.setdefault(job or "other", {"job": job or "other", "tokens": 0, "usd": 0.0})
                row["usd"] = round(row["usd"] + float(usd), 6)
            for model, u in (v.get("by_model") or {}).items():
                row = by_model.setdefault(model, {"model": model, "requests": 0, "usd": 0.0})
                row["requests"] += int(u.get("requests", 0))
                row["usd"] = round(row["usd"] + float(u.get("usd", 0)), 6)
    in_month = calendar.monthrange(today.year, today.month)[1]
    g = (days.get(today.isoformat()) or {}).get("groq") or {}
    return {
        "days": series,
        "today_usd": series[-1]["usd"],
        "month_usd": round(month_usd, 4),
        "forecast_usd": round(month_usd / today.day * in_month, 2),
        "budget_usd": claude.get("usd_per_month"),
        "day_cap_usd": claude.get("usd_per_day"),
        "by_job": sorted(by_job.values(), key=lambda r: (-r["usd"], -r["tokens"])),
        "by_model": sorted(by_model.values(), key=lambda r: (-r["usd"], -r["requests"])),
        "groq_today": {"requests": int(g.get("requests", 0)), "requests_cap": groq.get("requests_per_day"),
                       "tokens": int(g.get("tokens", 0)), "tokens_cap": groq.get("tokens_per_day")},
    }


def _weeks(joined: list[str], n: int = 8) -> list[dict[str, Any]]:
    today = date.today()
    start = today - timedelta(days=today.weekday())          # this Monday
    out = []
    for i in range(n - 1, -1, -1):
        a = start - timedelta(weeks=i)
        b = a + timedelta(weeks=1)
        out.append({"week": a.isoformat(), "n": sum(1 for j in joined if a.isoformat() <= j[:10] < b.isoformat())})
    return out


def overview(svc: Service, missing_settings: list[str]) -> dict[str, Any]:
    root, shared = svc.root, svc.shared
    data = accounts.load(shared)
    usage = Meter(root).month_by_profile()
    over = load_overrides(root)
    own_caps = over.get("people") or {}
    today = date.today()
    week_ago, month_ago = (today - timedelta(days=7)).isoformat(), (today - timedelta(days=30)).isoformat()

    people = []
    for row in sorted(data["users"].values(), key=lambda r: r.get("joined") or ""):
        prof = Profile(root, row["profile"])
        ready = prof.exists()
        people.append({
            "email": row.get("email"), "profile": row["profile"], "joined": row.get("joined"),
            "last_seen": row.get("last_seen"), "suspended": row.get("suspended"), "invite": row.get("invite"),
            "profile_ready": ready,
            "month": usage.get(row["profile"], {"requests": 0, "tokens": 0, "usd": 0.0}),
            "counts": _counts(prof) if ready else None,
            "own_usd_per_day": ((own_caps.get(row["profile"]) or {}).get("claude") or {}).get("usd_per_day"),
        })
    counted = [p["counts"] for p in people if p["counts"]]
    total = {k: sum(c[k] for c in counted) for k in ("ranked", "strong", "possible", "resumes", "applied",
                                                     "answers", "good")}
    funnel = [
        {"step": "Joined", "n": len(people)},
        {"step": "Set up a profile", "n": sum(1 for p in people if p["profile_ready"])},
        {"step": "Got ranked jobs", "n": sum(1 for c in counted if c["ranked"])},
        {"step": "Built a resume", "n": sum(1 for c in counted if c["resumes"])},
        {"step": "Applied", "n": sum(1 for c in counted if c["applied"])},
    ]
    email_of = {sub: r.get("email") for sub, r in data["users"].items()}
    invites = data["invites"]

    since = _day_range(DAYS)[0]
    runs = _runs(shared, since)
    by_day = {d: {"day": d, "runs": 0, "failed": 0, "new": 0} for d in _day_range(DAYS)}
    for r in runs:
        if r["day"] in by_day:
            b = by_day[r["day"]]
            b["runs"] += 1
            b["failed"] += int(r["failed"])
            b["new"] += r["new"]
    took = [r["took"] for r in runs if r["took"] is not None]
    limits = load_limits(root)
    claude = limits.get("claude") or {}
    routes = load_routes(root)
    opts = choices(root)

    return {
        "people": people,
        "active": {"week": sum(1 for p in people if (p["last_seen"] or "") >= week_ago),
                   "month": sum(1 for p in people if (p["last_seen"] or "") >= month_ago)},
        "joins": _weeks([p["joined"] or "" for p in people]),
        "funnel": funnel,
        "totals": total,
        "invites_unused": sorted(({"code": c, **v} for c, v in invites.items() if not v.get("used_by")),
                                 key=lambda r: r.get("created") or "", reverse=True),
        "invites_used": sum(1 for v in invites.values() if v.get("used_by")),
        "invites_used_by": sorted(({"code": c, "used_at": v.get("used_at"),
                                    "email": email_of.get(v["used_by"]) or "(account deleted)"}
                                   for c, v in invites.items() if v.get("used_by")),
                                  key=lambda r: r.get("used_at") or "", reverse=True),
        "month_usd": round(sum(p["month"]["usd"] for p in people), 4),
        "missing_settings": missing_settings,
        "ai": _ai(root),
        "jobs": {"days": list(by_day.values()), "runs": len(runs), "failed": sum(r["failed"] for r in runs),
                 "avg_took": round(sum(took) / len(took)) if took else None,
                 "last": runs[-1] if runs else None, "boards": _boards(shared)},
        "health": {
            "keys": {name: KNOWN[name].configured for name in ("claude", "groq")},     # set or not; never the key
            "database": ({"last_ok": svc.mirror.last_ok, "last_error": svc.mirror.last_error}
                         if svc.mirror else None),
            "cycle_paused": cycle.paused(shared),
            "busy": holder(shared),
            "running": bool(svc.cycle and svc.cycle.is_alive()),
            "errors": list(reversed(RECENT.items))[:20],
        },
        "settings": {
            "ai_paused": bool(over.get("paused")),
            "caps": {"usd_per_month": claude.get("usd_per_month"), "usd_per_day": claude.get("usd_per_day"),
                     "person_usd_per_day": (claude.get("per_profile") or {}).get("usd_per_day"),
                     "person_usd_per_month": (claude.get("per_profile") or {}).get("usd_per_month")},
            "ceiling": CEILING,
            "routes": [{"job": job, "label": spec["label"],
                        "current": _route_id(routes.get(job) or {}), "choices": opts[job]}
                       for job, spec in JOBS.items()],
        },
        "log": history(shared),
        "requests": company_requests.all_requests(shared),
    }


# ── the switches ────────────────────────────────────────────────────────────

def _money(v: Any, ceiling: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= float(v) <= ceiling:
        raise AdminError(f"enter an amount from 0 to {ceiling:g} dollars")
    return round(float(v), 2)


def _pid(body: dict[str, Any]) -> str:
    pid = str(body.get("profile") or "")
    if not re.fullmatch(r"u-[0-9a-f]{16}", pid):
        raise AdminError("no such person")
    return pid


def act(svc: Service, by: str, body: dict[str, Any]) -> dict[str, Any]:
    """One change from the admin page, written to the admin log. Returns what
    the page shows next to the change, if anything."""
    action = str(body.get("action") or "")
    root, shared = svc.root, svc.shared
    out: dict[str, Any] = {}
    if action in ("suspend", "unsuspend"):
        pid = _pid(body)
        row = accounts.suspend(shared, pid, action == "suspend")
        record(shared, by, action, pid, row.get("email") or "")
    elif action == "delete":
        pid = _pid(body)
        if str(body.get("confirm") or "").strip().lower() != "delete":
            raise AdminError('type "delete" to confirm')
        sub, row = accounts.by_profile(shared, pid)
        svc.delete_account(sub, Profile(root, pid))
        over = load_overrides(root)
        if pid in (over.get("people") or {}):
            over["people"].pop(pid)
            save_overrides(root, over)
        record(shared, by, "delete", pid)
    elif action == "person_limit":
        pid = _pid(body)
        accounts.by_profile(shared, pid)
        v = body.get("usd_per_day")
        over = load_overrides(root)
        people = over.setdefault("people", {})
        if v is None:
            people.pop(pid, None)
        else:
            people[pid] = {"claude": {"usd_per_day": _money(v, CEILING["person_usd_per_day"])}}
        save_overrides(root, over)
        record(shared, by, "person_limit", pid, "default" if v is None else f"${float(v):.2f} a day")
    elif action == "revoke_invite":
        code = str(body.get("code") or "")
        accounts.revoke(shared, code)
        record(shared, by, "revoke_invite", code)
    elif action in ("ai_pause", "ai_resume"):
        over = load_overrides(root)
        if action == "ai_pause":
            over["paused"] = True
        else:
            over.pop("paused", None)
        save_overrides(root, over)
        record(shared, by, action)
    elif action == "caps":
        over = load_overrides(root)
        caps = over.setdefault("providers", {}).setdefault("claude", {})
        changed = []
        for key in ("usd_per_month", "usd_per_day"):
            if key in body:
                caps[key] = _money(body[key], CEILING[key])
                changed.append(f"{key} ${caps[key]:.2f}")
        for key, cap in (("person_usd_per_day", "usd_per_day"), ("person_usd_per_month", "usd_per_month")):
            if key in body:
                v = _money(body[key], CEILING[key])
                caps.setdefault("per_profile", {})[cap] = v
                changed.append(f"{key} ${v:.2f}")
        if not changed:
            raise AdminError("nothing to change")
        save_overrides(root, over)
        record(shared, by, "caps", "claude", ", ".join(changed))
    elif action == "route":
        job, choice = str(body.get("job") or ""), str(body.get("choice") or "")
        _set_route(root, shared, job, choice)
        record(shared, by, "route", job, choice)
    elif action == "cycle_run":
        status, data = svc.start_cycle(forced=True)
        if status != 202:
            raise AdminError(data.get("error") or "couldn't start it")
        record(shared, by, "cycle_run")
        out["started"] = True
    elif action == "boards_check":
        status, data = svc.start_boards_check()
        if status != 202:
            raise AdminError(data.get("error") or "couldn't start it")
        record(shared, by, "boards_check")
        out["started"] = True
    elif action == "company_search":
        rid = str(body.get("id") or "")
        rids = [rid] if rid else company_requests.waiting(shared)
        status, data = svc.start_company_search(rids)
        if status != 202:
            raise AdminError(data.get("error") or "couldn't start it")
        record(shared, by, "company_search", ", ".join(rids)[:200])
        out["started"] = True
    elif action in ("company_decline", "company_stop"):
        rid, note = str(body.get("id") or ""), str(body.get("note") or "")
        try:
            row = (company_requests.decline(shared, rid, note) if action == "company_decline"
                   else company_requests.stop_watching(shared, rid, note))
        except company_requests.RequestError as exc:
            raise AdminError(str(exc)) from None
        record(shared, by, action, row["company"], note[:120])
    elif action in ("cycle_pause", "cycle_resume"):
        if action == "cycle_pause":
            cycle.pause(shared, None)
        else:
            cycle.resume(shared)
        record(shared, by, action)
    else:
        raise AdminError("unknown action")
    return out
