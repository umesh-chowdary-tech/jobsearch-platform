"""The AI queue: pass 3 for everyone, four postings to a request, until none
are left.

The cycle used to give ranking three minutes and leave the rest for the next
one. This reads until every kept posting at or above the match bar has a
judgement, and it does so on its own: the job update never waits for it, a
resume being built never waits for it, and a person's clicks never do.

  - One turn per person at a time (TURN seconds of requests), so nobody's
    long list starves anyone else's.
  - A rate limit that asks for a short wait is waited out; a long one, a
    daily or monthly cap, or AI paused on the admin page ends the run for
    that person, and the next job update starts it again.
  - While a person's own action is running (a resume being built), the queue
    steps aside until it ends.
  - What it is doing is written to var/<id>/ai-queue.json for the screens:
    "AI is reading your jobs: 12 of 40".

On the hosted service it runs in its own thread, started after each job
update. On a PC the scheduled cycle runs it after it lets go of its lock.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .busy import stop_requested, write_atomic
from .models import utcnow
from .profile import Profile, list_profiles

STATUS = "ai-queue.json"
TURN = 60.0                 # seconds of requests for one person before the next
LONGEST_WAIT = 15 * 60      # a limit asking for longer ends the run
STEP_ASIDE = 5.0            # how often to look again while a person's action runs
IDLE_TURNS = 3              # turns in a row with nothing judged and no reason given: give up

_lock = threading.Lock()    # one queue at a time in a process


def status(prof: Profile) -> dict[str, Any] | None:
    try:
        doc = json.loads((prof.var / STATUS).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _save(prof: Profile, **info: Any) -> dict[str, Any]:
    doc = {**(status(prof) or {}), **info, "updated": utcnow()}
    prof.var.mkdir(parents=True, exist_ok=True)
    write_atomic(prof.var / STATUS, doc)
    return doc


def view(prof: Profile) -> dict[str, Any] | None:
    """For the screens: the queue's state, or None when there is nothing to
    say. A "running" left behind by a process that died says nothing."""
    doc = status(prof)
    if not doc:
        return None
    state = doc.get("state")
    if state == "running":
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(str(doc.get("updated")))
        except ValueError:
            return None
        if age > timedelta(minutes=10):
            return None
    if state == "waiting" and doc.get("until"):
        try:
            if datetime.fromisoformat(str(doc["until"])) <= datetime.now(timezone.utc):
                return None
        except ValueError:
            pass
    if state not in ("running", "waiting"):
        return None
    return {k: doc.get(k) for k in ("state", "done", "total", "left", "why", "until", "by")}


def pending(prof: Profile) -> int:
    """How many postings are waiting for a model, for this person."""
    from .rank import floor_for, ranking_lead, unread
    path = prof.var / "triage-results.json"
    if not path.exists():
        return 0
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(unread(blob, lead=ranking_lead(prof), floor=floor_for(prof)))


_WAIT = re.compile(r"try again in ((?:\d+(?:\.\d+)?(?:ms|s|m|h))+)")


def wait_seconds(why: str) -> float | None:
    """The wait a rate limit asked for, in seconds; None when it named none
    (a daily cap, AI paused) - waiting would not help today."""
    m = _WAIT.search(why or "")
    if not m:
        return None
    total = 0.0
    for n, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", m.group(1)):
        total += float(n) * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    return total


def _later(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _tomorrow() -> str:
    # Caps are counted per local calendar day (docs/limits.md).
    now = datetime.now().astimezone()
    start = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return start.astimezone(timezone.utc).isoformat(timespec="seconds")


def drain(root: Path, profiles: Iterable[Profile] | None = None,
          should_stop: Callable[[], bool] = lambda: False,
          stepping_aside: Callable[[], bool] = lambda: False,
          sleep: Callable[[float], None] = time.sleep,
          turn: float = TURN) -> dict[str, Any]:
    """Read every person's waiting postings until none are left, a limit ends
    it, or `should_stop()`. Returns {"ranked": n, "people": n, "ended": why}."""
    from .llm import LLM
    from .rank import apply_to_results

    root = Path(root)
    people = list(profiles) if profiles is not None else [Profile(root, pid) for pid in list_profiles(root)]
    out: dict[str, Any] = {"ranked": 0, "people": 0, "ended": ""}
    if not _lock.acquire(blocking=False):
        out["ended"] = "already running"
        return out
    try:
        active: list[Profile] = []
        for prof in people:
            n = pending(prof)
            if n:
                _save(prof, state="running", done=0, total=n, left=n, why="", until=None, since=utcnow())
                active.append(prof)
            elif (status(prof) or {}).get("state") in ("running", "waiting"):
                _save(prof, state="done", left=0, why="", until=None)     # nothing left: say so
        out["people"] = len(active)
        if not active:
            out["ended"] = "nothing waiting"
            return out
        ok, why = LLM(active[0]).key_check()
        if not ok:
            for prof in active:
                _save(prof, state="waiting", why=f"no AI available: {why}", until=None)
            out["ended"] = f"no key: {why}"
            return out

        idle: dict[str, int] = {}
        while active:
            for prof in list(active):
                if should_stop() or stop_requested(prof.shared):
                    for p in active:
                        _save(p, state="stopped", why="stopped")
                    out["ended"] = "stopped"
                    return out
                while stepping_aside() and not should_stop():
                    sleep(STEP_ASIDE)
                doc = status(prof) or {}
                res = apply_to_results(prof, budget=turn)
                ranked = int(res.get("ranked") or 0)
                out["ranked"] += ranked
                left = int(res.get("left") or 0)
                done = int(doc.get("done") or 0) + ranked
                # Two passes (Groq first, then the lead) each give their own
                # reason, joined with "; ". Only a limit among them ends a turn
                # early; "time budget" alone just means the turn is over.
                reasons = [x.strip() for x in str(res.get("stopped") or "").split(";") if x.strip()]
                stopped = "; ".join(x for x in reasons if x != "time budget")
                if reasons and not stopped:
                    stopped = "time budget"
                if not left or (not res.get("considered") and not stopped):
                    _save(prof, state="done", done=done, left=0, total=max(int(doc.get("total") or 0), done),
                          by=res.get("attribution"), why="", until=None)
                    active.remove(prof)
                    continue
                total = max(int(doc.get("total") or 0), done + left)
                idle[prof.id] = 0 if ranked else idle.get(prof.id, 0) + 1
                if not ranked and not stopped and idle[prof.id] >= IDLE_TURNS:
                    # Requests sent, nothing came back, and no limit to blame:
                    # a provider down, or answers that never parse. Try again
                    # at the next job update rather than in a tight loop.
                    _save(prof, state="waiting", done=done, left=left, total=total,
                          why="the AI isn't answering - trying again at the next update", until=None)
                    active.remove(prof)
                    continue
                if not stopped or stopped == "time budget":
                    _save(prof, state="running", done=done, left=left, total=total, by=res.get("attribution"))
                    continue
                if "stopped by you" in stopped:
                    _save(prof, state="stopped", done=done, left=left, total=total, why="stopped")
                    active.remove(prof)
                    continue
                wait = wait_seconds(stopped)
                if wait is not None and wait <= LONGEST_WAIT:
                    _save(prof, state="waiting", done=done, left=left, total=total, why=stopped,
                          until=_later(wait))
                    sleep(wait + 1)
                    _save(prof, state="running", why="", until=None)
                    continue
                until = _later(wait) if wait is not None else (_tomorrow() if "daily" in stopped else None)
                _save(prof, state="waiting", done=done, left=left, total=total, why=stopped, until=until)
                active.remove(prof)
        waiting = sum(1 for p in people if (status(p) or {}).get("state") == "waiting")
        out["ended"] = out["ended"] or (f"{waiting} waiting for a limit" if waiting else "all read")
        return out
    finally:
        _lock.release()
