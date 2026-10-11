"""Groq reads what the rules couldn't decide. What it is sent, what it is
believed on, and that it is only ever Groq. Every email here is made up."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from jobsearch import applications as A
from jobsearch import mail_ai as M
from jobsearch.llm import LLM, Answer
from jobsearch.mailbox import Mail
from samples import make_root

ME = ({"kavya", "example"}, "kavyaexample|kavyaexample")


# ── what is sent ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, gone, kept", [
    ("Reply to jane.recruiter@globex.example today", "jane", "Reply to [email] today"),
    ("Call +91 00000 11111 or 0000011111.", "11111", "Call [number] or [number]."),
    ("PAN: ABCDE1234F, Reference CID: C10000001", "ABCDE", "PAN: [number], Reference CID: [number]"),
    ("Current CTC: 18 LPA, expected ₹ 25,00,000", "18", "Current CTC: [money], expected [money]"),
    ("Hi EXAMPLE KAVYA, Dear Kavya,", "EXAMPLE", "Hi [you] [you], Dear [you],"),
    ("Log in at https://careers.example.com/x?id=9", "careers", "Log in at [link]"),
])
def test_personal_details_are_removed(text, gone, kept):
    out = M.redact(text, ME)
    assert gone not in out and out == kept


@pytest.mark.parametrize("text", [
    "Thank you for applying for the position of R-123456 Software Engineer - AI Engineer.",
    "Your job application status (Job number: 210000001)",
    "Date: 03-09-2026 Time: 15:00:00 until 16:00:00 IST",
    "Senior Software Engineer - 3303",
    "in the position of Software Engineer - II, 200012345 at Globex.",
])
def test_what_tells_applications_apart_stays(text):
    assert M.redact(text, ME) == text


def _app(**kw):
    a = {"id": "x-1", "company": "", "company_source": "", "role": "", "req_id": "", "status": "applied",
         "first_seen": "2026-09-11", "updated": "2026-09-11", "ignored": False,
         "look": ["no company name in the email"],
         "events": [{"date": "2026-09-11T05:43:55+00:00", "kind": "received", "status": "applied",
                     "confidence": "high", "subject": "Thanks for your Application", "sender": "globex@myworkday.com",
                     "message_id": "<n1@x>", "thread": "t1", "evidence": "", "matched_by": "new"}]}
    a.update(kw)
    return a


GLOBEX = ("Dear Kavya, Thank you for your interest in a career with Globex and taking the time to complete "
            "an application for our Software Engineer opening. Call 0000011111 with questions.\n"
            "This is a system generated mail, please do not reply. Globex, 1 Example Road.")


def test_the_payload_is_exactly_the_question_and_nothing_more():
    app = _app()
    p = M.build_payload(app, [(app["events"][0], "Globex Careers", GLOBEX)], [], ME)
    assert set(p) == {"why_flagged", "record", "emails", "other_records"}
    (e,) = p["emails"]
    assert set(e) == {"n", "date", "from_name", "from_domain", "subject", "text"}
    blob = json.dumps(p)
    assert "globex@" not in blob and "@" not in blob             # never the sender's address
    assert "0000011111" not in blob and "Kavya" not in blob
    assert "1 Example Road" not in blob                        # below the sign-off: cut
    assert "a career with Globex" in e["text"] and len(e["text"]) <= M.MAX_TEXT


def test_other_records_are_only_ones_it_could_be():
    ledger = {"apps": {
        "x-1": _app(company="Initech For Clients"),
        "acc": _app(id="acc", company="Initech", events=[{**_app()["events"][0], "thread": "zz"}]),
        "wf": _app(id="wf", company="Umbrella", events=[{**_app()["events"][0], "thread": "zz"}]),
        "same-thread": _app(id="same-thread", company="Someone"),
    }}
    ids = {o["id"] for o in M.others(ledger, ledger["apps"]["x-1"])}
    assert ids == {"acc", "same-thread"}                       # same company; same mail thread


# ── what it is believed on ───────────────────────────────────────────────────

def _payload():
    app = _app()
    return M.build_payload(app, [(app["events"][0], "", GLOBEX)],
                           [{"id": "other", "company": "Globex", "role": "", "status": "applied",
                             "first_seen": "2026-09-01", "updated": "2026-09-01", "events": []}], ME)


def test_a_claim_it_can_quote_is_kept():
    s = M.ground({"company": "Globex", "company_quote": "a career with Globex",
                  "role": "Software Engineer", "role_quote": "for our Software Engineer opening",
                  "emails": [{"n": 1, "outcome": "received", "quote": "Thank you for your interest in a career "
                                                                      "with Globex"}],
                  "same_as": "other", "same_as_why": "same company"}, _payload(), ["<n1@x>"])
    assert s["company"]["value"] == "Globex" and s["role"]["value"] == "Software Engineer"
    assert s["outcomes"][0]["message_id"] == "<n1@x>" and s["same_as"]["id"] == "other"
    assert not s["dropped"]


@pytest.mark.parametrize("answer, dropped", [
    ({"company": "Globex Corporation", "company_quote": "a career with Globex"}, "company"),  # not in its quote
    ({"company": "Globex", "company_quote": "Globex is hiring"}, "company"),                   # quote not in email
    ({"role": "Senior Engineer", "role_quote": "Senior Engineer"}, "role"),
    ({"emails": [{"n": 1, "outcome": "offer", "quote": "We are pleased to offer you"}]}, "email 1"),
    ({"not_an_application": True, "not_an_application_quote": "this is marketing"}, "not an application"),
    ({"same_as": "made-up-id"}, "same as"),
])
def test_a_claim_it_cannot_show_in_the_email_is_dropped_and_listed(answer, dropped):
    s = M.ground(answer, _payload(), ["<n1@x>"])
    assert not {"company", "role", "same_as", "not_an_application"} & set(s) and not s["outcomes"]
    assert any(dropped in d for d in s["dropped"])


def test_quotes_survive_curly_apostrophes_and_spacing():
    app = _app()
    p = M.build_payload(app, [(app["events"][0], "", "We’re  moving you to the next stage.")], [], ME)
    s = M.ground({"emails": [{"n": 1, "outcome": "interview", "quote": "We're moving you to the next stage"}]},
                 p, ["<n1@x>"])
    assert s["outcomes"]


# ── only Groq ────────────────────────────────────────────────────────────────

def test_the_inbox_only_ever_uses_groq(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "openrouter,groq")
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    llm = LLM(providers=M.PROVIDERS)
    assert [p.name for p in llm.chain] == ["groq"] and not llm.available


# ── one review pass, end to end with no network ──────────────────────────────

@pytest.fixture
def root(tmp_path, monkeypatch):
    prof = make_root(tmp_path)
    (prof.var / "triage-results.json").write_text(json.dumps({"postings": {}}))
    A.save(prof, {"apps": {"x-1": _app()}, "seen": [], "naukri": {"applied": {}, "status_pings": [], "neo": {}}})
    monkeypatch.setenv("GMAIL_USER", "kavya.example@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcdefghijklmnop")
    monkeypatch.setenv("MAIL_PROFILE", prof.id)
    return prof


class FakeInbox:
    def __init__(self, root):
        self.user = "kavya.example@example.com"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def by_message_id(self, mid):
        return Mail(uid=1, message_id=mid, thread="t1", date="2026-09-11T05:43:55+00:00",
                    from_addr="globex@myworkday.com", from_name="", subject="Thanks for your Application",
                    body=GLOBEX)


class FakeGroq:
    def __init__(self, data=None, ok=True, key_ok=True, error="HTTP 503"):
        self.data, self.ok, self.sent, self.key_ok, self.error = data, ok, [], key_ok, error
        self.chain = LLM(providers=M.PROVIDERS).chain
        self.available = True

    def key_check(self):
        return (True, "") if self.key_ok else (False, "Groq rejected the API key (HTTP 401) - it may have expired")

    def ask_json(self, system, user, **kw):
        self.sent.append(user)
        if not self.ok:
            return Answer(ok=False, failures=[{"provider": "groq", "error": self.error}])
        return Answer(ok=True, data=self.data, provider="groq", model="gpt-oss-120b")


GOOD = {"company": "Globex", "company_quote": "a career with Globex", "role": "Software Engineer",
        "role_quote": "our Software Engineer opening", "summary": "A receipt from Globex."}
# One claim it can't show in the email: the reading waits for a click.
PARTIAL = {**GOOD, "role_quote": "the Principal Engineer opening"}


def test_a_flagged_record_gets_a_suggestion_and_the_sent_text_is_on_record(root, monkeypatch):
    monkeypatch.setattr("jobsearch.mailbox.Inbox", FakeInbox)
    groq = FakeGroq(PARTIAL)
    rep = M.review(root, say=lambda *_: None, llm=groq)
    assert rep["asked"] == 1 and rep["suggested"] == 1
    app = A.load(root)["apps"]["x-1"]
    assert app["suggestion"]["company"]["value"] == "Globex"
    (logged,) = M.sent_for(root, "x-1")
    assert logged["payload"] == groq.sent[0]                          # exactly what was sent
    assert logged["sha256"] == hashlib.sha256(groq.sent[0].encode()).hexdigest()
    assert M.review(root, say=lambda *_: None, llm=groq)["asked"] == 0   # waiting on an answer


def test_using_it_corrects_the_record_and_forgets_the_text(root, monkeypatch):
    monkeypatch.setattr("jobsearch.mailbox.Inbox", FakeInbox)
    M.review(root, say=lambda *_: None, llm=FakeGroq(PARTIAL))
    a = A.answer_suggestion(root, "x-1", use=True)
    assert (a["company"], a["role"], a["look"]) == ("Globex", "", [])     # the unquoted role left out
    (logged,) = M.sent_for(root, "x-1")
    assert "payload" not in logged and logged["sha256"]              # that it happened, not what it said
    groq = FakeGroq(GOOD)
    assert M.review(root, say=lambda *_: None, llm=groq)["asked"] == 0 and not groq.sent


def test_dismissing_changes_nothing_and_is_not_asked_again(root, monkeypatch):
    monkeypatch.setattr("jobsearch.mailbox.Inbox", FakeInbox)
    M.review(root, say=lambda *_: None, llm=FakeGroq(PARTIAL))
    a = A.answer_suggestion(root, "x-1", use=False)
    assert a["company"] == "" and a["look"]
    assert M.review(root, say=lambda *_: None, llm=FakeGroq(PARTIAL))["asked"] == 0


NOT_ONE = {"not_an_application": True, "not_an_application_quote": "taking the time to complete an application",
           "summary": "Not an application."}


def test_not_an_application_is_set_aside(root, monkeypatch):
    monkeypatch.setattr("jobsearch.mailbox.Inbox", FakeInbox)
    M.review(root, say=lambda *_: None, llm=FakeGroq(NOT_ONE))
    a = A.load(root)["apps"]["x-1"]
    assert a["ignored"] is True and a["auto"]["suggestion"]["not_an_application"]


# ── applied without a click, when it can quote everything it was asked ──────

def test_a_reading_quoted_in_full_is_applied_without_a_click(root, monkeypatch):
    """A marketplace's matches showed as an interview while Groq's right
    answer waited for a click."""
    monkeypatch.setattr("jobsearch.mailbox.Inbox", FakeInbox)
    rep = M.review(root, say=lambda *_: None, llm=FakeGroq(GOOD))
    a = A.load(root)["apps"]["x-1"]
    assert rep["applied"] == 1 and "suggestion" not in a
    assert (a["company"], a["role"], a["look"]) == ("Globex", "Software Engineer", [])
    assert a["auto"]["before"]["company"] == "" and a["auto"]["by"] == "gpt-oss-120b via groq"
    (logged,) = M.sent_for(root, "x-1")
    assert "payload" not in logged                                       # answered: the text is let go


def test_a_company_named_by_groq_moves_its_tracked_posting_at_once(root, monkeypatch):
    """A receipt named no company, so the sync couldn't link it. Groq named
    it, but the posting stayed under "new" until the next sync, though the
    inbox already said applied."""
    from jobsearch import pipeline
    (root.var / "triage-results.json").write_text(json.dumps({"postings": {"4739": {
        "id": "4739", "title": "Software Engineer", "verdict": "keep",
        "board": "greenhouse:globex", "company": "Globex Corporation"}}}))
    monkeypatch.setattr("jobsearch.mailbox.Inbox", FakeInbox)
    M.review(root, say=lambda *_: None, llm=FakeGroq(GOOD))
    assert A.load(root)["apps"]["x-1"]["posting_id"] == "4739"
    assert pipeline.status(pipeline.load(root), "4739") == "applied"


def test_undo_puts_it_back_with_the_reading_waiting(root, monkeypatch):
    monkeypatch.setattr("jobsearch.mailbox.Inbox", FakeInbox)
    M.review(root, say=lambda *_: None, llm=FakeGroq(GOOD))
    a = A.undo_auto(root, "x-1")
    assert a["company"] == "" and a["look"] == ["no company name in the email"] and "auto" not in a
    assert a["suggestion"]["company"]["value"] == "Globex" and a["suggestion"]["undone"]
    groq = FakeGroq(GOOD)
    assert M.review(root, say=lambda *_: None, llm=groq)["applied"] == 0 and not groq.sent
    assert "auto" not in A.load(root)["apps"]["x-1"]                     # not re-applied on its own
    assert A.answer_suggestion(root, "x-1", use=True)["company"] == "Globex"   # the click still works


def _with_events(root, *events):
    led = A.load(root)
    led["apps"]["x-1"]["events"] += [{**led["apps"]["x-1"]["events"][0], **e} for e in events]
    led["apps"]["x-1"]["look"] = A.look_reasons(led["apps"]["x-1"])
    A.save(root, led)


def test_not_an_application_waits_if_the_record_has_emails_groq_did_not_read(root, monkeypatch):
    monkeypatch.setattr("jobsearch.mailbox.Inbox", FakeInbox)
    _with_events(root, {"message_id": "<n9@x>", "date": "2026-09-01T00:00:00+00:00"})   # not flagged, not sent
    M.review(root, say=lambda *_: None, llm=FakeGroq(NOT_ONE))
    a = A.load(root)["apps"]["x-1"]
    assert not a["ignored"] and a["suggestion"]["not_an_application"] and "auto" not in a


def test_same_job_as_another_record_waits_for_a_click(root, monkeypatch):
    monkeypatch.setattr("jobsearch.mailbox.Inbox", FakeInbox)
    led = A.load(root)
    led["apps"]["n-2"] = {**_app(id="n-2", company="Globex", look=[]),
                          "events": [{**_app()["events"][0], "message_id": "<n0@x>", "thread": "t1"}]}
    A.save(root, led)
    M.review(root, say=lambda *_: None, llm=FakeGroq({**GOOD, "same_as": "n-2", "same_as_why": "Same thread."}))
    assert "suggestion" in A.load(root)["apps"]["x-1"]


def test_every_unsure_email_must_be_settled(root, monkeypatch):
    """An unsure "interview" shows as "checking"; Groq quoting what it is
    settles it, and the status follows."""
    monkeypatch.setattr("jobsearch.mailbox.Inbox", FakeInbox)
    _with_events(root, {"message_id": "<n2@x>", "date": "2026-09-12T00:00:00+00:00", "kind": "interview",
                        "status": "interviewing", "confidence": "low"})
    assert A.status_of(A.load(root)["apps"]["x-1"]["events"]) == "applied"
    M.review(root, say=lambda *_: None, llm=FakeGroq(GOOD))             # names the company, not email 2
    assert "auto" not in A.load(root)["apps"]["x-1"]
    A.answer_suggestion(root, "x-1", use=False)
    _with_events(root, {"message_id": "<n3@x>", "date": "2026-09-13T00:00:00+00:00", "kind": "interview",
                        "status": "interviewing", "confidence": "low"})
    settled = {**GOOD, "emails": [{"n": n, "outcome": "received", "quote": "a career with Globex"}
                                  for n in (1, 2, 3)]}                  # the first email, and both unsure
    M.review(root, say=lambda *_: None, llm=FakeGroq(settled))
    a = A.load(root)["apps"]["x-1"]
    assert a.get("auto") and a["status"] == "applied" and a["look"] == []


def test_a_reading_that_was_waiting_is_settled_on_the_next_check(root, monkeypatch):
    """One record's: Groq answered before readings could be applied on their own."""
    led = A.load(root)
    led["apps"]["x-1"]["suggestion"] = {"summary": "Not an application.", "outcomes": [], "dropped": [],
                                       "not_an_application": {"quote": "taking the time"},
                                       "message_ids": ["<n1@x>"], "by": "gpt-oss-120b via groq"}
    A.save(root, led)
    monkeypatch.setattr("jobsearch.mailbox.Inbox", lambda root: pytest.fail("nothing needed reading"))
    rep = M.review(root, say=lambda *_: None, llm=FakeGroq(GOOD))
    assert rep["applied"] == 1 and A.load(root)["apps"]["x-1"]["ignored"] is True


def test_a_record_groq_keeps_failing_on_stops_being_retried(root, monkeypatch):
    monkeypatch.setattr("jobsearch.mailbox.Inbox", FakeInbox)
    down = FakeGroq(ok=False)
    for _ in range(M.MAX_FAILURES + 2):
        M.review(root, say=lambda *_: None, llm=down)
    assert len(down.sent) == M.MAX_FAILURES


def test_without_a_groq_key_nothing_is_sent(root, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setattr("jobsearch.mailbox.Inbox", lambda root: pytest.fail("the inbox was opened"))
    monkeypatch.setattr("requests.post", lambda *a, **k: pytest.fail("something was sent"))
    assert M.review(root, say=lambda *_: None)["skipped"] == "GROQ_API_KEY not set"


def test_an_expired_key_is_found_before_any_email_is_sent(root, monkeypatch):
    """Found for real: an expired Groq key turned five records' emails into
    five rejected requests."""
    monkeypatch.setattr("jobsearch.mailbox.Inbox", lambda root: pytest.fail("the inbox was opened"))
    dead = FakeGroq(GOOD, key_ok=False)
    rep = M.review(root, say=lambda *_: None, llm=dead)
    assert not dead.sent and "rejected the API key" in rep["skipped"]
    assert "rejected the API key" in M.last_status(root)["skipped"]      # the app says why


def test_a_key_rejected_mid_run_stops_the_run_and_blames_no_record(root, monkeypatch):
    monkeypatch.setattr("jobsearch.mailbox.Inbox", FakeInbox)
    led = A.load(root)
    led["apps"]["x-2"] = {**_app(id="x-2"), "events": [{**_app()["events"][0], "message_id": "<n2@x>"}]}
    A.save(root, led)
    dead = FakeGroq(ok=False, error="HTTP 401 (strict json): expired_api_key")
    rep = M.review(root, say=lambda *_: None, llm=dead)
    assert len(dead.sent) == 1 and "rejected the API key" in rep["skipped"]
    assert all(not a.get("ask_failures") for a in A.load(root)["apps"].values())
