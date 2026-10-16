"""The shared job pool: every posting the watcher reads, kept once for everyone.

The watcher's baseline (seen/*.json) holds ids and nothing else; the pool holds
what triage needs to know about each posting - title, place, link, when it was
posted, when it closed - and its description, fetched once however many people
triage it. Each person keeps only their own verdicts (triage-results.json).

  - `var/_shared/pool/<provider>__<slug>.json`: one file per board, a record
    per posting. Written only when something on the board changed, so a quiet
    poll costs the database nothing.
  - `var/_shared/pool/text/<provider>__<slug>/<id>.json`: one description per
    file. Written once; removed SHRINK_DAYS after the posting closes, when the
    record alone is kept. statestore deletes these from the database too.

Only postings posted in the last WINDOW_DAYS are triaged and shown. When a
board gives no date, the day the pool first saw the posting stands in.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .busy import write_atomic
from .models import utcnow

WINDOW_DAYS = 60
SHRINK_DAYS = 90
POOL = "pool"
TEXT = "text"
# A posting checked on its board within this long is not asked again.
CHECK_TTL = 15 * 60

_write_lock = threading.Lock()


# ── where things live ────────────────────────────────────────────────────────

def _board_name(board: str) -> str:
    """provider__slug, as the watcher's seen/ files are named - shortened with
    a hash when long: an Amazon or Microsoft slug names a whole search, and on
    Windows a path past 260 characters can't be written."""
    provider, _, slug = board.partition(":")
    name = f"{provider}__{slug}"
    if len(name) <= 60:
        return name
    return f"{name[:48]}-{hashlib.sha1(name.encode()).hexdigest()[:10]}"


def _safe(pid: str) -> str:
    """A file name for an id. Ids are the boards' own, so most are digits;
    anything else gets a short hash so two ids never share a file."""
    s = re.sub(r"[^A-Za-z0-9_-]", "_", str(pid))
    return s if s == str(pid) else f"{s[:80]}-{hashlib.sha1(str(pid).encode()).hexdigest()[:8]}"


def pool_dir(shared: Path) -> Path:
    return Path(shared) / POOL


def board_path(shared: Path, board: str) -> Path:
    return pool_dir(shared) / f"{_board_name(board)}.json"


def text_path(shared: Path, board: str, pid: str) -> Path:
    return pool_dir(shared) / TEXT / _board_name(board) / f"{_safe(pid)}.json"


def exists(shared: Path) -> bool:
    return any(pool_dir(shared).glob("*.json"))


# ── dates ────────────────────────────────────────────────────────────────────

def posted_day(value: Any) -> str | None:
    """A board's date, in whatever shape it comes, as YYYY-MM-DD. Lever sends
    milliseconds since 1970; the others ISO dates or date-times."""
    if value in (None, ""):
        return None
    s = str(value).strip()
    if s.isdigit():
        n = int(s)
        if n > 10**11:                       # milliseconds
            n //= 1000
        try:
            return datetime.fromtimestamp(n, timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1)).isoformat()
    except ValueError:
        return None


def age_day(rec: dict[str, Any]) -> str | None:
    """The day a posting's age is counted from: when the board says it was
    posted, else when it was first seen, else when it was triaged."""
    return (posted_day(rec.get("posted_at")) or posted_day(rec.get("first_seen"))
            or posted_day(rec.get("triaged_at")))


def _today() -> date:
    return datetime.now(timezone.utc).date()


def within_window(rec: dict[str, Any], today: date | None = None, days: int = WINDOW_DAYS) -> bool:
    """Posted in the last `days` days. Undated counts as recent: nothing says
    it is old."""
    day = age_day(rec)
    if not day:
        return True
    today = today or _today()
    return date.fromisoformat(day) >= today - timedelta(days=days)


# ── reading and writing ──────────────────────────────────────────────────────

def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_board(shared: Path, board: str) -> dict[str, dict[str, Any]]:
    blob = _read(board_path(shared, board))
    return blob if isinstance(blob, dict) else {}


def _save_board(shared: Path, board: str, recs: dict[str, dict[str, Any]]) -> None:
    path = board_path(shared, board)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, dict(sorted(recs.items())))


def load_all(shared: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """board -> id -> record, for every board in the pool."""
    out = {}
    for f in sorted(pool_dir(shared).glob("*.json")):
        blob = _read(f)
        if isinstance(blob, dict) and blob:
            board = next(iter(blob.values())).get("board")
            if board:
                out[board] = blob
    return out


def load_text(shared: Path, board: str, pid: str) -> dict[str, Any] | None:
    blob = _read(text_path(shared, board, pid))
    return blob if isinstance(blob, dict) and blob.get("description") else None


def save_text(shared: Path, board: str, pid: str, fields: dict[str, Any]) -> None:
    path = text_path(shared, board, pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, {**fields, "fetched_at": utcnow()})


# ── after each poll ──────────────────────────────────────────────────────────

def _queue_first_seen(shared: Path) -> dict[str, str]:
    """When the queue first recorded each posting - for a board the pool has
    not met yet, the closest thing to its posting date the watcher kept."""
    out: dict[str, str] = {}
    p = Path(shared) / "new-postings.jsonl"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = f"{rec.get('board')}|{rec.get('id')}"
        if rec.get("first_seen") and key not in out:
            out[key] = rec["first_seen"]
    return out


def update(shared: Path, boards: Iterable[Any], now: str | None = None) -> dict[str, int]:
    """Fold one watcher run into the pool. `boards` are the run's BoardWatch
    results; a board whose poll failed is left exactly as it was, so a timeout
    never closes anything.

    A posting new to the pool is first seen now - unless its board was being
    watched before the pool existed: then the queue's date, or the day the
    board was first read, which is the most the watcher can vouch for."""
    from .watch import load_seen, seen_path       # watch calls this; no cycle at import

    now = now or utcnow()
    today = _today()
    counts = {"added": 0, "closed": 0, "reopened": 0, "texts": 0}
    queue: dict[str, str] | None = None
    with _write_lock:
        for b in boards:
            if b.status not in ("seeded", "polled"):
                continue
            board = b.key
            met = board_path(shared, board).exists()
            recs = load_board(shared, board)
            fallback = now
            if not met and b.status == "polled":
                queue = _queue_first_seen(shared) if queue is None else queue
                fallback = (load_seen(seen_path(shared, b.provider, b.slug)) or {}).get("seeded_at") or now
            changed = not met          # an empty board is written too: the pool has met it
            current = {post.id: post for post in b.current}
            fresh = {post.id for post in b.added}
            for pid, post in current.items():
                rec = recs.get(pid)
                if rec is None:
                    first = now if pid in fresh else (queue or {}).get(f"{board}|{pid}") or fallback
                    rec = {"id": pid, "board": board, "company": b.company, "title": post.title,
                           "location": post.location, "url": post.url,
                           "posted_at": posted_day(post.updated_at), "first_seen": first}
                    recs[pid] = rec
                    counts["added"] += 1
                    changed = True
                    # Lever, Ashby, Amazon and Atlassian send the description
                    # with the list: keep it now, for what anyone could triage.
                    if post.description and within_window(rec, today):
                        save_text(shared, board, pid, {"description": post.description})
                        counts["texts"] += 1
                    continue
                if rec.pop("closed_at", None):
                    counts["reopened"] += 1
                    changed = True
                for key, value in (("title", post.title), ("url", post.url), ("company", b.company)):
                    if value and rec.get(key) != value:
                        rec[key] = value
                        changed = True
                day = posted_day(post.updated_at)
                if day and not rec.get("posted_at"):
                    rec["posted_at"] = day
                    changed = True
            for pid, rec in recs.items():
                if pid not in current and not rec.get("closed_at"):
                    rec["closed_at"] = now
                    counts["closed"] += 1
                    changed = True
            if changed:
                _save_board(shared, board, recs)
    return counts


def retire(shared: Path, watched: Iterable[str], now: str | None = None) -> int:
    """Close every open posting on a board that is no longer watched: nobody
    will poll it again to say whether it is still open."""
    now = now or utcnow()
    watched = set(watched)
    closed = 0
    with _write_lock:
        for board, recs in load_all(shared).items():
            if board in watched:
                continue
            open_ = [r for r in recs.values() if not r.get("closed_at")]
            for r in open_:
                r["closed_at"] = now
            if open_:
                closed += len(open_)
                _save_board(shared, board, recs)
    return closed


def open_postings(shared: Path, today: date | None = None) -> list[dict[str, Any]]:
    """What triage reads: every open posting in the window, without its
    description - `details` fetches that, once, for what could still pass."""
    return [dict(rec) for recs in load_all(shared).values() for rec in recs.values()
            if not rec.get("closed_at") and within_window(rec, today)]


def is_open(shared: Path, board: str, pid: str) -> bool | None:
    """From the pool: None when it does not know the posting."""
    rec = load_board(shared, board).get(str(pid))
    if rec is None:
        return None
    return not rec.get("closed_at")


def mark_closed(shared: Path, board: str, pid: str, now: str | None = None) -> bool:
    with _write_lock:
        recs = load_board(shared, board)
        rec = recs.get(str(pid))
        if rec is None or rec.get("closed_at"):
            return False
        rec["closed_at"] = now or utcnow()
        _save_board(shared, board, recs)
        return True


def _set_posted(shared: Path, board: str, pid: str, day: str) -> None:
    with _write_lock:
        recs = load_board(shared, board)
        rec = recs.get(str(pid))
        if rec is not None and not rec.get("posted_at"):
            rec["posted_at"] = day
            _save_board(shared, board, recs)


# ── descriptions, once for everyone ──────────────────────────────────────────

def details(shared: Path, fetcher: Any, posting: dict[str, Any]) -> dict[str, Any]:
    """The fields a posting's list entry withheld - its description, and for
    some boards the full location and the date - from the pool when it has
    them, else from the board once, kept for the next person. {} when there
    is nothing to be had."""
    from .providers import DETAIL_PROVIDERS, fetch_details

    board, pid = str(posting.get("board") or ""), str(posting.get("id") or "")
    if ":" not in board or not pid:
        return {}
    kept = load_text(shared, board, pid)
    if kept:
        return {k: v for k, v in kept.items() if k != "fetched_at"}
    provider, slug = board.split(":", 1)
    if provider not in DETAIL_PROVIDERS or fetcher is None:
        return {}
    got = fetch_details(fetcher, provider, slug, posting)
    if got.get("description"):
        save_text(shared, board, pid, got)
        day = posted_day(got.get("updated_at"))
        if day:
            _set_posted(shared, board, pid, day)
    return got


# ── 90 days after closing, only the record ───────────────────────────────────

def shrink(shared: Path, now: datetime | None = None, days: int = SHRINK_DAYS) -> int:
    """Remove the descriptions of postings closed more than `days` ago. The
    records stay: they are small, and they are the pool's history."""
    now = now or datetime.now(timezone.utc)
    cut = (now - timedelta(days=days)).isoformat(timespec="seconds")
    gone = 0
    for board, recs in load_all(shared).items():
        for pid, rec in recs.items():
            if rec.get("closed_at") and rec["closed_at"] <= cut:
                path = text_path(shared, board, pid)
                if path.exists():
                    path.unlink()
                    gone += 1
    return gone


# ── is it still open? one posting, asked of its board ───────────────────────

def quick_fetcher(shared: Path) -> Any:
    """For a check someone is waiting on: a short timeout and no retries, so a
    slow board costs seconds, not the minute the cycle's fetcher would wait."""
    from .http import Fetcher, user_agent
    from .robots import RobotsCache
    return Fetcher(RobotsCache(Path(shared) / "robots", user_agent()), timeout=6.0, retries=0)


def last_poll(shared: Path) -> Callable[[str], str | None]:
    def when(board: str) -> str | None:
        from .watch import load_seen, seen_path
        provider, _, slug = board.partition(":")
        return (load_seen(seen_path(shared, provider, slug)) or {}).get("last_poll")
    return when


_checked: dict[tuple[str, str], tuple[float, bool | None]] = {}


def _ask_board(fetcher: Any, board: str, pid: str, company: str | None) -> bool | None:
    """True or False only when the board answers plainly; None otherwise."""
    from .providers import probe

    provider, slug = board.split(":", 1)
    if provider == "greenhouse":
        res = fetcher.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{pid}")
    elif provider == "lever":
        res = fetcher.get(f"https://api.lever.co/v0/postings/{slug}/{pid}")
    elif provider in ("ashby", "atlassian"):
        # One request answers for the whole board.
        p = probe(fetcher, provider, slug, company, with_board_name=False)
        if not p.found:
            return None
        return any(post.id == pid for post in p.postings)
    else:
        return None
    if res.status == 200:
        return True
    if res.status in (404, 410):
        return False
    return None


def check_live(shared: Path, fetcher: Any, board: str, pid: str,
               company: str | None = None, last_poll: Callable[[str], str | None] | None = None) -> bool | None:
    """Whether a posting is still open, asking its board when the last poll is
    more than CHECK_TTL old. False also closes it in the pool. None when the
    board can't be asked plainly; the watcher's word then stands."""
    if ":" not in board:
        return None
    key = (board, str(pid))
    hit = _checked.get(key)
    if hit and time.monotonic() - hit[0] < CHECK_TTL:
        return hit[1]
    if last_poll:
        polled = last_poll(board)
        if polled:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(polled)
            if age.total_seconds() < CHECK_TTL:
                return is_open(shared, board, pid)
    try:
        answer = _ask_board(fetcher, board, str(pid), company)
    except Exception:  # noqa: BLE001 - a blocked or failed request answers nothing
        answer = None
    _checked[key] = (time.monotonic(), answer)
    if answer is False:
        mark_closed(shared, board, pid)
    return answer
