"""The local app's server. The security properties are the point: this process
holds API keys and personal data, and a browser will happily send requests to
127.0.0.1 on behalf of any page that asks."""
from __future__ import annotations

import http.client
import json
import re
import shutil
import socket
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from jobsearch import pipeline
from jobsearch.assemble import Variant, emittable, load_store
from jobsearch.busy import busy
from jobsearch.web import server as srv
from samples import make_root

ROOT = Path(__file__).resolve().parents[1]
SECRET = "sk-test-DO-NOT-LEAK-7f3a"


@pytest.fixture
def app_root(tmp_path):
    prof = make_root(tmp_path)
    var = prof.var
    (var / "resumes").mkdir(parents=True)
    store = load_store(prof)
    posting = {"id": "1000001", "title": "Senior Backend Engineer <script>alert(1)</script>",
               "company": "stripe", "board": "greenhouse:stripe", "verdict": "keep",
               "url": "https://job-boards.greenhouse.io/stripe/jobs/1000001",
               "location": "Bangalore", "matched": {"Python": "core"}, "coverage": "1/2",
               "ai": {"fit": "strong", "why": "fits"}, "description": "text"}
    (var / "triage-results.json").write_text(json.dumps({"postings": {"1000001": posting}}))
    lib = [Variant(id="fullstack", angle="fullstack",
                   emits=[e["id"] for e in store["entries"] if emittable(e)],
                   sent_to=[{"posting_id": "1000001", "date": "2026-09-24"}]).to_dict()]
    (var / "resume-variants.json").write_text(json.dumps(lib))
    (var / "resumes" / "fullstack.txt").write_text("resume text", encoding="utf-8")
    (tmp_path / ".env").write_text(f"GROQ_API_KEY={SECRET}\n", encoding="utf-8")
    return prof


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(app_root, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    port = _free_port()
    app = srv.App(app_root, port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), srv.make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app
    httpd.shutdown()
    httpd.server_close()
    monkeypatch.delenv("GROQ_API_KEY", raising=False)


def req(app, method, path, body=None, host=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    h = {"Host": host or f"127.0.0.1:{app.port}", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    return r.status, dict(r.getheaders()), raw


def jget(app, path):
    status, _, raw = req(app, "GET", path)
    return status, json.loads(raw)


# ── who can talk to it ───────────────────────────────────────────────────────

def test_the_page_is_served_with_a_strict_policy(live):
    status, h, raw = req(live, "GET", "/")
    assert status == 200 and b"<title>Job search</title>" in raw
    assert "script-src 'self'" in h["Content-Security-Policy"]
    assert h["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("host", ["evil.example:8765", "attacker.test", "192.168.1.5"])
def test_a_foreign_host_header_is_refused(live, host):
    """DNS rebinding: a page on attacker.test re-points its name at 127.0.0.1."""
    status, _, _ = req(live, "GET", "/api/postings", host=host)
    assert status == 421


def test_localhost_by_name_is_accepted(live):
    status, _, _ = req(live, "GET", "/api/overview", host=f"localhost:{live.port}")
    assert status == 200


def test_a_cross_site_post_is_refused(live):
    status, _, raw = req(live, "POST", "/api/postings/1000001/status", {"status": "skipped"},
                         headers={"Origin": "https://attacker.test"})
    assert status == 403
    assert pipeline.status(pipeline.load(live.prof), "1000001") == "new"


def test_a_form_post_is_refused(live):
    """A plain HTML form can POST cross-site without a preflight - but not JSON."""
    c = http.client.HTTPConnection("127.0.0.1", live.port, timeout=10)
    c.request("POST", "/api/postings/1000001/status", body="status=skipped",
              headers={"Host": f"127.0.0.1:{live.port}",
                       "Content-Type": "application/x-www-form-urlencoded"})
    assert c.getresponse().status == 415


def test_reads_do_not_change_state(live):
    status, _, _ = req(live, "GET", "/api/postings/1000001/status")
    assert status == 404
    assert not pipeline.path(live.prof).exists()


# ── what it will serve ───────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/files/resumes/../../.env",
    "/files/resumes/..%2F..%2F.env",
    "/files/manual/../../data/master-store.yaml",
    "/files/resumes/../../../profiles/sample-backend/master-store.yaml",
    "/files/other/fullstack.txt",
    "/static/../server.py",
    "/static/..%2Fserver.py",
    "/.env",
])
def test_nothing_outside_the_resume_folders_is_served(live, path):
    status, _, raw = req(live, "GET", path)
    assert status == 404
    assert SECRET.encode() not in raw


def test_a_resume_file_is_served_with_its_own_locked_down_policy(live):
    status, h, raw = req(live, "GET", "/files/resumes/fullstack.txt")
    assert status == 200 and raw == b"resume text"
    assert h["Content-Security-Policy"].startswith("default-src 'none'")


def test_keys_never_reach_the_browser(live):
    status, data = jget(live, "/api/system")
    assert status == 200
    blob = json.dumps(data)
    assert SECRET not in blob
    groq = next(m for m in data["models"] if m["name"] == "groq")
    # "today": requests and tokens used against data/llm-limits.yaml - counts, no key.
    assert groq["configured"] is True and set(groq) == {"name", "label", "model", "configured", "today"}


def test_third_party_text_is_returned_as_data_not_markup(live):
    """The server does not sanitise titles; it never renders them. The page
    inserts every value as text - held here by counting innerHTML uses."""
    status, data = jget(live, "/api/postings/1000001")
    assert "<script>" in data["title"]                 # passed through untouched, as data
    js = (srv.STATIC / "app.js").read_text(encoding="utf-8")
    assert len(re.findall(r"\.innerHTML\s*=", js)) == 1   # the server-escaped gap chart only
    assert "insertAdjacentHTML" not in js and "document.write" not in js and "eval(" not in js


# ── what it does ─────────────────────────────────────────────────────────────

def test_a_decision_is_recorded_with_the_resume_that_was_sent(live):
    assert req(live, "POST", "/api/postings/1000001/status", {"status": "applied"})[0] == 200
    _, data = jget(live, "/api/postings/1000001")
    assert data["status"] == "applied"
    assert pipeline.load(live.prof)["1000001"]["variant"] == "fullstack"


def test_an_unknown_posting_or_status_is_refused(live):
    assert req(live, "POST", "/api/postings/nope/status", {"status": "skipped"})[0] == 404
    assert req(live, "POST", "/api/postings/1000001/status", {"status": "ghosted"})[0] == 409


def test_an_escalation_needs_a_note(live):
    status, _, raw = req(live, "POST", "/api/escalations/resolve",
                         {"provider": "greenhouse", "slug": "purestorage", "note": " "})
    assert status == 400 and b"note" in raw


def test_an_unknown_action_is_refused(live):
    assert req(live, "POST", "/api/run", {"action": "rm -rf"})[0] == 400


def test_an_action_runs_and_its_output_streams(live, monkeypatch):
    def fake(root, opts):
        for i in range(3):
            print(f"line {i}")
        return 0
    monkeypatch.setitem(srv.ACTIONS, "dashboard", ("Fake", fake))
    status, _, raw = req(live, "POST", "/api/run", {"action": "dashboard"})
    assert status == 202
    for _ in range(50):
        _, snap = jget(live, "/api/job")
        if snap["current"] and snap["current"]["done"]:
            break
        time.sleep(0.05)
    assert snap["current"]["ok"] is True
    assert snap["current"]["lines"] == ["line 0", "line 1", "line 2"]


def test_an_action_is_refused_while_the_scheduled_cycle_holds_the_lock(live):
    with busy(live.prof.shared, "scheduled cycle") as got:
        assert got
        status, _, raw = req(live, "POST", "/api/run", {"action": "dashboard"})
    assert status == 409
    assert json.loads(raw)["holder"]["what"] == "scheduled cycle"


# ── favorite and save-for-later ──────────────────────────────────────────────

def test_star_a_posting(live):
    assert req(live, "POST", "/api/postings/1000001/favorite", {"on": True})[0] == 200
    _, row = jget(live, "/api/postings/1000001")
    assert row["favorite"] is True and row["status"] == "new"
    _, today = jget(live, "/api/today")
    assert today["favorites"] == 1


@pytest.mark.parametrize("body", [{}, {"on": "yes"}, {"on": 1}])
def test_favorite_needs_a_real_boolean(live, body):
    assert req(live, "POST", "/api/postings/1000001/favorite", body)[0] == 400


def test_saved_for_later_leaves_today_and_comes_back(live):
    assert req(live, "POST", "/api/postings/1000001/later", {"days": 7})[0] == 200
    _, today = jget(live, "/api/today")
    assert [r["id"] for r in today["decide"]] == []
    assert today["saved_for_later"] == 1 and today["back_from_later"] == []

    # A week passes.
    data = pipeline.load(live.prof)
    data["1000001"]["later"]["until"] = "2026-01-01T00:00:00+00:00"
    pipeline.path(live.prof).write_text(json.dumps(data), encoding="utf-8")
    _, today = jget(live, "/api/today")
    assert [r["id"] for r in today["back_from_later"]] == ["1000001"]
    assert today["saved_for_later"] == 0


def test_later_can_be_undone_and_rejects_nonsense(live):
    req(live, "POST", "/api/postings/1000001/later", {"days": None})
    assert jget(live, "/api/postings/1000001")[1]["later"]["state"] == "someday"
    assert req(live, "POST", "/api/postings/1000001/later", {"clear": True})[0] == 200
    assert jget(live, "/api/postings/1000001")[1]["later"] is None
    assert req(live, "POST", "/api/postings/1000001/later", {"days": True})[0] == 409
    assert req(live, "POST", "/api/postings/nope/later", {"days": 3})[0] == 404


def test_flags_need_the_same_origin_as_everything_else(live):
    status, _, _ = req(live, "POST", "/api/postings/1000001/favorite", {"on": True},
                       headers={"Origin": "https://attacker.test"})
    assert status == 403
    assert not pipeline.path(live.prof).exists()


def test_later_from_the_calendar(live):
    from datetime import datetime, timedelta, timezone
    until = (datetime.now(timezone.utc) + timedelta(days=10)).replace(microsecond=0)
    status, _, raw = req(live, "POST", "/api/postings/1000001/later",
                         {"until": until.isoformat().replace("+00:00", "Z")})
    assert status == 200, raw
    later = jget(live, "/api/postings/1000001")[1]["later"]
    assert later["state"] == "waiting" and later["until"].startswith(until.date().isoformat())


@pytest.mark.parametrize("until, status", [
    ("not a date", 400),
    ("2020-01-01T00:00:00Z", 409),          # in the past
    ("2026-10-03T00:00:00", 409),           # no timezone
])
def test_a_bad_calendar_date_is_refused(live, until, status):
    assert req(live, "POST", "/api/postings/1000001/later", {"until": until})[0] == status
    assert jget(live, "/api/postings/1000001")[1]["later"] is None


def test_the_apply_kit_is_served(live):
    status, kit = jget(live, "/api/postings/1000001/kit")
    assert status == 200
    assert kit["experience"][0]["title"] == "Software Engineer II"
    assert any(s["workday"].startswith("Python") for s in kit["skills_add"])
    assert SECRET not in json.dumps(kit)


# ── applications from the inbox ──────────────────────────────────────────────

def _seed_ledger(root):
    from jobsearch import applications as A
    ledger = {"apps": {}, "seen": [], "naukri": {"applied": {"2026-09-23": {"count": 19}}, "status_pings": [], "neo": {}}}
    for i, (co, st) in enumerate((("Sam", "applied"), ("Acme", "applied"))):
        ledger["apps"][f"a{i}"] = {"id": f"a{i}", "company": co, "company_source": "display", "role": "",
                                   "req_id": "", "source": "x", "posting_id": None, "status": st,
                                   "first_seen": "2026-09-20", "updated": "2026-09-20", "ignored": False,
                                   "events": [{"date": "2026-09-20T10:00:00+00:00", "kind": "received",
                                               "status": st, "confidence": "high", "subject": "Thanks",
                                               "sender": "", "message_id": f"<{i}>", "thread": "",
                                               "evidence": "", "matched_by": "new"}],
                                   "look": ["company taken from the sender's name"]}
    A.save(root, ledger)


def test_the_applications_view_is_served(live):
    _seed_ledger(live.prof)
    status, d = jget(live, "/api/applications")
    assert status == 200 and len(d["apps"]) == 2 and len(d["look"]) == 2
    assert d["naukri"]["applied_all"] == 19


def test_corrections_go_through_and_clear_the_flag(live):
    _seed_ledger(live.prof)
    assert req(live, "POST", "/api/applications/a0/confirm", {"company": "Real Co"})[0] == 200
    assert req(live, "POST", "/api/applications/a1/merge", {"into": "a0"})[0] == 200
    _, d = jget(live, "/api/applications")
    assert [a["company"] for a in d["apps"]] == ["Real Co"] and not d["look"]


@pytest.mark.parametrize("path, body, code", [
    ("/api/applications/nope/confirm", {}, 404),
    ("/api/applications/a0/status", {"status": "ghosted"}, 400),
    ("/api/applications/a0/merge", {"into": "nope"}, 404),
    ("/api/applications/a0/merge", {"into": "a0"}, 400),
])
def test_bad_corrections_are_refused(live, path, body, code):
    _seed_ledger(live.prof)
    assert req(live, "POST", path, body)[0] == code


def test_corrections_need_the_same_origin(live):
    _seed_ledger(live.prof)
    status, _, _ = req(live, "POST", "/api/applications/a0/ignore", {"ignored": True},
                       headers={"Origin": "https://attacker.test"})
    assert status == 403


# ── Groq's readings and learned rules ───────────────────────────────────────

def _seed_suggestion(root):
    from jobsearch import applications as A
    _seed_ledger(root)
    ledger = A.load(root)
    ledger["apps"]["a0"]["suggestion"] = {
        "summary": "A receipt from Acme.", "outcomes": [], "dropped": [], "by": "gpt-oss via groq",
        "company": {"value": "Acme", "quote": "applying to Acme!"}, "message_ids": ["<0>"], "sent_sha256": "ab"}
    A.save(root, ledger)


def test_a_suggestion_shows_on_the_card_and_the_key_never_does(live):
    _seed_suggestion(live.prof)
    status, d = jget(live, "/api/applications")
    assert status == 200 and d["proposals"] == [] and isinstance(d["groq"], bool)
    assert next(a for a in d["look"] if a["id"] == "a0")["suggestion"]["company"]["value"] == "Acme"
    assert SECRET not in json.dumps(d)


def test_using_a_suggestion_through_the_app(live):
    _seed_suggestion(live.prof)
    status, _, raw = req(live, "POST", "/api/applications/a0/suggestion", {"use": True})
    assert status == 200 and json.loads(raw)["company"] == "Acme"
    assert req(live, "POST", "/api/applications/a0/suggestion", {"use": True})[0] == 400   # answered once


def test_what_was_sent_is_readable_and_rules_are_decided_by_id(live):
    from jobsearch import mail_learn as L
    assert jget(live, "/api/applications/a0/sent") == (200, [])
    assert req(live, "POST", "/api/mail-rules/0123456789/approve", {})[0] == 404
    assert req(live, "POST", "/api/mail-rules/not-an-id/approve", {})[0] == 404
    L._add(live.prof, {"id": "abcdef0123", "type": "sender", "status": "pending", "sender": "x@acme.com",
                       "company": "Acme", "example": "", "changes": [], "proposed_at": "2026-09-26",
                       "learned_from": {"app_id": "a0", "company": "", "subject": "s", "date": "2026-09-20"}})
    assert [p["id"] for p in jget(live, "/api/applications")[1]["proposals"]] == ["abcdef0123"]
    assert req(live, "POST", "/api/mail-rules/abcdef0123/reject", {})[0] == 200
    assert jget(live, "/api/applications")[1]["proposals"] == []


# ── pause and stop ───────────────────────────────────────────────────────────

def test_pause_and_resume_the_schedule(live):
    status, _, raw = req(live, "POST", "/api/schedule/pause", {"hours": 3})
    assert status == 200 and json.loads(raw)["until"]
    _, o = jget(live, "/api/overview")
    assert o["paused"]["until"]
    assert req(live, "POST", "/api/schedule/pause", {"hours": None})[0] == 200
    _, o = jget(live, "/api/overview")
    assert o["paused"]["until"] is None
    assert req(live, "POST", "/api/schedule/resume", {})[0] == 200
    _, o = jget(live, "/api/overview")
    assert o["paused"] is None


@pytest.mark.parametrize("hours", [0, -1, 1000, "3", True])
def test_pause_needs_a_sane_number_of_hours(live, hours):
    assert req(live, "POST", "/api/schedule/pause", {"hours": hours})[0] == 400


def test_stop_asks_the_running_cycle_to_stop(live):
    assert req(live, "POST", "/api/stop", {})[0] == 409            # nothing running
    with busy(live.prof.shared, "scheduled cycle"):
        status, _, raw = req(live, "POST", "/api/stop", {})
        assert status == 202 and json.loads(raw)["stopping"]["what"] == "scheduled cycle"
        _, o = jget(live, "/api/overview")
        assert o["stopping"] is True
