"""SwitchKaro inside Claude (hosted/mcp.py): the connector's tools for every
member, each signed in as themselves; and the page where they approve it."""
from __future__ import annotations

import json
import shutil

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")
jwt = pytest.importorskip("jwt")
from fastapi.testclient import TestClient  # noqa: E402

from jobsearch import pipeline  # noqa: E402
from jobsearch.hosted import accounts  # noqa: E402
from jobsearch.hosted.app import create_app  # noqa: E402
from jobsearch.hosted.auth import AuthError, Verifier  # noqa: E402
from jobsearch.profile import shared_var  # noqa: E402
from samples import ROOT  # noqa: E402
from test_hosted import ALICE, BOB, SECRET, URL, auth, joined, token  # noqa: E402

BASE = "https://switchkaro.example"
HDR = {"Accept": "application/json, text/event-stream"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "data", tmp_path / "data")
    monkeypatch.setenv("JOBSEARCH_PROFILES", str(tmp_path / "var" / "_profiles"))
    monkeypatch.setenv("JOBSEARCH_PUBLIC_URL", BASE)
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>SwitchKaro</title>", encoding="utf-8")
    app = create_app(tmp_path, Verifier(URL, SECRET), static_dir=web)
    with TestClient(app) as client:              # its lifespan runs the MCP session manager
        yield client, tmp_path


def member(client, root, sub=ALICE, email="alice@example.com"):
    prof = joined(client, root, sub, email)
    shutil.copytree(ROOT / "profiles" / "sample-backend", prof.dir)
    prof.var.mkdir(parents=True, exist_ok=True)
    posting = {"id": "1000001", "title": "Backend Engineer", "company": "Globex", "board": "lever:globex",
               "verdict": "keep", "location": "Pune", "matched": {"Python": "core"}, "coverage": "1/2"}
    (prof.var / "triage-results.json").write_text(json.dumps({"postings": {"1000001": posting}}))
    return prof


def call(client, tok, method, params=None, n=2):
    return client.post("/mcp", headers={**HDR, **auth(tok)},
                       json={"jsonrpc": "2.0", "id": n, "method": method, "params": params or {}})


def test_without_a_token_it_says_where_to_sign_in(env):
    client, _ = env
    r = client.post("/mcp", json=INIT, headers=HDR)
    assert r.status_code == 401
    assert "/.well-known/oauth-protected-resource/mcp" in r.headers["www-authenticate"]
    meta = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert meta["resource"] == BASE + "/mcp" and meta["authorization_servers"] == [f"{URL}/auth/v1"]
    assert client.get("/.well-known/oauth-protected-resource").json()["authorization_servers"] == [f"{URL}/auth/v1"]


def test_a_member_works_on_their_own_jobs_only(env):
    client, root = env
    alice = member(client, root)
    bob = member(client, root, BOB, "bob@example.com")
    (bob.var / "triage-results.json").write_text(json.dumps({"postings": {}}))
    tok = token(ALICE, "alice@example.com", aud="claude-client", client_id="claude-client")
    assert client.post("/mcp", json=INIT, headers={**HDR, **auth(tok)}).status_code == 200
    names = {t["name"] for t in call(client, tok, "tools/list").json()["result"]["tools"]}
    assert {"find_jobs", "today", "set_status", "save_rankings"} <= names
    assert "refresh_jobs" not in names                          # the schedule polls the boards here
    found = call(client, tok, "tools/call", {"name": "find_jobs", "arguments": {}}).json()["result"]
    assert "Globex" in found["content"][0]["text"]
    call(client, tok, "tools/call", {"name": "set_status", "arguments": {"posting_id": "1000001", "status": "shortlisted"}})
    assert pipeline.status(pipeline.load(alice), "1000001") == "shortlisted"
    bobs = call(client, token(BOB, "bob@example.com"), "tools/call", {"name": "find_jobs", "arguments": {}}).json()
    assert "Globex" not in bobs["result"]["content"][0]["text"]


def test_no_member_no_tools(env):
    client, root = env
    r = client.post("/mcp", json=INIT, headers={**HDR, **auth(token(BOB, "bob@example.com"))})
    assert r.status_code == 401                                 # signed in, but never joined
    alice = member(client, root)
    accounts.suspend(shared_var(root), alice.id, True)
    assert client.post("/mcp", json=INIT, headers={**HDR, **auth(token())}).status_code == 401


@pytest.mark.parametrize("over", [{"role": "anon"}, {"role": "service_role"}, {"is_anonymous": True},
                                  {"aud": "someone-else"}, {"iss": "https://other.supabase.co/auth/v1"}])
def test_tokens_that_are_not_a_persons_grant_are_refused(over):
    v = Verifier(URL, SECRET)
    with pytest.raises(AuthError):
        v.verify_claims(token(**over), any_audience=True)


def test_an_apps_grant_may_name_the_app_as_its_audience():
    v = Verifier(URL, SECRET)
    who, claims = v.verify_claims(token(aud="claude-client", client_id="claude-client"), any_audience=True)
    assert who.sub == ALICE and claims["client_id"] == "claude-client"
    with pytest.raises(AuthError):                              # the web app's own check stays as strict
        v.verify(token(aud="claude-client", client_id="claude-client"))


def test_the_consent_page_is_the_web_app(env):
    client, _ = env
    r = client.get("/oauth/consent?authorization_id=abc")
    assert r.status_code == 200 and "<title>SwitchKaro</title>" in r.text
    assert r.headers["cache-control"] == "no-store"


def test_no_connector_without_a_public_address(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "data", tmp_path / "data")
    monkeypatch.delenv("JOBSEARCH_PUBLIC_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    with TestClient(create_app(tmp_path, Verifier(URL, SECRET), static_dir=tmp_path / "none")) as client:
        assert client.post("/mcp", json=INIT, headers=HDR).status_code in (404, 405)
