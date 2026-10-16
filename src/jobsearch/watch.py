"""Module 2 — the watcher.

Poll the boards discovery found, diff job IDs against what we saw last time,
emit only what is new. No model involved, and that is the point: an LLM reading
every posting on every poll would spend ~99% of its budget re-reading yesterday.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .busy import stop_requested
from .http import Fetcher
from .models import NOT_FOUND, Posting, utcnow
from .providers import probe


def _key(provider: str, slug: str) -> str:
    return f"{provider}:{slug}"


def seen_path(var_dir: Path, provider: str, slug: str) -> Path:
    # One file per board: a crash mid-write can only corrupt one board's
    # baseline, and a board can be reset by deleting a single file.
    return Path(var_dir) / "seen" / f"{provider}__{slug}.json"


def load_seen(path: Path) -> dict[str, Any] | None:
    """None means this board has never been polled — not that it is empty."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(blob, dict) or "ids" not in blob:
        return None
    return blob


def posting_live(
    var_dir: Path, board: str, posting_id: str, since: str | None = None
) -> bool | None:
    """Whether the last poll of `board` saw this posting.

    None when the watcher cannot say: it does not track the board, or its last
    poll is no later than `since`. A posting read from a live `--board` a few
    minutes ago has not closed just because the watcher has not polled since.

    outreach.py has its own copy, so that it never imports a module that fetches.
    """
    if ":" not in board:
        return None
    provider, slug = board.split(":", 1)
    seen = load_seen(seen_path(var_dir, provider, slug))
    if seen is None:
        return None
    if str(posting_id) in {str(i) for i in seen["ids"]}:
        return True
    if since and str(seen.get("last_poll") or "") <= since:
        return None
    return False


def save_seen(path: Path, ids: Iterable[str], previous: dict[str, Any] | None) -> None:
    """Atomic write: temp file in the same directory, then replace.

    A half-written baseline is worse than no baseline — the next poll would read
    a truncated id list and report a hundred stale postings as new.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = {
        "ids": sorted(set(ids)),
        "last_poll": utcnow(),
        "seeded_at": (previous or {}).get("seeded_at") or utcnow(),
        "poll_count": int((previous or {}).get("poll_count", 0)) + 1,
    }
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(blob, fh, indent=2)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ── one board ────────────────────────────────────────────────────────────────

SEEDED = "seeded"     # first sight: baseline recorded, nothing reported
POLLED = "polled"
SKIPPED = "skipped"   # error or block; baseline deliberately left untouched


@dataclass
class BoardWatch:
    provider: str
    slug: str
    company: str
    status: str
    added: list[Posting] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    total: int = 0
    # Everything open on the board this run, for the pool (pool.update).
    # The baseline file keeps ids, nothing more.
    current: list[Posting] = field(default_factory=list, repr=False)
    error: str | None = None

    @property
    def key(self) -> str:
        return _key(self.provider, self.slug)


def watch_board(fetcher: Fetcher, var_dir: Path, board: dict[str, Any]) -> BoardWatch:
    provider, slug = board["provider"], board["slug"]
    company = board.get("company") or slug
    w = BoardWatch(provider=provider, slug=slug, company=company, status=SKIPPED)

    p = probe(fetcher, provider, slug, company, with_board_name=False)

    if not p.found:
        # A 404 means the board is gone and discovery will drop it. Anything
        # else is transient. Either way the baseline is NOT updated: overwriting
        # it with an empty result would report the entire board as new next time.
        w.status = SKIPPED
        w.error = p.error or (f"board returned {p.status}")
        if p.status == NOT_FOUND:
            w.error = "board no longer exists (404) — re-run discovery"
        return w

    current = {post.id: post for post in p.postings}
    w.total = len(current)
    w.current = list(current.values())
    path = seen_path(var_dir, provider, slug)
    previous = load_seen(path)

    if previous is None:
        # First sight. Record the baseline and report nothing — otherwise the
        # first run would emit every posting on every board, which is 4,869
        # items of noise and would bury the first genuine diff.
        save_seen(path, current.keys(), None)
        w.status = SEEDED
        return w

    before = set(previous["ids"])
    now = set(current)
    w.added = [current[i] for i in sorted(now - before)]
    w.removed = sorted(before - now)
    w.status = POLLED
    save_seen(path, now, previous)
    return w


# ── a run across all boards ──────────────────────────────────────────────────

@dataclass
class WatchRun:
    boards: list[BoardWatch] = field(default_factory=list)

    @property
    def added(self) -> list[tuple[BoardWatch, Posting]]:
        return [(b, post) for b in self.boards for post in b.added]

    def summary(self) -> dict[str, int]:
        return {
            "boards": len(self.boards),
            "seeded": sum(b.status == SEEDED for b in self.boards),
            "polled": sum(b.status == POLLED for b in self.boards),
            "skipped": sum(b.status == SKIPPED for b in self.boards),
            "new_postings": sum(len(b.added) for b in self.boards),
            "closed_postings": sum(len(b.removed) for b in self.boards),
        }


def load_boards(var_dir: Path) -> list[dict[str, Any]]:
    p = Path(var_dir) / "boards.json"
    if not p.exists():
        return []
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return sorted(blob.values(), key=lambda b: (b.get("provider", ""), b.get("slug", "")))


def watch_all(
    fetcher: Fetcher, var_dir: Path, boards: Iterable[dict[str, Any]]
) -> WatchRun:
    run = WatchRun()
    for board in boards:
        if stop_requested(var_dir):
            # Stopped from the app. The boards not reached keep their
            # baselines, so the next run sees their news exactly.
            break
        try:
            run.boards.append(watch_board(fetcher, var_dir, board))
        except Exception as exc:
            # One board's failure is that board's problem. Raising here aborted
            # the run after the boards before it had saved their new baselines,
            # and before record_new - so what was new on them was marked seen
            # and never recorded. A stale app process met the new Workday board
            # this way on 2026-09-24. watch_board saves last, so a board that
            # raises has its baseline untouched: skipping it is exact.
            run.boards.append(BoardWatch(
                provider=str(board.get("provider")), slug=str(board.get("slug")),
                company=str(board.get("company") or board.get("slug")),
                status=SKIPPED, error=f"{type(exc).__name__}: {exc}",
            ))
    return run


def _compact_queue(path: Path) -> None:
    """Once: drop the descriptions a queue written before the pool still
    carries - megabytes copied to the database whole on every cycle. The
    pool keeps them now. A marker says it is done, so the file is not read
    whole on every run after."""
    marker = path.with_name(".queue-compacted")
    if marker.exists() or not path.exists():
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(utcnow(), encoding="utf-8")
        return
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue                      # a torn line: nothing to keep
        rec.pop("description", None)
        lines.append(json.dumps(rec))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(f"{x}\n" for x in lines), encoding="utf-8")
    os.replace(tmp, path)
    marker.write_text(utcnow(), encoding="utf-8")


def record_new(var_dir: Path, run: WatchRun) -> Path:
    """Fold the run into the shared pool (pool.py), and append what is new to
    the queue - a log of arrivals, one line each, the run a posting was first
    seen. Descriptions live in the pool, not here: the queue is never pruned,
    and it is copied to the database whole."""
    from .pool import update
    update(var_dir, run.boards)
    path = Path(var_dir) / "new-postings.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    _compact_queue(path)
    stamp = utcnow()
    with path.open("a", encoding="utf-8") as fh:
        for board, post in run.added:
            fh.write(json.dumps({
                "first_seen": stamp,
                "board": board.key,
                "company": board.company,
                **{k: v for k, v in post.to_dict().items() if k != "description"},
            }) + "\n")
    return path
