"""Job emails pasted in, and applications added by hand (mail_paste.py): the
hosted service's way into the application ledger, with no inbox read and no
model."""
from __future__ import annotations

import json

import pytest

from jobsearch import applications as A
from jobsearch import mail_paste as P
from jobsearch import pipeline
from jobsearch.web import routes
from jobsearch.web.routes import HOSTED, ApiError
from jobsearch.web.runner import Runner
from samples import make_root

RECEIPT = """From: Globex Careers <careers@globex.example>
Subject: Thank you for applying to Globex
Date: Mon, 12 Oct 2026 10:15:00 +0530

Hi Kavya,
Thank you for applying for the Backend Engineer position at Globex. We have received your application.
PRIVATE-LINE-7Q is our internal reference for the hiring sprint.
"""
VAGUE = "Hello, just checking in about next week."


@pytest.fixture
def prof(tmp_path):
    return make_root(tmp_path)


def test_a_pasted_receipt_becomes_an_application(prof):
    r = P.paste(prof, RECEIPT)
    a = r["app"]
    assert (a["company"], a["role"], a["status"], a["first_seen"]) == ("Globex", "Backend Engineer", "applied", "2026-10-12")
    assert a["events"][0]["sender"] == "careers@globex.example"


def test_only_the_subject_sender_and_deciding_sentence_are_kept(prof):
    P.paste(prof, RECEIPT)
    kept = A.path(prof).read_text(encoding="utf-8")
    assert "Thank you for applying to Globex" in kept
    assert "PRIVATE-LINE-7Q" not in kept and "Hi Kavya" not in kept


def test_the_same_email_twice_is_one_event(prof):
    P.paste(prof, RECEIPT)
    assert P.paste(prof, RECEIPT)["duplicate"] is True
    assert len(A.load(prof)["apps"]["globex-backend-engineer"]["events"]) == 1


def test_when_the_rules_cant_tell_the_person_says(prof):
    assert P.paste(prof, VAGUE)["ask"] == "kind"
    assert P.paste(prof, VAGUE, kind="interview")["ask"] == "company"
    a = P.paste(prof, VAGUE, kind="interview", company="Initech", role="SRE")["app"]
    assert (a["company"], a["role"], a["status"]) == ("Initech", "SRE", "interviewing")
    assert a["events"][0]["evidence"] == "you said so"


@pytest.mark.parametrize("kw, match", [({"text": ""}, "paste"), ({"text": "x" * 20_001}, "longer"),
                                       ({"text": VAGUE, "kind": "hired"}, "kind must be")])
def test_nonsense_is_refused(prof, kw, match):
    with pytest.raises(P.PasteError, match=match):
        P.paste(prof, **kw)


def test_an_application_made_elsewhere_moves_its_tracked_job(prof):
    posting = {"id": "1000001", "title": "Backend Engineer", "company": "Globex", "board": "lever:globex",
               "verdict": "keep", "location": "Pune"}
    (prof.var / "triage-results.json").write_text(json.dumps({"postings": {"1000001": posting}}))
    a = P.add(prof, "Globex", "Backend Engineer", "2026-10-10")["app"]
    assert a["status"] == "applied" and a["posting_id"] == "1000001"
    assert pipeline.status(pipeline.load(prof), "1000001") == "applied"


def test_a_mail_client_date_or_a_typed_one():
    assert P._when("2026-10-12") == "2026-10-12T12:00:00+00:00"
    assert P._when("Mon, 12 Oct 2026 10:15:00 +0530") == "2026-10-12T04:45:00+00:00"
    assert P._when("not a date") == "" and P._when("") == ""


def test_headers_are_read_only_from_the_top():
    h = P.headers("From: A <a@x.example>\nSent: Mon, 12 Oct 2026 10:15:00 +0000\nSubject: Hi\n\n" + "\n" * 20 + "Subject: later")
    assert h == {"from": "A <a@x.example>", "date": "Mon, 12 Oct 2026 10:15:00 +0000", "subject": "Hi"}


def test_the_hosted_service_tracks_applications_without_an_inbox(prof):
    run = Runner(prof)
    routes.post(prof, "/api/applications/paste", {"text": RECEIPT}, run, scope=HOSTED)
    _, r = routes.post(prof, "/api/applications/add", {"company": "Initech", "status": "offer"}, run, scope=HOSTED)
    view = routes.get(prof, "/api/applications", {}, run, scope=HOSTED)
    assert {a["company"] for a in view["apps"]} == {"Globex", "Initech"}
    assert "configured" not in view and "groq" not in view          # no inbox, no model here
    routes.post(prof, f"/api/applications/{r['app']['id']}/status", {"status": "rejected"}, run, scope=HOSTED)
    with pytest.raises(ApiError) as e:                                # model suggestions are the local app's
        routes.post(prof, f"/api/applications/{r['app']['id']}/suggestion", {"use": True}, run, scope=HOSTED)
    assert e.value.status == 404
    with pytest.raises(P.PasteError) as bad:
        routes.post(prof, "/api/applications/add", {"company": ""}, run, scope=HOSTED)
    assert routes.as_error(bad.value, HOSTED) == (400, {"error": "name the company"})
