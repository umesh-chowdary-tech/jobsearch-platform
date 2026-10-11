""""Good match?" - a person's answer on a posting, kept to build the ranking
evaluation set (docs/ROADMAP.md, "Quality").

One JSON line per answer in var/<id>/feedback.jsonl; the latest answer for a
posting is the one that counts. The fit and who judged it are copied in, so
the set still says what was being judged after the posting is gone from the
board and from triage.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import utcnow
from .profile import Profile

NOTE_MAX = 500


def path(prof: Profile) -> Path:
    return prof.var / "feedback.jsonl"


def record(prof: Profile, posting: dict[str, Any], good: bool, note: str = "") -> dict[str, Any]:
    ai = posting.get("ai") or {}
    entry = {"posting_id": str(posting.get("id")), "good": bool(good),
             "note": " ".join(str(note or "").split())[:NOTE_MAX],
             "fit": ai.get("fit"), "judged_by": ai.get("by"),
             "title": posting.get("title"), "company": posting.get("company"), "at": utcnow()}
    prof.var.mkdir(parents=True, exist_ok=True)
    with path(prof).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def latest(prof: Profile) -> dict[str, dict[str, Any]]:
    """posting id -> its latest answer."""
    out: dict[str, dict[str, Any]] = {}
    try:
        lines = path(prof).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        out[str(e.get("posting_id"))] = e
    return out
