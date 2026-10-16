"""One scheduled pass: watch the boards once, then for every profile triage
what is new, rank it, read the inbox and regenerate the dashboard.

Boards are shared and people are separate: one poll of each board serves every
profile, and fills the shared pool (pool.py) - each posting and its description
kept once. Each profile keeps its own record of which postings it has triaged,
so a profile added today reads everything open in the pool on its first cycle.

Scheduled runs have nobody reading stdout, so this writes a one-line summary to
`var/_shared/logs/` and keeps going when a step fails. A cycle that dies on a
single flaky board would stop the watcher, and a stopped watcher looks exactly
like a quiet job market. One profile's failure never stops the next one's.
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import pool
from .busy import busy, holder, stop_requested, write_atomic
from .dashboard import render
from .http import default_fetcher
from .llm import LLM
from .models import utcnow
from .profile import Profile, list_profiles, shared_var
from .rank import apply_to_results, floor_for, ranking_lead, unread
from .triage import sync_closed, sync_pool
from .triage_cli import run_triage
from .watch import load_boards, posting_live, record_new, watch_all

# Seconds of model requests a cycle may start inline, shared between the
# profiles - only when the caller holds the lock and wants the ranking done
# inside it (the local app's "Run a full cycle"). Otherwise the AI queue
# (ai_queue.py) reads everything after the cycle, without a time limit, and
# holds nothing else up.
RANK_BUDGET = 180.0
# The least any one profile gets, however many there are.
RANK_FLOOR = 30.0


def _log(var: Path, line: str) -> None:
    d = var / "logs"
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"cycle-{date.today():%Y%m}.log").open("a", encoding="utf-8") as fh:
        fh.write(f"{utcnow()}  {line}\n")


# ── pause: the schedule skips its ticks until a time, or until resumed ──────
# A file, because the scheduled cycle is another process. Only the scheduled
# tick obeys it: "Run cycle" in the app is you asking, so it still runs.

PAUSE = ".paused.json"


def pause(var: Path, hours: float | None) -> dict[str, str | None]:
    """`hours=None`: until resumed."""
    now = datetime.now(timezone.utc)
    info = {"since": now.isoformat(timespec="seconds"),
            "until": (now + timedelta(hours=hours)).isoformat(timespec="seconds") if hours else None}
    Path(var).mkdir(parents=True, exist_ok=True)
    write_atomic(Path(var) / PAUSE, info)
    return info


def resume(var: Path) -> None:
    (Path(var) / PAUSE).unlink(missing_ok=True)


def paused(var: Path) -> dict[str, str | None] | None:
    """The pause in force, or None. One that has run out is no pause."""
    try:
        info = json.loads((Path(var) / PAUSE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    until = info.get("until")
    if until and datetime.fromisoformat(until) <= datetime.now(timezone.utc):
        return None
    return info


def run_cycle(root: Path, quiet: bool = False, lock: bool = True,
              profiles: list[Profile] | None = None, rank: str | None = None) -> int:
    """`profiles`: whom to triage and rank for - every profile by default.
    `lock=False` only for a caller that already holds `busy` - the app does,
    and a second lock from the same process is refused on Windows.

    `rank`: "inline" ranks inside the cycle for at most RANK_BUDGET seconds;
    "after" lets go of the lock first, then runs the AI queue until nothing
    is left; "none" leaves it to the caller (the hosted service runs the
    queue in a thread of its own). Default: "after" with the lock, "inline"
    for a caller already holding it."""
    root = Path(root)
    var = shared_var(root)
    rank = rank or ("after" if lock else "inline")
    if profiles is None:
        profiles = [Profile(root, pid) for pid in list_profiles(root)]
    if not lock:
        return _cycle(root, profiles, quiet, inline=rank == "inline")
    p = paused(var)
    if p:
        # Exit 0: a paused schedule is doing what it was told.
        _log(var, f"cycle SKIPPED paused until {p.get('until') or 'resumed'}")
        return 0
    with busy(var, "scheduled cycle") as got:
        if not got:
            # Someone - the app, usually - is mid-action on the same files.
            # Skipping one 15-minute tick loses nothing; racing on seen/*.json
            # can. Exit 0: a skipped tick is not a failed one.
            who = holder(var) or {}
            _log(var, f"cycle SKIPPED busy: {who.get('what')} since {who.get('since')}")
            return 0
        code = _cycle(root, profiles, quiet, inline=rank == "inline")
    if rank == "after":
        # Outside the lock: the next cycle, or a click in the app, is not
        # held up by a long read.
        from .ai_queue import drain
        try:
            res = drain(root, profiles)
            _log(var, f"ai-queue read={res['ranked']} people={res['people']} ended={res['ended']!r}")
        except Exception as exc:  # noqa: BLE001
            _log(var, f"ai-queue FAILED {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            code = 1
    return code


class _Run:
    """What one cycle has done so far, for the log line."""

    def __init__(self, var: Path, quiet: bool) -> None:
        self.var, self.parts, self.failed = var, [], False
        self.say = (lambda s: None) if quiet else print
        self.t0 = time.monotonic()

    def failure(self, step: str, exc: Exception) -> None:
        self.failed = True
        self.parts.append(f"{step} FAILED {type(exc).__name__}: {exc}")
        _log(self.var, f"{step} traceback\n" + traceback.format_exc())

    def stopped(self, after: str) -> bool:
        """Stopped from the app: end here. What ran is saved; the rest waits
        for the next cycle."""
        if not stop_requested(self.var):
            return False
        self.parts.append(f"STOPPED by you after {after}")
        self.say(f"stopped by you after {after}")
        return True

    def end(self) -> int:
        _log(self.var, " | ".join([*self.parts, f"took {round(time.monotonic() - self.t0)}s"]))
        # Non-zero only on a real failure, so a scheduler's "last result"
        # column means something. A quiet cycle with nothing new is a success.
        return 1 if self.failed else 0


def _cycle(root: Path, profiles: list[Profile], quiet: bool, inline: bool = True) -> int:
    var = shared_var(root)
    run = _Run(var, quiet)

    # ── watch: once, for everyone ────────────────────────────────────────────
    try:
        boards = load_boards(var)
        if not boards:
            _log(var, "watch SKIPPED no boards - run `verify` first")
            run.say("no boards known - run `verify` first")
            return 1
        w = watch_all(default_fetcher(var), var, boards)
        record_new(var, w)                      # the queue, and the pool
        s = w.summary()
        run.parts.append(
            f"watch boards={s['boards']} new={s['new_postings']} "
            f"closed={s['closed_postings']} skipped={s['skipped']}"
        )
        run.say(f"watch: {s}")
    except Exception as exc:  # noqa: BLE001 - a cycle must not die on one step
        run.failure("watch", exc)

    # ── the pool: boards no longer watched, descriptions past keeping ───────
    try:
        retired = pool.retire(var, {f"{b.get('provider')}:{b.get('slug')}" for b in load_boards(var)})
        gone = pool.shrink(var)
        if retired or gone:
            run.parts.append(f"pool retired={retired} shrunk={gone}")
    except Exception as exc:  # noqa: BLE001
        run.failure("pool", exc)

    if run.stopped("watch"):
        return run.end()
    if not profiles:
        run.parts.append("no profiles - nothing to triage")
        return run.end()

    budget = max(RANK_FLOOR, RANK_BUDGET / len(profiles))
    keys: dict[str, tuple[bool, str]] = {}
    for prof in profiles:
        if _for_profile(prof, run, budget if inline else 0.0, keys):
            return run.end()
    return run.end()


CAUGHT_UP = "caught-up.json"


def caught_up(prof: Profile) -> bool:
    """Whether this profile has had its first triage: everything open in the
    pool, read once. Until then the service starts an update when the person
    first saves their profile, rather than leaving them waiting."""
    return (prof.var / CAUGHT_UP).exists()


def _live(shared: Path):
    """Open or closed, for sync_closed: the pool's word, else the watcher's."""
    def live(board: str, pid: str, since: str | None) -> bool | None:
        known = pool.is_open(shared, board, pid)
        return known if known is not None else posting_live(shared, board, pid, since)
    return live


def _for_profile(prof: Profile, run: _Run, budget: float, keys: dict[str, tuple[bool, str]]) -> bool:
    """One person's share of the cycle. True when the run was stopped."""
    tag = prof.id
    shared = prof.shared

    # ── closed on the board ──────────────────────────────────────────────────
    # Read from the seen files, not this run's diff, so a posting that left
    # during a skipped cycle is still caught. Before triage on purpose: a
    # re-triaged posting's `triaged_at` is later than the last poll, and the
    # watcher will not speak for it until the next one - so a posting that came
    # back would stay marked closed for a whole cycle.
    try:
        closed, reopened = sync_closed(prof.var, _live(shared))
        if closed or reopened:
            run.parts.append(f"{tag} closed marked={len(closed)} reopened={len(reopened)}")
            run.say(f"{tag}: closed on the board: {len(closed)} marked, {len(reopened)} reopened")
        # Posting dates from the pool, and long-closed postings shrunk.
        boards: dict[str, dict[str, dict[str, Any]]] = {}

        def posted(board: str, pid: str) -> str | None:
            if board not in boards:
                boards[board] = pool.load_board(shared, board)
            return (boards[board].get(pid) or {}).get("posted_at")

        done = sync_pool(prof.var, posted)
        if done["dated"] or done["shrunk"]:
            run.parts.append(f"{tag} pool dated={done['dated']} shrunk={done['shrunk']}")
    except Exception as exc:  # noqa: BLE001
        run.failure(f"{tag} closed", exc)

    if run.stopped(f"{tag} closed"):
        return True

    # ── triage: the pool, less what this profile has read ────────────────────
    try:
        kept = run_triage(prof, argparse.Namespace(all=False, board=None, quiet=True)) or 0
        run.parts.append(f"{tag} triage kept={kept}")
        run.say(f"{tag}: triage kept {kept}")
        if not caught_up(prof) and prof.exists():
            (prof.var / CAUGHT_UP).write_text(json.dumps({"at": utcnow()}), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        run.failure(f"{tag} triage", exc)

    if run.stopped(f"{tag} triage"):
        return True

    # ── rank (pass 3) ────────────────────────────────────────────────────────
    # At most `budget` seconds of requests for this profile; what is left waits
    # for the next cycle. Each answered request is saved as it comes.
    # Every kept posting no model has read, four to a request - and nothing
    # when there are none. Re-judging an unchanged shortlist 96 times a day is
    # the waste the diff-first design avoids; leaving kept postings unread was
    # the opposite mistake. A dead key is found by a request with no content,
    # once a cycle, so a broken key doesn't mean a failed call per profile.
    try:
        path = prof.var / "triage-results.json"
        waiting = len(unread(json.loads(path.read_text(encoding="utf-8")) if path.exists() else {},
                             lead=ranking_lead(prof), floor=floor_for(prof)))
        if not waiting:
            run.parts.append(f"{tag} rank skipped nothing-unread")
        elif not budget:
            run.parts.append(f"{tag} rank queued {waiting}")       # the AI queue reads them
        else:
            if "lead" not in keys:
                keys["lead"] = LLM(prof).key_check()
            ok, why = keys["lead"]
            if not ok:
                run.parts.append(f"{tag} rank skipped {waiting}-unread {why}")
            else:
                res = apply_to_results(prof, budget=budget)
                # Not "stopped": that name is the stop-request check, and
                # shadowing it once crashed every cycle that ranked anything.
                cut = f" stopped={res['stopped']!r}" if res.get("stopped") else ""
                run.parts.append(f"{tag} rank read={res.get('ranked', 0)} of={waiting} "
                                 f"left={res.get('left', 0)} by={res.get('attribution')}{cut}")
                run.say(f"{tag}: rank {res.get('ranked', 0)} of {waiting} unread judged by "
                        f"{res.get('attribution')}"
                        + (f", {res.get('left', 0)} left for the next cycle ({res['stopped']})" if cut else ""))
    except Exception as exc:  # noqa: BLE001
        run.failure(f"{tag} rank", exc)

    if run.stopped(f"{tag} rank"):
        return True

    # ── inbox ────────────────────────────────────────────────────────────────
    # Only for the profile MAIL_PROFILE names. One read-only IMAP question -
    # "anything after the last UID?" - which is almost always "no". No model
    # is involved. A mail problem is logged and must not stop the watcher.
    try:
        from .mailbox import configured
        if configured(prof):
            from .mail_sync import sync
            rep = sync(prof, say=lambda s: None)
            run.parts.append(f"{tag} mail read={rep['read']} events={rep['events']} "
                             f"new_apps={rep['new_apps']} postings_moved={len(rep['pipeline'])} "
                             f"look={rep['look']}")
            run.say(f"{tag}: mail {rep['events']} event(s), {len(rep['pipeline'])} posting(s) moved")

            # What the rules weren't sure of: only flagged records, at most
            # three a cycle, only Groq - usually nothing to ask, so no call at
            # all. See mail_ai.py for exactly what is sent.
            from .mail_ai import review
            r = review(prof, say=lambda s: _log(run.var, s))
            if r["skipped"]:
                run.parts.append(f"{tag} mail-ai skipped {r['skipped']}")
            elif r["asked"]:
                run.parts.append(f"{tag} mail-ai asked={r['asked']} suggested={r['suggested']} "
                                 f"failed={r['failed']} proposals={r['proposals']}")
    except Exception as exc:  # noqa: BLE001
        run.failure(f"{tag} mail", exc)

    if run.stopped(f"{tag} inbox"):
        return True

    # ── dashboard ────────────────────────────────────────────────────────────
    try:
        out = render(prof)
        run.parts.append(f"{tag} dashboard {out.stat().st_size // 1024}KB")
        run.say(f"{tag}: dashboard {out}")
    except Exception as exc:  # noqa: BLE001
        run.failure(f"{tag} dashboard", exc)
    return False
