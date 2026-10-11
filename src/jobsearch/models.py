"""Data shapes shared across discovery and (later) the watcher."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Posting:
    """One job posting, normalised across the three ATS shapes.

    `id` is the ATS's own identifier. It is what the watcher will diff on, so it
    has to be stable across polls — never a hash of the title or the URL, both of
    which get edited in place.
    """

    id: str
    title: str
    location: str
    url: str
    updated_at: str | None = None
    # Filled where the ATS returns it in the list payload (Lever, Ashby).
    # Greenhouse needs a per-job fetch, so triage does it on demand and
    # only for postings that are actually new.
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Probe outcomes. `not_found` and `blocked` are cached and never escalated;
# `error` is transient and retried next run.
OK = "ok"
EMPTY = "empty"            # valid board, zero postings today
NOT_FOUND = "not_found"    # 404 — the slug is wrong, or the company left this ATS
BLOCKED = "blocked"        # robots.txt disallows
ERROR = "error"            # timeout, 5xx, malformed body


@dataclass
class BoardProbe:
    company: str
    provider: str
    slug: str
    status: str
    url: str
    http_status: int | None = None
    board_name: str | None = None
    postings: list[Posting] = field(default_factory=list)
    error: str | None = None
    checked_at: str = field(default_factory=utcnow)

    @property
    def found(self) -> bool:
        return self.status in (OK, EMPTY)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["postings"] = [p.to_dict() for p in self.postings]
        return d


@dataclass
class Escalation:
    """A question for a human. Discovery escalates on identity doubt ONLY.

    A plain 404 is an answer, not a question — it gets cached. Escalating on
    404s would train the human to ignore the queue, which is how a
    human-in-the-loop system quietly stops having a human in it.
    """

    kind: str
    company: str
    provider: str
    slug: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    raised_at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
