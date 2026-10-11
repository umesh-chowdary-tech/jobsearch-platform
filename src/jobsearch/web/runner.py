"""One action at a time, in a thread, with its output captured live.

Everything the app can do already exists as a function that prints as it goes.
So the app calls that same function and captures stdout rather than
reimplementing any of it: there is one code path for `watch`, not two, and the
log you read in the browser is the log the scheduler writes.

One at a time, enforced with a lock. These actions share `var/` and the
per-host rate limiter - two concurrent watch runs would poll every board twice,
race on `seen/*.json`, and the loser would still report success.
"""
from __future__ import annotations

import io
import threading
import traceback
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..models import utcnow

# A long verify prints one line per board; a cycle prints a handful. 4000 is
# far more than any action emits, and bounded so a runaway loop cannot eat the
# process's memory while nobody is watching the page.
MAX_LINES = 4000


@dataclass
class Job:
    id: int
    action: str
    label: str
    started: str
    lines: list[str] = field(default_factory=list)
    done: bool = False
    ok: bool | None = None
    code: int | None = None
    error: str = ""
    finished: str = ""

    def snapshot(self, since: int = 0) -> dict[str, Any]:
        return {
            "id": self.id, "action": self.action, "label": self.label,
            "started": self.started, "done": self.done, "ok": self.ok,
            "code": self.code, "error": self.error, "finished": self.finished,
            "lines": self.lines[since:], "total_lines": len(self.lines),
        }


class _Sink(io.TextIOBase):
    """Collects written text into a job's line buffer as whole lines.

    print() writes the text and the newline separately, so buffering until a
    newline arrives is what keeps one print() as one line in the browser.
    """

    def __init__(self, job: Job, lock: threading.Lock) -> None:
        self._job = job
        self._lock = lock
        self._buf = ""

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        self._buf += s
        if "\n" in self._buf:
            *whole, self._buf = self._buf.split("\n")
            with self._lock:
                for line in whole:
                    if len(self._job.lines) < MAX_LINES:
                        self._job.lines.append(line.rstrip())
        return len(s)

    def flush(self) -> None:
        if self._buf:
            with self._lock:
                if len(self._job.lines) < MAX_LINES:
                    self._job.lines.append(self._buf.rstrip())
            self._buf = ""


class Runner:
    """Holds the current job and a short history of finished ones."""

    def __init__(self, prof: Any) -> None:
        self.prof = prof
        self._gate = threading.Lock()      # guards "is something running"
        self._lines = threading.Lock()     # guards a job's line buffer
        self._seq = 0
        self.current: Job | None = None
        self.history: list[Job] = []

    @property
    def busy(self) -> bool:
        job = self.current
        return job is not None and not job.done

    def start(self, action: str, label: str,
              fn: Callable[[Path, dict[str, Any]], int],
              opts: dict[str, Any]) -> Job | None:
        """Returns the new Job, or None when something is already running."""
        with self._gate:
            if self.busy:
                return None
            self._seq += 1
            job = Job(id=self._seq, action=action, label=label, started=utcnow())
            self.current = job

        threading.Thread(target=self._run, args=(job, fn, opts),
                         name=f"job-{job.id}-{action}", daemon=True).start()
        return job

    def _run(self, job: Job, fn: Callable[[Path, dict[str, Any]], int],
             opts: dict[str, Any]) -> None:
        sink = _Sink(job, self._lines)
        try:
            # redirect_stdout is process-wide, which is safe only because one
            # job runs at a time and the HTTP threads log to stderr.
            with redirect_stdout(sink):
                code = fn(self.prof, opts)
            sink.flush()
            job.code = int(code or 0)
            job.ok = job.code == 0
        except Exception as exc:  # noqa: BLE001 - a failed action is data, not a crash
            sink.flush()
            job.ok = False
            job.code = 1
            job.error = f"{type(exc).__name__}: {exc}"
            with self._lines:
                for line in traceback.format_exc().splitlines()[-12:]:
                    job.lines.append(line)
        finally:
            job.finished = utcnow()
            job.done = True
            self.history.insert(0, job)
            del self.history[12:]

    def job(self, job_id: int | None = None) -> Job | None:
        if job_id is None:
            return self.current
        if self.current and self.current.id == job_id:
            return self.current
        return next((j for j in self.history if j.id == job_id), None)

    def snapshot(self, since: int = 0) -> dict[str, Any]:
        with self._lines:
            cur = self.current.snapshot(since) if self.current else None
        return {
            "busy": self.busy,
            "current": cur,
            "history": [
                {"id": j.id, "action": j.action, "label": j.label,
                 "started": j.started, "finished": j.finished, "ok": j.ok,
                 "error": j.error, "lines": len(j.lines)}
                for j in self.history
            ],
        }
