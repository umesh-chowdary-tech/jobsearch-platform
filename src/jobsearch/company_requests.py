"""Companies people ask to be watched.

A person names a company (and, if they know it, its careers page). Whoever
runs the service sees the requests on the admin page and has discovery look
for the company's job board (discovery.search_company: a handful of polite
requests to the job-board providers, robots.txt and rate limits held). A
board that answers goes into boards.json, so the next job update reads it.

The shared list holds no one's name: a company, the page someone gave, and
how many asked. Which requests are a person's own sits in their own folder
(`company-asks.json`), so deleting the account deletes it. Every change to
the shared list is made under the edit lock: many people share it.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .busy import editing
from .discovery import DiscoveryRun, normalise
from .models import utcnow
from .profile import Profile

FILE = "company-requests.json"
MINE = "company-asks.json"
MAX_OPEN = 5                     # a person's requests waiting at once
STATUSES = ("asked", "checking", "watching", "not_found", "declined")


class RequestError(ValueError):
    pass


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def load(shared: Path) -> dict[str, dict[str, Any]]:
    return _read(Path(shared) / FILE, {}) or {}


def save(shared: Path, reqs: dict[str, dict[str, Any]]) -> None:
    _write(Path(shared) / FILE, reqs)


def _mine(prof: Profile) -> list[str]:
    return [str(x) for x in _read(prof.var / MINE, []) or []]


def watched(shared: Path) -> dict[str, str]:
    """normalised company -> board key, for every board the watcher reads."""
    boards = _read(Path(shared) / "boards.json", {}) or {}
    return {normalise(str(b.get("company") or "")): key for key, b in boards.items()
            if isinstance(b, dict) and b.get("company")}


def _url(url: str) -> str:
    """A careers page as given - shown to whoever runs the service, never
    fetched by it."""
    url = (url or "").strip()
    if not url:
        return ""
    if len(url) > 300 or not url.lower().startswith(("https://", "http://")) or any(c.isspace() for c in url):
        raise RequestError("the careers page should be a web address starting with https://")
    return url


def ask(prof: Profile, company: str, url: str = "") -> dict[str, Any]:
    company = " ".join((company or "").split())[:120]
    rid = normalise(company)
    if not rid:
        raise RequestError("name the company")
    url = _url(url)
    with editing(prof.shared):
        return _ask(prof, rid, company, url)


def _ask(prof: Profile, rid: str, company: str, url: str) -> dict[str, Any]:
    mine = _mine(prof)
    shared = prof.shared
    reqs = load(shared)
    if rid in mine:
        return view(reqs.get(rid) or {}, rid)
    open_ = [r for r in mine if (reqs.get(r) or {}).get("status") in ("asked", "checking")]
    if len(open_) >= MAX_OPEN:
        raise RequestError(f"you have {MAX_OPEN} requests waiting already - wait for one to be looked at")
    req = reqs.get(rid)
    if req is None:
        board = watched(shared).get(rid)
        req = {"id": rid, "company": company, "url": url, "asked": 0, "first_asked": utcnow(),
               "status": "watching" if board else "asked", "board": board or "", "note": ""}
        reqs[rid] = req
    elif url and not req.get("url"):
        req["url"] = url
    req["asked"] = int(req.get("asked") or 0) + 1
    save(shared, reqs)
    _write(prof.var / MINE, mine + [rid])
    return view(req, rid)


def withdraw(prof: Profile, rid: str) -> None:
    mine = _mine(prof)
    if rid not in mine:
        raise RequestError("no such request of yours")
    _write(prof.var / MINE, [r for r in mine if r != rid])
    with editing(prof.shared):
        reqs = load(prof.shared)
        req = reqs.get(rid)
        if req:
            req["asked"] = max(0, int(req.get("asked") or 0) - 1)
            if not req["asked"] and req.get("status") == "asked":
                reqs.pop(rid)
            save(prof.shared, reqs)


def view(req: dict[str, Any], rid: str = "") -> dict[str, Any]:
    keep = ("id", "company", "status", "board", "note", "postings", "checked_at")
    return {k: req.get(k) for k in keep} | {"id": req.get("id") or rid}


def mine(prof: Profile) -> list[dict[str, Any]]:
    reqs = load(prof.shared)
    return [view(reqs[r], r) for r in reversed(_mine(prof)) if r in reqs]


# ── for whoever runs the service ─────────────────────────────────────────────

def waiting(shared: Path) -> list[str]:
    return [rid for rid, r in load(shared).items() if r.get("status") in ("asked", "checking")]


def mark_checking(shared: Path, rids: list[str]) -> None:
    with editing(shared):
        reqs = load(shared)
        for rid in rids:
            if rid in reqs:
                reqs[rid]["status"] = "checking"
        save(shared, reqs)


def release(shared: Path, rids: list[str], note: str) -> None:
    """Requests a search started on but never reached: waiting again."""
    with editing(shared):
        reqs = load(shared)
        left = [r for r in rids if (reqs.get(r) or {}).get("status") == "checking"]
        for rid in left:
            reqs[rid].update(status="asked", note=note)
        if left:
            save(shared, reqs)


def record_search(shared: Path, rid: str, run: DiscoveryRun) -> dict[str, Any]:
    """What discovery found for one request. A board it found is already in
    boards.json (registry.write_run)."""
    with editing(shared):
        return _record(shared, rid, run)


def _record(shared: Path, rid: str, run: DiscoveryRun) -> dict[str, Any]:
    reqs = load(shared)
    req = reqs.get(rid)
    if req is None:
        return {}
    found = run.found
    req["checked_at"] = utcnow()
    if found:
        p = found[0]
        req.update(status="watching", board=f"{p.provider}:{p.slug}", postings=len(p.postings),
                   note="check it's theirs: " + "; ".join(e.reason for e in run.escalations)[:300]
                   if run.escalations else "")
    elif any(p.status not in ("not_found",) for p in run.probes):
        req.update(status="asked", note="a job board didn't answer - try again later")
    else:
        req.update(status="not_found", note="no job board we can read - their careers site may be their own")
    save(shared, reqs)
    return req


def decline(shared: Path, rid: str, note: str) -> dict[str, Any]:
    with editing(shared):
        reqs = load(shared)
        if rid not in reqs:
            raise RequestError("no such request")
        reqs[rid].update(status="declined", note=(note or "").strip()[:300])
        save(shared, reqs)
        return reqs[rid]


def stop_watching(shared: Path, rid: str, note: str = "") -> dict[str, Any]:
    """A board discovery matched to the wrong company, or one not worth
    reading: out of boards.json, so no update reads it again."""
    with editing(shared):
        return _stop(shared, rid, note)


def _stop(shared: Path, rid: str, note: str) -> dict[str, Any]:
    reqs = load(shared)
    req = reqs.get(rid)
    if req is None or not req.get("board"):
        raise RequestError("that request has no board being watched")
    path = Path(shared) / "boards.json"
    boards = _read(path, {}) or {}
    boards.pop(req["board"], None)
    _write(path, boards)
    req.update(status="declined", note=(note or "stopped watching").strip()[:300], board="")
    save(shared, reqs)
    return req


def all_requests(shared: Path) -> list[dict[str, Any]]:
    return sorted(load(shared).values(), key=lambda r: (r.get("status") not in ("asked", "checking"),
                                                        -int(r.get("asked") or 0), r.get("first_asked") or ""))
