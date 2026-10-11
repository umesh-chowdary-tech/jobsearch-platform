"""The hosted service's var/ kept in Postgres (hosted/mirror.py), and the one
deletion statestore does: forgetting a person who deleted their account."""
from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import time

import pytest

from jobsearch import statestore

pytest.importorskip("fastapi")
jwt = pytest.importorskip("jwt")
from fastapi.testclient import TestClient  # noqa: E402

from jobsearch.hosted import accounts  # noqa: E402
from jobsearch.hosted.app import create_app  # noqa: E402
from jobsearch.hosted.auth import Verifier  # noqa: E402
from jobsearch.hosted.mirror import Mirror  # noqa: E402
from samples import ROOT  # noqa: E402

URL = "https://proj-test.supabase.co"
SECRET = "test-only-jwt-secret-0123456789abcdef"
ALICE = "11111111-1111-4111-8111-111111111111"


class FakeRemote:
    """Strict like Postgres: every read and write inside lock()."""
    def __init__(self, fail_delete: bool = False) -> None:
        self.rows: dict[str, tuple[bytes, str]] = {}
        self.inside = False
        self.fail_delete = fail_delete

    @contextlib.contextmanager
    def lock(self):
        self.inside = True
        try:
            yield
        finally:
            self.inside = False

    def _check(self):
        assert self.inside, "used outside lock()"

    def hashes(self):
        self._check()
        return {p: h for p, (_, h) in self.rows.items()}

    def get(self, paths):
        self._check()
        return {p: self.rows[p][0] for p in paths if p in self.rows}

    def put(self, rows):
        self._check()
        for p, data, h in rows:
            self.rows[p] = (data, h)

    def delete(self, paths):
        self._check()
        if self.fail_delete:
            raise ConnectionError("database unreachable")
        for p in paths:
            self.rows.pop(p, None)

    @contextlib.contextmanager
    def cycle_lock(self):
        yield True

    def seed(self, path: str, text: str) -> None:
        data = text.encode("utf-8")
        self.rows[path] = (data, hashlib.sha256(data).hexdigest())

    def text(self, path: str) -> str | None:
        row = self.rows.get(path)
        return row[0].decode("utf-8") if row else None


def until(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ── statestore.forget ────────────────────────────────────────────────────────

def test_forget_deletes_only_under_the_prefixes_there_and_in_the_manifest(tmp_path):
    remote = FakeRemote()
    for p in ("u-aaa/pipeline.json", "u-aaa/setup/resume.txt", "_profiles/u-aaa/skills.yaml",
              "u-aaab/pipeline.json", "_shared/accounts.json"):
        remote.seed(p, p)
    statestore.pull(tmp_path, remote)
    gone = statestore.forget(tmp_path, remote, ["u-aaa", "_profiles/u-aaa/"])
    assert gone == ["_profiles/u-aaa/skills.yaml", "u-aaa/pipeline.json", "u-aaa/setup/resume.txt"]
    assert set(remote.rows) == {"u-aaab/pipeline.json", "_shared/accounts.json"}     # a longer id is someone else
    manifest = json.loads((tmp_path / statestore.MANIFEST).read_text())
    assert set(manifest) == {"u-aaab/pipeline.json", "_shared/accounts.json"}


@pytest.mark.parametrize("prefixes", [[], [""], ["/"]])
def test_forget_with_no_prefix_deletes_nothing(tmp_path, prefixes):
    remote = FakeRemote()
    remote.seed("u-aaa/pipeline.json", "x")
    assert statestore.forget(tmp_path, remote, prefixes) == []
    assert remote.rows


def test_supabases_transaction_pooler_is_refused_and_its_session_pooler_is_not():
    with pytest.raises(ValueError, match="session pooler"):
        statestore.Postgres("postgresql://u:p@aws-0-ap-south-1.pooler.supabase.com:6543/postgres")
    statestore.Postgres("postgresql://u:p@aws-0-ap-south-1.pooler.supabase.com:5432/postgres")


# ── the mirror ───────────────────────────────────────────────────────────────

def test_the_mirror_pulls_at_start_pushes_after_a_change_and_on_stop(tmp_path):
    remote = FakeRemote()
    remote.seed("_shared/accounts.json", '{"users": {}}')
    m = Mirror(tmp_path, remote, delay=0.01, sweep=60)
    m.start()
    try:
        assert (tmp_path / "_shared" / "accounts.json").read_text() == '{"users": {}}'
        (tmp_path / "u-a").mkdir()
        (tmp_path / "u-a" / "pipeline.json").write_text("{}")
        m.touch()
        assert until(lambda: remote.text("u-a/pipeline.json") == "{}")
        (tmp_path / "u-a" / "pipeline.json").write_text('{"x": 1}')
    finally:
        m.stop()                                            # no touch: stopping pushes anyway
    assert remote.text("u-a/pipeline.json") == '{"x": 1}'


# ── the service with a database ──────────────────────────────────────────────

def _token(sub=ALICE, email="alice@example.com"):
    now = int(time.time())
    return jwt.encode({"sub": sub, "email": email, "aud": "authenticated", "iss": f"{URL}/auth/v1",
                       "iat": now, "exp": now + 600}, SECRET, algorithm="HS256")


@pytest.fixture
def remote_with_alice(tmp_path, monkeypatch):
    """A database already holding Alice's account and profile, and a fresh
    disk - the service just restarted."""
    shutil.copytree(ROOT / "data", tmp_path / "data")
    monkeypatch.setenv("JOBSEARCH_PROFILES", str(tmp_path / "var" / "_profiles"))
    remote = FakeRemote()
    pid = accounts.profile_id(ALICE)
    remote.seed("_shared/accounts.json", json.dumps(
        {"users": {ALICE: {"email": "alice@example.com", "profile": pid, "invite": "AAAA-BBBB", "joined": "x"}},
         "invites": {"AAAA-BBBB": {"used_by": ALICE}}}))
    for f in (ROOT / "profiles" / "sample-backend").glob("*.yaml"):
        remote.seed(f"_profiles/{pid}/{f.name}", f.read_text(encoding="utf-8"))
    posting = {"id": "5000001", "title": "Backend Engineer", "company": "Globex", "board": "greenhouse:globex",
               "verdict": "keep", "location": "Pune", "matched": {}, "coverage": "0/0", "ai": {"fit": "strong"}}
    remote.seed(f"{pid}/triage-results.json", json.dumps({"postings": {"5000001": posting}}))
    return tmp_path, remote, pid


def test_after_a_restart_everything_comes_back_from_the_database(remote_with_alice):
    root, remote, pid = remote_with_alice
    app = create_app(root, Verifier(URL, SECRET), static_dir=root / "none", remote=remote, mirror_delay=0.01)
    a = {"Authorization": f"Bearer {_token()}"}
    with TestClient(app) as client:
        me = client.get("/api/me", headers=a).json()
        assert me["member"] and me["profile_ready"]
        assert [r["id"] for r in client.get("/api/postings", headers=a).json()] == ["5000001"]
        assert client.post("/api/postings/5000001/status", json={"status": "applied"}, headers=a).status_code == 200
        assert until(lambda: '"applied"' in (remote.text(f"{pid}/pipeline.json") or ""))


def test_deleting_an_account_deletes_its_rows_in_the_database(remote_with_alice):
    root, remote, pid = remote_with_alice
    app = create_app(root, Verifier(URL, SECRET), static_dir=root / "none", remote=remote, mirror_delay=0.01)
    a = {"Authorization": f"Bearer {_token()}"}
    with TestClient(app) as client:
        r = client.post("/api/account/delete", json={"confirm": "delete my account"}, headers=a)
        assert r.status_code == 200
        assert not [p for p in remote.rows if pid in p]
        assert until(lambda: ALICE not in (remote.text("_shared/accounts.json") or ALICE))
    assert not (root / "var" / pid).exists()


def test_when_the_database_cant_delete_nothing_is_deleted_here_either(remote_with_alice):
    root, remote, pid = remote_with_alice
    remote.fail_delete = True
    app = create_app(root, Verifier(URL, SECRET), static_dir=root / "none", remote=remote, mirror_delay=0.01)
    a = {"Authorization": f"Bearer {_token()}"}
    with TestClient(app) as client:
        r = client.post("/api/account/delete", json={"confirm": "delete my account"}, headers=a)
        assert r.status_code == 503 and "try again" in r.json()["error"]
        assert (root / "var" / pid / "triage-results.json").exists()
        assert client.get("/api/me", headers=a).json()["member"] is True
