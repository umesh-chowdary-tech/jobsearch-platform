"""What the person decided about each posting.

Everything else in their var/ is what the agent found. This file is the only
record of what they did about it: before it, 27 kept postings and not one
decision stored anywhere, so the "worth applying to" list could only grow.

  new -> shortlisted -> applied -> interviewing -> offer
  and at any point: skipped, or closed

Any move is allowed, including back - a mis-click is fixed by clicking again,
and the history keeps both. A referral is not a stage: it can happen before or
after applying, and outreach.json already records it.

Two flags sit beside the stage, not inside it:

  favorite   "I want this one." Sorts first everywhere. Survives any stage.
  later      "Not now - ask me again." Off Today until the date, then back on
             it. A saved-for-later list nobody opens is where postings go to
             expire, so the default is a date, and the undated kind is listed
             on Today by count. Deciding (any stage but new) clears it.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .busy import locked_edit
from .profile import Profile
from .models import utcnow

STAGES = ("new", "shortlisted", "applied", "interviewing", "offer", "closed", "skipped")
ACTIVE = ("shortlisted", "applied", "interviewing", "offer")
DONE = ("closed", "skipped")


class PipelineError(ValueError):
    pass


def path(prof: Profile) -> Path:
    return prof.var / "pipeline.json"


def load(prof: Profile) -> dict[str, dict[str, Any]]:
    p = path(prof)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        # A corrupt file must not look like "no decisions": that would put
        # every skipped posting back on the Today screen.
        raise PipelineError(f"{p} is unreadable - fix or remove it by hand")


def _save(prof: Profile, data: dict[str, dict[str, Any]]) -> None:
    p = path(prof)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix="pipeline.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, p)


def status(data: dict[str, dict[str, Any]], posting_id: str) -> str:
    return (data.get(str(posting_id)) or {}).get("status", "new")


@locked_edit
def set_status(prof: Profile, posting_id: str, new: str, note: str = "",
               variant: str | None = None) -> dict[str, Any]:
    """Record a decision. `variant` is kept when the person applies, so the resume
    that was actually sent is on record next to the application."""
    if new not in STAGES:
        raise PipelineError(f"status must be one of {', '.join(STAGES)}")
    data = load(prof)
    pid = str(posting_id)
    entry = data.setdefault(pid, {"status": "new", "history": []})
    if entry["status"] == new and not note:
        return entry                              # a double click is not an event
    step: dict[str, Any] = {"status": new, "at": utcnow()}
    if note:
        step["note"] = note
    if new == "applied" and variant:
        step["variant"] = variant
        entry["variant"] = variant
    entry["status"] = new
    entry["history"].append(step)
    if new != "new":
        # "Later" meant "decide later". Deciding answers it.
        entry.pop("later", None)
    _save(prof, data)
    return entry


LATER_DAYS = (3, 7, 14)
MAX_LATER_DAYS = 90


def _entry(data: dict[str, dict[str, Any]], posting_id: str) -> dict[str, Any]:
    return data.setdefault(str(posting_id), {"status": "new", "history": []})


@locked_edit
def set_favorite(prof: Profile, posting_id: str, on: bool) -> dict[str, Any]:
    data = load(prof)
    entry = _entry(data, posting_id)
    if on:
        entry.setdefault("favorite", utcnow())
    else:
        entry.pop("favorite", None)
    _save(prof, data)
    return entry


@locked_edit
def save_for_later(prof: Profile, posting_id: str, days: int | None = None,
                   now: datetime | None = None, until: datetime | None = None
                   ) -> dict[str, Any]:
    """Back on Today after `days`, or at `until` (a date picked on a calendar).
    Neither is "someday": no date, so it never comes back on its own."""
    now = now or datetime.now(timezone.utc)
    if days is not None and until is not None:
        raise PipelineError("give days or a date, not both")
    if days is not None:
        # bool is an int in Python: {"days": true} would otherwise mean one day.
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_LATER_DAYS:
            raise PipelineError(f"days must be a whole number from 1 to {MAX_LATER_DAYS}, or none")
        until = now + timedelta(days=days)
    elif until is not None:
        if until.tzinfo is None:
            # A date with no zone is ambiguous by up to a day; the page sends
            # local midnight with its offset, so this only catches misuse.
            raise PipelineError("the date needs a timezone")
        if until <= now:
            raise PipelineError("pick a date after today")
        # The calendar's last day is local midnight of today + 90, which is
        # never later than now + 90 days in any zone - now is already past
        # today's midnight. So no slack is needed.
        if until > now + timedelta(days=MAX_LATER_DAYS):
            raise PipelineError(f"pick a date within {MAX_LATER_DAYS} days")
    data = load(prof)
    entry = _entry(data, posting_id)
    entry["later"] = {
        "since": now.isoformat(timespec="seconds"),
        "until": until.isoformat(timespec="seconds") if until else None,
    }
    _save(prof, data)
    return entry


@locked_edit
def clear_later(prof: Profile, posting_id: str) -> dict[str, Any]:
    data = load(prof)
    entry = _entry(data, posting_id)
    entry.pop("later", None)
    _save(prof, data)
    return entry


def later_state(entry: dict[str, Any] | None, now: datetime | None = None) -> str | None:
    """None (not saved), "waiting" (off Today), "due" (back on Today), or
    "someday" (saved with no date)."""
    later = (entry or {}).get("later")
    if not later:
        return None
    if not later.get("until"):
        return "someday"
    now = now or datetime.now(timezone.utc)
    return "due" if datetime.fromisoformat(later["until"]) <= now else "waiting"


def counts(data: dict[str, dict[str, Any]], posting_ids: list[str]) -> dict[str, int]:
    out = {s: 0 for s in STAGES}
    for pid in posting_ids:
        out[status(data, pid)] += 1
    return out
