"""var/ mirrored into Postgres, so the hosted connector remembers.

Render's free web service loses its disk every time it sleeps, which is after
15 quiet minutes. Everything the agent knows - the seen IDs, the triage
results, what each person decided - lives in var/. So the server pulls var/ from
Postgres when it starts and before each tool call, and pushes what changed
after each one that writes.

The rest of the code still reads and writes plain files. This module is the
only part that knows a database exists.

  - One row per file: path, bytes, sha256. Whole files, not records: the
    biggest is triage-results.json at ~3 MB, which Neon's free tier carries
    easily, and it keeps every other module unchanged.
  - `var/.sync.json` holds the sha of each file as of the last sync. That is
    how "changed here" is told apart from "changed there".
  - Both sides changed one file: the remote copy wins and the local copy is
    kept in var/sync-conflicts/, so nothing is lost silently.
  - A file deleted on one side is not deleted on the other: a missing file
    must not wipe the other copy. The one deletion is `forget`, asked for by
    name - a person deleting their account on the hosted service.
  - Pull and push take a Postgres advisory lock, so the PC and the server
    never interleave a sync.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Protocol
from urllib.parse import urlparse

from .models import utcnow

MANIFEST = ".sync.json"
CONFLICTS = "sync-conflicts"
# Discovery runs are 20 MB of history nothing reads back; trials are one-off
# experiments; dashboards are regenerated from the rest. Matched at any depth:
# discovery is under var/_shared/, each dashboard under var/<profile>/.
SKIP_DIRS = ("discovery", "trials", CONFLICTS)
SKIP_FILES = ("dashboard.html", MANIFEST, ".busy.lock", ".busy.json", ".edit.lock")
LOCK_KEY = 0x6A6F6273        # "jobs" - one advisory lock for the whole of var/
# Held for a whole cycle, separately from LOCK_KEY: a sync takes a second and a
# cycle takes minutes, and a chat's tool call must not wait out a cycle. Two
# cycles at once - the PC's schedule and a refresh from the chat - would both
# change seen/*.json, and one side's poll would end up in sync-conflicts/.
CYCLE_KEY = 0x6379636C       # "cycl"


def env_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tracked(var: Path) -> Iterator[tuple[str, Path]]:
    """(posix path relative to var/, file) for every file that is mirrored."""
    var = Path(var)
    if not var.exists():
        return
    for f in sorted(var.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(var).as_posix()
        dirs = rel.split("/")[:-1]
        if set(dirs) & set(SKIP_DIRS) or f.name in SKIP_FILES or f.suffix == ".tmp" or ".lock" in f.name:
            continue
        yield rel, f


class Remote(Protocol):
    """Where the files live. Postgres in production, a dict in tests."""
    def lock(self) -> Any: ...
    def hashes(self) -> dict[str, str]: ...
    def get(self, paths: list[str]) -> dict[str, bytes]: ...
    def put(self, rows: list[tuple[str, bytes, str]]) -> None: ...
    def delete(self, paths: list[str]) -> None: ...
    def cycle_lock(self) -> Any: ...     # context manager yielding True if got


@dataclass
class SyncReport:
    pulled: list[str] = field(default_factory=list)
    pushed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return f"pulled={len(self.pulled)} pushed={len(self.pushed)} conflicts={len(self.conflicts)}"


def _manifest(var: Path) -> dict[str, str]:
    try:
        return json.loads((var / MANIFEST).read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_manifest(var: Path, m: dict[str, str]) -> None:
    var.mkdir(parents=True, exist_ok=True)
    tmp = var / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(m, indent=0, sort_keys=True), encoding="utf-8")
    os.replace(tmp, var / MANIFEST)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".sync.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _pull(var: Path, remote: Remote, rep: SyncReport) -> None:
    m = _manifest(var)
    there = remote.hashes()
    want = [p for p, h in there.items() if m.get(p) != h]
    if not want:
        return
    blobs = remote.get(want)
    for rel, data in blobs.items():
        local = var / rel
        if local.exists():
            mine = _sha(local.read_bytes())
            if mine == there[rel]:
                m[rel] = mine                     # same bytes arrived both ways
                continue
            if rel in m and mine != m[rel]:
                # Changed here and there since the last sync. Keep ours aside.
                stamp = utcnow().replace(":", "").replace("+", "_")
                _write(var / CONFLICTS / f"{rel}.{stamp}", local.read_bytes())
                rep.conflicts.append(rel)
        _write(local, data)
        m[rel] = there[rel]
        rep.pulled.append(rel)
    _save_manifest(var, m)


def _push(var: Path, remote: Remote, rep: SyncReport) -> None:
    m = _manifest(var)
    rows = []
    for rel, f in tracked(var):
        data = f.read_bytes()
        h = _sha(data)
        if m.get(rel) != h:
            rows.append((rel, data, h))
    if rows:
        remote.put(rows)
        for rel, _, h in rows:
            m[rel] = h
            rep.pushed.append(rel)
        _save_manifest(var, m)


def pull(var: Path, remote: Remote) -> SyncReport:
    rep = SyncReport()
    with remote.lock():
        _pull(Path(var), remote, rep)
    return rep


def push(var: Path, remote: Remote) -> SyncReport:
    """Pull first, under the same lock: a push must never overwrite a change
    the other side made since our last pull."""
    rep = SyncReport()
    with remote.lock():
        _pull(Path(var), remote, rep)
        _push(Path(var), remote, rep)
    return rep


def forget(var: Path, remote: Remote, prefixes: list[str]) -> list[str]:
    """Delete every mirrored file under these prefixes from the database and
    from the manifest - before the local files go, so a failure here leaves
    everything in place to try again rather than rows that come back."""
    var = Path(var)
    prefixes = [p.rstrip("/") + "/" for p in prefixes if p.strip("/")]
    if not prefixes:
        return []
    with remote.lock():
        gone = sorted(p for p in remote.hashes() if p.startswith(tuple(prefixes)))
        if gone:
            remote.delete(gone)
        m = _manifest(var)
        kept = {k: v for k, v in m.items() if not k.startswith(tuple(prefixes))}
        if kept != m:
            _save_manifest(var, kept)
    return gone


def status(var: Path, remote: Remote) -> dict[str, list[str]]:
    """What a sync would do, without doing it."""
    var = Path(var)
    m = _manifest(var)
    with remote.lock():
        there = remote.hashes()
    local = {rel: _sha(f.read_bytes()) for rel, f in tracked(var)}
    return {
        "to_pull": sorted(p for p, h in there.items() if m.get(p) != h and local.get(p) != h),
        "to_push": sorted(p for p, h in local.items() if m.get(p) != h and there.get(p) != h),
    }


# ── Postgres ─────────────────────────────────────────────────────────────────

# Run in order, each on its own, whenever a process first connects.
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS var_files (
    path        text PRIMARY KEY,
    content     bytea NOT NULL,
    sha256      text NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
)""",
    # var/ holds everyone's data. Row-level security with no policies leaves
    # the table to its owner - this code's own connection - and refuses every
    # other role. On Supabase that shuts out "anon" and "authenticated", the
    # roles its public key and its signed-in users act as, even if the
    # project's Data API is ever switched on. Nothing changes for the owner.
    "ALTER TABLE var_files ENABLE ROW LEVEL SECURITY",
    """DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON var_files FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON var_files FROM authenticated;
    END IF;
END $$""",
)


class Postgres:
    def __init__(self, url: str) -> None:
        if not url:
            raise ValueError("DATABASE_URL is not set")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if "-pooler." in host:
            # PgBouncer in transaction mode hands each statement any server
            # connection, so a session advisory lock taken on one is released
            # - or kept - on another. Neon's direct URL has no "-pooler".
            raise ValueError("DATABASE_URL is Neon's pooled URL - use the direct one "
                             "(turn off 'Connection pooling' when copying it)")
        if host.endswith(".pooler.supabase.com") and parsed.port == 6543:
            # The same for Supabase's transaction pooler. Its session pooler
            # (port 5432) keeps one connection per client, locks included.
            raise ValueError("DATABASE_URL is Supabase's transaction pooler (port 6543) - "
                             "use the session pooler's URL (port 5432)")
        self.url = url
        self._ready = False

    def _connect(self) -> Any:
        import psycopg                       # only the server and `state` need it
        conn = psycopg.connect(self.url, autocommit=True, connect_timeout=20)
        if not self._ready:
            for statement in SCHEMA:
                conn.execute(statement)
            self._ready = True
        return conn

    class _Lock:
        def __init__(self, pg: "Postgres") -> None:
            self.pg = pg

        def __enter__(self) -> None:
            self.conn = self.pg._connect()
            self.conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
            self.pg._held = self.conn

        def __exit__(self, *exc: Any) -> None:
            try:
                self.conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
            finally:
                self.pg._held = None
                self.conn.close()

    _held: Any = None

    def lock(self) -> "Postgres._Lock":
        return Postgres._Lock(self)

    @contextmanager
    def cycle_lock(self) -> Iterator[bool]:
        conn = self._connect()
        try:
            got = conn.execute("SELECT pg_try_advisory_lock(%s)", (CYCLE_KEY,)).fetchone()[0]
            try:
                yield bool(got)
            finally:
                if got:
                    conn.execute("SELECT pg_advisory_unlock(%s)", (CYCLE_KEY,))
        finally:
            conn.close()

    def _conn(self) -> Any:
        if self._held is None:
            raise RuntimeError("Postgres remote used outside lock()")
        return self._held

    def hashes(self) -> dict[str, str]:
        return dict(self._conn().execute("SELECT path, sha256 FROM var_files").fetchall())

    def get(self, paths: list[str]) -> dict[str, bytes]:
        rows = self._conn().execute(
            "SELECT path, content FROM var_files WHERE path = ANY(%s)", (paths,)).fetchall()
        return {p: bytes(c) for p, c in rows}

    def put(self, rows: list[tuple[str, bytes, str]]) -> None:
        with self._conn().cursor() as cur:
            cur.executemany(
                "INSERT INTO var_files (path, content, sha256, updated_at) VALUES (%s, %s, %s, now()) "
                "ON CONFLICT (path) DO UPDATE SET content = EXCLUDED.content, "
                "sha256 = EXCLUDED.sha256, updated_at = now()",
                rows)


    def delete(self, paths: list[str]) -> None:
        self._conn().execute("DELETE FROM var_files WHERE path = ANY(%s)", (paths,))


def remote_from_env() -> Postgres | None:
    url = env_url()
    return Postgres(url) if url else None
