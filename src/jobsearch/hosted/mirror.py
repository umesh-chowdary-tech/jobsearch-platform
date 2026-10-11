"""var/ kept in Postgres for the hosted service (statestore.py does the work).

Render's free service loses its disk when it sleeps, so the service pulls
var/ - profiles and all, they live in var/_profiles/ there - from Postgres
when it starts, pushes a few seconds after anything changes, sweeps every
couple of minutes for what background jobs wrote, and pushes once more when
it stops. The service is the only writer, so on its own disk is the truth
between pushes; statestore's lock and conflict rules cover the overlap of a
deploy, when the old instance and the new one briefly both run.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from .. import statestore

log = logging.getLogger("jobsearch.mirror")


class Mirror:
    def __init__(self, var: Path, remote: statestore.Remote, delay: float = 3.0, sweep: float = 120.0) -> None:
        self.var = Path(var)
        self.remote = remote
        self.delay = delay
        self.sweep = sweep
        self._dirty = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()          # one push at a time
        self._thread: threading.Thread | None = None

    def start(self) -> statestore.SyncReport:
        rep = statestore.pull(self.var, self.remote)
        log.info("pulled var/ from the database: %s", rep.summary())
        self._thread = threading.Thread(target=self._loop, name="mirror", daemon=True)
        self._thread.start()
        return rep

    def touch(self) -> None:
        """Something changed: push soon."""
        self._dirty.set()

    def push(self) -> statestore.SyncReport | None:
        with self._lock:
            try:
                return statestore.push(self.var, self.remote)
            except Exception:  # noqa: BLE001 - the next touch or sweep tries again
                log.exception("push to the database failed")
                return None

    def forget(self, prefixes: list[str]) -> list[str]:
        """A person's rows, deleted from the database. Raises if it can't -
        the caller keeps the local files until it can."""
        with self._lock:
            return statestore.forget(self.var, self.remote, prefixes)

    def _loop(self) -> None:
        while not self._stop.is_set():
            woke = self._dirty.wait(timeout=self.sweep)
            if self._stop.is_set():
                break
            if woke:
                self._stop.wait(self.delay)    # let a burst of changes land together
            self._dirty.clear()
            self.push()

    def stop(self) -> None:
        self._stop.set()
        self._dirty.set()
        if self._thread:
            self._thread.join(timeout=10)
        self.push()
