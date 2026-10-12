"""The hosted service: who gets in, and that each person sees only their own.

Tokens here are signed with a test secret, the way an older Supabase project
signs them; the asymmetric (JWKS) path is checked with a key made in the test.
"""
from __future__ import annotations

import io
import json
import shutil
import time
import zipfile

import pytest

pytest.importorskip("fastapi")
jwt = pytest.importorskip("jwt")
from fastapi.testclient import TestClient  # noqa: E402

from jobsearch.hosted import accounts  # noqa: E402
from jobsearch.hosted.accounts import CONSENT_VERSION  # noqa: E402
from jobsearch.hosted.app import FILE_CSP, create_app  # noqa: E402
from jobsearch.hosted.auth import AuthError, Verifier  # noqa: E402
from jobsearch.profile import Profile, shared_var  # noqa: E402
from samples import ROOT  # noqa: E402

URL = "https://proj-test.supabase.co"
SECRET = "test-only-jwt-secret-0123456789abcdef"
ALICE = "11111111-1111-4111-8111-111111111111"
BOB = "22222222-2222-4222-8222-222222222222"


def token(sub=ALICE, email="alice@example.com", secret=SECRET, alg="HS256", **over):
    now = int(time.time())
    claims = {"sub": sub, "email": email, "aud": "authenticated", "iss": f"{URL}/auth/v1",
              "iat": now, "exp": now + 600, **over}
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, secret, algorithm=alg)


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "data", tmp_path / "data")
    monkeypatch.setenv("JOBSEARCH_PROFILES", str(tmp_path / "var" / "_profiles"))
    monkeypatch.setenv("JOBSEARCH_ADMINS", "boss@example.com")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-anon-key")
    app = create_app(tmp_path, Verifier(URL, SECRET), static_dir=tmp_path / "no-web-build",
                     cron_secret="cron-test-secret")
    return TestClient(app), tmp_path


def joined(client, root, sub=ALICE, email="alice@example.com"):
    code = accounts.create_invites(shared_var(root), 1)[0]
    r = client.post("/api/invite", json={"code": code, "consent": CONSENT_VERSION}, headers=auth(token(sub, email)))
    assert r.status_code == 200 and r.json()["member"], r.text
    return Profile(root, accounts.profile_id(sub))


# ── who gets in ──────────────────────────────────────────────────────────────

def test_health_and_config_are_open_and_hold_no_secret(env):
    client, _ = env
    assert client.get("/api/health").json() == {"ok": True}
    cfg = client.get("/api/config").json()
    assert cfg["auth"] == "supabase" and cfg["supabase_anon_key"] == "public-anon-key"
    assert SECRET not in json.dumps(cfg)


@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer"}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer not-a-jwt"},
    auth(token(secret="some-other-secret-0123456789abcdef")),
    auth(token(exp=int(time.time()) - 5)),
    auth(token(aud="anon")),
    auth(token(iss="https://evil.example/auth/v1")),
    auth(token(sub=None)),
])
def test_a_missing_bad_or_stale_token_gets_nothing(env, headers):
    client, _ = env
    assert client.get("/api/me", headers=headers).status_code == 401
    assert client.get("/api/today", headers=headers).status_code == 401


def test_a_shared_secret_token_is_refused_where_only_published_keys_are_trusted():
    """Algorithm confusion: with no shared secret configured, an HS256 token is
    refused outright - it can't be checked against a public key instead."""
    v = Verifier(URL, "", jwks_client=object())
    with pytest.raises(AuthError):
        v.verify(token())


def test_an_asymmetric_token_is_checked_against_the_published_key():
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())

    class Jwks:
        def get_signing_key_from_jwt(self, _tok):
            return type("K", (), {"key": key.public_key()})()

    v = Verifier(URL, "", jwks_client=Jwks())
    assert v.verify(token(secret=key, alg="ES256")).sub == ALICE
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(AuthError):
        v.verify(token(secret=other, alg="ES256"))


def test_signed_in_without_an_invite_reaches_only_me_and_invite(env):
    client, _ = env
    me = client.get("/api/me", headers=auth(token())).json()
    assert me == {"email": "alice@example.com", "member": False, "profile_ready": False, "admin": False,
                  "consent": None}
    r = client.get("/api/today", headers=auth(token()))
    assert r.status_code == 403 and r.json()["join"] is True


def test_an_invite_is_used_once(env):
    client, root = env
    code = accounts.create_invites(shared_var(root), 1)[0]
    yes = {"consent": CONSENT_VERSION}
    assert client.post("/api/invite", json={"code": "WRNG-CODE", **yes}, headers=auth(token())).status_code == 400
    assert client.post("/api/invite", json={"code": code.lower().replace("-", " "), **yes},
                       headers=auth(token())).json()["member"]                       # typed loosely is fine
    assert client.post("/api/invite", json={"code": code, **yes}, headers=auth(token())).status_code == 200
    r = client.post("/api/invite", json={"code": code, **yes}, headers=auth(token(BOB, "bob@example.com")))
    assert r.status_code == 400 and "already been used" in r.json()["error"]


def test_a_profile_is_named_from_the_sign_in_id_never_the_email(env):
    client, root = env
    prof = joined(client, root)
    assert prof.id.startswith("u-") and "alice" not in prof.id
    row = accounts.user(shared_var(root), ALICE)
    assert row["profile"] == prof.id and row["email"] == "alice@example.com"


# ── each person sees only their own ──────────────────────────────────────────

def _postings(prof, pid="3000001"):
    prof.var.mkdir(parents=True, exist_ok=True)
    p = {"id": pid, "title": "Backend Engineer", "company": "Globex", "board": "greenhouse:globex",
         "verdict": "keep", "location": "Pune", "matched": {"Python": "core"}, "coverage": "1/1",
         "ai": {"fit": "strong", "why": "fits", "by": "Claude"}}
    (prof.var / "triage-results.json").write_text(json.dumps({"postings": {pid: p}}))


def test_one_person_never_sees_anothers_postings_or_files(env):
    client, root = env
    alice = joined(client, root)
    joined(client, root, BOB, "bob@example.com")
    _postings(alice)
    (alice.var / "resumes").mkdir()
    (alice.var / "resumes" / "v1.txt").write_text("alice's resume", encoding="utf-8")
    a, b = auth(token()), auth(token(BOB, "bob@example.com"))
    assert [r["id"] for r in client.get("/api/postings", headers=a).json()] == ["3000001"]
    assert client.get("/api/postings", headers=b).json() == []
    assert client.get("/api/postings/3000001", headers=b).status_code == 404
    assert client.post("/api/postings/3000001/status", json={"status": "skipped"}, headers=b).status_code == 404
    assert client.get("/files/resumes/v1.txt", headers=b).status_code == 404
    r = client.get("/files/resumes/v1.txt", headers=a)
    assert r.status_code == 200 and r.text == "alice's resume" and r.headers["content-security-policy"] == FILE_CSP
    assert client.get("/files/resumes/..%2F..%2Ftriage-results.json", headers=a).status_code == 404


def test_the_hosted_scope_holds_on_the_service(env):
    client, root = env
    joined(client, root)
    a = auth(token())
    assert client.get("/api/system", headers=a).status_code == 404
    assert client.get("/api/applications", headers=a).status_code == 404
    assert client.post("/api/run", json={"action": "cycle"}, headers=a).status_code == 400
    assert client.post("/api/schedule/pause", json={"hours": 1}, headers=a).status_code == 404


def test_setup_runs_on_the_service_for_the_signed_in_person(env):
    client, root = env
    prof = joined(client, root)
    a = auth(token())
    assert client.get("/api/setup", headers=a).json()["status"] == "new"
    r = client.post("/api/setup/upload", content=b"Alice Example\nPune, India\nSoftware Engineer, Globex, 2021 - present\n",
                    headers={**a, "Content-Type": "text/plain"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "uploaded" and (prof.var / "setup" / "resume.txt").exists()
    assert client.post("/api/setup/upload", content=b"x", headers={**a, "Content-Type": "image/png"}).status_code == 415


def test_requests_too_large_are_refused_before_they_are_read(env):
    client, root = env
    joined(client, root)
    a = auth(token())
    assert client.post("/api/setup/upload", content=b"x" * (5 * 1024 * 1024 + 1),
                       headers={**a, "Content-Type": "text/plain"}).status_code == 413
    assert client.post("/api/postings/x/status", content=b"{" + b" " * 70_000 + b"}",
                       headers={**a, "Content-Type": "application/json"}).status_code == 413
    assert client.post("/api/postings/x/status", content=b"status=skipped",
                       headers={**a, "Content-Type": "application/x-www-form-urlencoded"}).status_code == 415


# ── export and delete ────────────────────────────────────────────────────────

def test_export_holds_everything_kept_for_that_person_and_nothing_else(env):
    client, root = env
    alice, bob = joined(client, root), joined(client, root, BOB, "bob@example.com")
    _postings(alice)
    _postings(bob, "4000001")
    r = client.get("/api/account/export", headers=auth(token()))
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "data/triage-results.json" in names
    assert "4000001" not in zipfile.ZipFile(io.BytesIO(r.content)).read("data/triage-results.json").decode()


def test_delete_removes_the_person_and_everything_kept_for_them(env):
    client, root = env
    alice = joined(client, root)
    _postings(alice)
    a = auth(token())
    assert client.post("/api/account/delete", json={"confirm": "yes"}, headers=a).status_code == 400
    assert alice.var.exists()
    assert client.post("/api/account/delete", json={"confirm": "Delete my account"}, headers=a).json() == {"deleted": True}
    assert not alice.var.exists() and not alice.dir.exists()
    assert client.get("/api/me", headers=a).json()["member"] is False
    inv = accounts.load(shared_var(root))["invites"]
    assert [v["used_by"] for v in inv.values()] == ["deleted"]          # spent, and no link back


# ── admins and the cron job ──────────────────────────────────────────────────

def test_only_admins_see_people_and_make_invites(env):
    client, root = env
    joined(client, root)
    assert client.get("/api/admin/overview", headers=auth(token())).status_code == 403
    boss = auth(token("33333333-3333-4333-8333-333333333333", "boss@example.com"))
    o = client.get("/api/admin/overview", headers=boss).json()
    assert [p["email"] for p in o["people"]] == ["alice@example.com"] and o["invites_used"] == 1
    assert o["people"][0]["month"] == {"requests": 0, "tokens": 0, "usd": 0.0}
    codes = client.post("/api/admin/invites", json={"n": 2, "note": "friends"}, headers=boss).json()["codes"]
    assert len(codes) == 2 and all(len(c) == 9 and c[4] == "-" for c in codes)
    assert client.post("/api/admin/invites", json={"n": 0}, headers=boss).status_code == 400


def test_the_cycle_starts_only_for_the_cron_secret(env, monkeypatch):
    client, _ = env
    ran = []
    monkeypatch.setattr("jobsearch.cycle.run_cycle", lambda root, quiet, lock: ran.append(lock))
    assert client.post("/api/cron/cycle").status_code == 404
    assert client.post("/api/cron/cycle", headers={"X-Cron-Secret": "wrong"}).status_code == 404
    assert client.post("/api/cron/cycle", headers={"X-Cron-Secret": "cron-test-secret"}).status_code == 202
    client.app.state.service.cycle.join(5)
    assert ran == [True]


def test_every_response_carries_the_security_headers(env):
    client, _ = env
    h = client.get("/api/health").headers
    assert "connect-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert h["cache-control"] == "no-store" and h["referrer-policy"] == "no-referrer"


# ── dev sign-in ──────────────────────────────────────────────────────────────

def test_dev_sign_in_works_only_in_dev_mode(env, tmp_path, monkeypatch):
    client, _ = env
    r = client.post("/api/dev/sign-in", json={"email": "a@example.com"})
    assert r.status_code == 401 and "token" not in r.json()            # no such thing on a real service
    with pytest.raises(ValueError):
        Verifier(URL, "", dev=True)
    dev = TestClient(create_app(tmp_path, Verifier(dev=True), static_dir=tmp_path / "none"))
    assert dev.get("/api/config").json()["auth"] == "dev"
    assert dev.post("/api/dev/sign-in", json={"email": "nope"}).status_code == 400
    tok = dev.post("/api/dev/sign-in", json={"email": "Dev@Example.com"}).json()["token"]
    assert dev.get("/api/me", headers=auth(tok)).json()["email"] == "dev@example.com"
    # The same email signs back into the same account.
    again = dev.post("/api/dev/sign-in", json={"email": "dev@example.com"}).json()["token"]
    assert jwt.decode(again, options={"verify_signature": False})["sub"] == \
        jwt.decode(tok, options={"verify_signature": False})["sub"]


def test_invites_from_the_command_line(tmp_path, capsys, monkeypatch):
    from jobsearch import cli
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    assert cli.main(["invites", "create", "2", "--note", "beta"]) == 0
    codes = capsys.readouterr().out.split()
    assert len(codes) == 2 and (tmp_path / "var" / "_shared" / "accounts.json").exists()
    assert cli.main(["invites", "list"]) == 0
    out = capsys.readouterr().out
    assert codes[0] in out and "unused" in out and "0 account(s)" in out


def test_the_dev_email_check_takes_no_time_on_a_long_input():
    from jobsearch.hosted.app import plausible_email
    assert plausible_email("a@example.com") and not plausible_email("a@example") and not plausible_email("a b@x.com")
    started = time.time()
    assert not plausible_email("a@" + "b" * 60_000)          # CodeQL: a backtracking regex was here
    assert time.time() - started < 0.1


# ── consent ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("consent", [None, "", "2020-01-01", True])
def test_joining_needs_agreement_to_the_current_notice(env, consent):
    client, root = env
    code = accounts.create_invites(shared_var(root), 1)[0]
    body = {"code": code} if consent is None else {"code": code, "consent": consent}
    r = client.post("/api/invite", json=body, headers=auth(token()))
    assert r.status_code == 400 and "agree" in r.json()["error"]
    assert accounts.load(shared_var(root))["invites"][code]["used_by"] is None      # the code isn't spent


def test_which_notice_was_agreed_to_and_when_is_kept(env):
    client, root = env
    joined(client, root)
    consent = client.get("/api/me", headers=auth(token())).json()["consent"]
    assert consent["version"] == CONSENT_VERSION and consent["at"]
    assert client.get("/api/config").json()["consent_version"] == CONSENT_VERSION


def test_the_notice_the_page_and_the_server_name_one_version():
    """Change the privacy notice, and all three must move together - or a
    person would be recorded as agreeing to text they never saw."""
    import re
    web = ROOT / "web" / "src"
    page = re.search(r'LEGAL_VERSION = "([^"]+)"', (web / "pages" / "Legal.tsx").read_text(encoding="utf-8")).group(1)
    for doc in ("privacy.md", "terms.md"):
        text = (web / "legal" / doc).read_text(encoding="utf-8")
        assert f"Version {CONSENT_VERSION}." in text, doc
    assert page == CONSENT_VERSION
