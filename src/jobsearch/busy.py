"""One writer at a time across processes: the scheduled cycle and the app.

The app's own lock (web/runner.py) only covers its own process. The 15-minute
cycle is a separate Windows task, so a "Run watch" click mid-cycle would poll
every board twice and race on `seen/*.json` - the loser still reporting
success.

This is an OS lock on `var/.busy.lock`, not a marker file. A process that dies
holding it releases it with its handle, so there is no stale-lock cleanup and
no "is that PID still alive?" guessing. On Windows that release is
asynchronous - moments after the process exits, not at the same instant - so
a caller refused right after a crash just tries again. Who holds it, and since when, is
written beside it in `var/.busy.json` for display only - the lock is the truth.

The holder also writes a heartbeat there every BEAT seconds. A laptop asleep
mid-cycle froze a cycle for 14 hours, then for 7, and the app said "started
14 h ago" as if it had been working all along. A gap between heartbeats far
longer than BEAT is time the process wasn't running; it is added up as
`paused_s`, so the app can say how long the PC slept and how long it ran.
"""
from __future__ import annotations

import functools
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .models import utcnow

if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _paths(var: Path) -> tuple[Path, Path]:
    var = Path(var)
    var.mkdir(parents=True, exist_ok=True)
    return var / ".busy.lock", var / ".busy.json"


BEAT = 30.0                 # seconds between heartbeats
ASLEEP = 3 * BEAT           # a gap longer than this between beats: not running
READ_TRIES = 10             # holder()'s reads of a file being rewritten, 10 ms apart


def write_atomic(path: Path, info: dict[str, Any]) -> None:
    """Whole or not at all: `holder()` may read mid-write, and a plain
    write_text truncates first, so it read an empty file. Windows refuses to
    replace a file another process has open, so a reader caught mid-read gets
    a moment to finish, then a plain write (as rank._write does)."""
    text = json.dumps(info)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.02)
    path.write_text(text, encoding="utf-8")
    tmp.unlink(missing_ok=True)


class _Heartbeat(threading.Thread):
    """Rewrites the info file every BEAT seconds with "beat" (now) and
    "paused_s" (the gaps, by wall clock, the process didn't run through)."""

    def __init__(self, path: Path, info: dict[str, Any]) -> None:
        super().__init__(daemon=True, name="busy-heartbeat")
        self.path, self.info, self.halt = path, info, threading.Event()

    def run(self) -> None:
        last = time.time()
        while not self.halt.wait(BEAT):
            now = time.time()
            if now - last > ASLEEP:
                self.info["paused_s"] = round(self.info.get("paused_s", 0) + (now - last - BEAT))
            last = now
            self.info["beat"] = utcnow()
            try:
                write_atomic(self.path, self.info)
            except OSError:
                pass


@contextmanager
def busy(var: Path, what: str) -> Iterator[bool]:
    """`with busy(var, "watch") as got:` - `got` is False when someone else
    holds it; the body then decides what "skip" means for it."""
    lock_path, info_path = _paths(var)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT)
    got = _try_lock(fd)
    heart = None
    try:
        if got:
            now = utcnow()
            info = {"what": what, "pid": os.getpid(), "since": now, "beat": now, "paused_s": 0}
            write_atomic(info_path, info)
            heart = _Heartbeat(info_path, info)
            heart.start()
        yield got
    finally:
        if heart:
            heart.halt.set()
            heart.join(timeout=5)
        if got:
            try:
                info_path.unlink(missing_ok=True)
                (info_path.parent / STOP).unlink(missing_ok=True)
            finally:
                _unlock(fd)
        os.close(fd)


# ── stop: asked from the app, honoured by whoever holds the lock ─────────────
# The holder may be another process (the scheduled cycle), so this is a file,
# not a flag. It names the run it is for by its "since", so a request that
# lands as one run ends never stops the next. Nothing is killed: the run
# checks between steps, between boards and before each model request, the
# points where everything before is already saved.

STOP = ".stop.json"


def request_stop(var: Path) -> dict[str, Any] | None:
    """Ask the current run to stop at its next safe point. Returns who was
    asked, or None when nothing is running."""
    who = holder(var)
    if not who:
        return None
    write_atomic(Path(var) / STOP, {"since": who.get("since"), "at": utcnow()})
    return who


def stop_requested(var: Path) -> bool:
    var = Path(var)
    try:
        ask = json.loads((var / STOP).read_text(encoding="utf-8"))
        info = json.loads((var / ".busy.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return ask.get("since") is not None and ask.get("since") == info.get("since")


class EditTimeout(RuntimeError):
    pass


_edit_thread_lock = threading.RLock()
_edit_depth = 0


@contextmanager
def editing(var: Path, timeout: float = 15.0) -> Iterator[None]:
    """Hold while loading, changing and saving applications.json or
    pipeline.json - short, and waited for rather than refused.

    `busy` is the wrong lock for this: a click in the app must not wait out a
    whole cycle. But without any lock, a "Looks right" landing between the
    inbox sync's load and save was overwritten - or overwrote the sync's new
    events, whose mail the saved inbox position then never reads again.

    Re-entrant in one thread (the sync moves postings while holding it);
    other threads and processes wait their turn."""
    global _edit_depth
    deadline = time.monotonic() + timeout
    if not _edit_thread_lock.acquire(timeout=timeout):       # immediate for the holder
        raise EditTimeout(f"another thread kept {var} locked for {timeout:.0f}s")
    try:
        if _edit_depth:
            _edit_depth += 1
            try:
                yield
            finally:
                _edit_depth -= 1
            return
        var = Path(var)
        var.mkdir(parents=True, exist_ok=True)
        fd = os.open(var / ".edit.lock", os.O_RDWR | os.O_CREAT)
        try:
            while not _try_lock(fd):
                if time.monotonic() > deadline:
                    raise EditTimeout(f"another process kept {var} locked for {timeout:.0f}s")
                time.sleep(0.05)
            _edit_depth = 1
            try:
                yield
            finally:
                _edit_depth = 0
                _unlock(fd)
        finally:
            os.close(fd)
    finally:
        _edit_thread_lock.release()


def locked_edit(fn: Callable[..., Any]) -> Callable[..., Any]:
    """For a function whose first argument is a Profile and which loads,
    changes and saves a file in that profile's var/."""
    @functools.wraps(fn)
    def wrapper(prof: Any, *args: Any, **kwargs: Any) -> Any:
        with editing(prof.var):
            return fn(prof, *args, **kwargs)
    return wrapper


def holder(var: Path) -> dict[str, Any] | None:
    """Who is busy right now, or None. Probes the lock rather than trusting
    the info file, which a killed process can leave behind."""
    lock_path, info_path = _paths(var)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT)
    try:
        if _try_lock(fd):
            _unlock(fd)
            return None
    finally:
        os.close(fd)
    # The lock is held, so the info file exists and is about to be whole: a
    # read that lands while the heartbeat replaces it (Windows refuses the
    # open) or while its fallback plain write is mid-way (an empty or cut
    # file) is retried, not reported as "another process".
    for attempt in range(READ_TRIES):
        try:
            return json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            if attempt + 1 < READ_TRIES:
                time.sleep(0.01)
    return {"what": "another process", "pid": None, "since": None}
