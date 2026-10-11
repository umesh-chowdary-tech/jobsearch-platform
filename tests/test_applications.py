"""The applications ledger, on the situations a real inbox produced. Every
company, person and requisition number here is made up."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from jobsearch import applications as A
from jobsearch import mail_rules as R
from jobsearch import pipeline
from samples import make_root, senders_with

DATA = senders_with({"globex.example": "Globex"})
_n = iter(range(1, 10_000))


def mail(sender, subject, body="", date="2026-09-15T10:00:00+00:00", thread="", name=""):
    uid = next(_n)
    return SimpleNamespace(uid=uid, message_id=f"<m{uid}>", thread=thread, date=date, from_addr=sender,
                           from_name=name, subject=subject, body=body, domain=sender.rsplit("@", 1)[-1])


def feed(ledger, m):
    return A.record(ledger, m, R.classify(m.from_addr, m.subject, m.body),
                    R.facts(m.from_addr, m.from_name, m.subject, m.body, DATA))


@pytest.fixture
def ledger():
    return {"apps": {}, "seen": [], "naukri": {"applied": {}, "status_pings": [], "neo": {}}}


GBX = "talent@globex.example"


def gbx_receipt(req, date):
    return mail(GBX, "Globex Careers: Thank you for applying",
                f"Thank you for applying for the position of {req} Software Engineer - AI Engineer.",
                date=date, thread="gbx-thread")


def gbx_rejection(req, date):
    return mail(GBX, "Globex Careers: Thank you for your time and interest",
                f"Thank you for applying for the position of {req} Software Engineer - AI Engineer. We decided "
                "to move forward with other applicants.", date=date, thread="gbx-thread")


def by_req(ledger, req):
    return next(a for a in ledger["apps"].values() if a["req_id"] == req)


# ── matching ─────────────────────────────────────────────────────────────────

def test_the_requisition_id_keeps_same_company_applications_apart(ledger):
    """Three applications at one employer in one mail thread: only the id told
    the rejection of one from the one still open."""
    feed(ledger, gbx_receipt("R-111111", "2026-09-15T10:00:00+00:00"))
    feed(ledger, gbx_receipt("R-123456", "2026-09-24T10:00:00+00:00"))
    feed(ledger, gbx_rejection("R-111111", "2026-09-25T10:00:00+00:00"))
    assert len(ledger["apps"]) == 2
    assert by_req(ledger, "R-111111")["status"] == "rejected"
    assert by_req(ledger, "R-123456")["status"] == "applied"


def test_one_thread_with_three_job_numbers_is_three_applications(ledger):
    """One employer's receipts share a subject, so the mail client threads them."""
    for n in ("200012345", "200012346", "200012347"):
        feed(ledger, mail("c@email.careers.globex.example", "Thank you for your application!",
                          f"We've received your application for Job number: {n}.", thread="gx"))
    assert len(ledger["apps"]) == 3


def test_a_rejection_that_names_the_role_joins_the_receipt_that_did_not(ledger):
    feed(ledger, mail("no-reply@hooli.example", "Thank you for applying to Hooli", "We received your application."))
    feed(ledger, mail("no-reply@hooli.example", "Thank you for your interest in Hooli | Senior AI Platform Engineer",
                      "We have decided to proceed with other candidates.", date="2026-09-16T10:00:00+00:00"))
    (app,) = ledger["apps"].values()
    assert app["status"] == "rejected" and app["role"] == "Senior AI Platform Engineer"


def test_two_roles_at_one_company_stay_two(ledger):
    oracle = "no-reply@workflow.mail.us2.cloud.oracle.com"
    feed(ledger, mail(oracle, "Your recent job application for Software Engineer - Java Full Stack",
                      "We have received your application.", name="Initech Talent Acquisition Team"))
    feed(ledger, mail(oracle, "Your recent job application for Software Engineering Senior Associate - Java Full Stack",
                      "We have received your application.", name="Initech Talent Acquisition Team",
                      date="2026-09-21T10:00:00+00:00"))
    assert len(ledger["apps"]) == 2


def test_same_day_receipt_and_rejection_ends_rejected(ledger):
    """One employer sent both on the same day."""
    wd = "umbrella@myworkday.com"
    feed(ledger, mail(wd, "Your journey at Umbrella begins for Full Stack Developer", "R-40001 received.",
                      date="2026-09-03T09:00:00+00:00"))
    feed(ledger, mail(wd, "Update on your Umbrella Job Application for Full Stack Developer",
                      "R-40001: we regret to inform you that your application was not selected.",
                      date="2026-09-03T08:00:00+00:00"))            # earlier clock, same day
    assert by_req(ledger, "R-40001")["status"] == "rejected"


def test_the_same_email_twice_is_recorded_once(ledger):
    m = gbx_receipt("R-111111", "2026-09-15T10:00:00+00:00")
    assert feed(ledger, m) is not None
    assert feed(ledger, m) is None
    assert len(by_req(ledger, "R-111111")["events"]) == 1


def test_naukri_is_counted_and_never_becomes_applications(ledger):
    feed(ledger, mail("info@naukri.com", "You applied for 19 jobs on 23 Sep",
                      "Applied on September 23, 2026\nFullstack Developer\nGlobex\nTrack applications\n"))
    assert ledger["apps"] == {}
    assert next(iter(ledger["naukri"]["applied"].values()))["count"] == 19


# ── what needs a look ────────────────────────────────────────────────────────

def test_a_bare_sender_name_is_flagged_an_organisation_is_not(ledger):
    feed(ledger, mail("sam@connect.hackajob.com", "Thanks for applying", "Thanks for applying.", name="Sam"))
    feed(ledger, mail("x@myworkday.com", "Thank you for your application!", "We received your application.",
                      name="Umbrella People Services"))
    looks = {a["company"]: a["look"] for a in ledger["apps"].values()}
    assert looks["Sam"] and not looks["Umbrella"]


# ── the person's corrections ─────────────────────────────────────────────────

@pytest.fixture
def root(tmp_path):
    prof = make_root(tmp_path)
    postings = {
        "gbx-R-123456": {"id": "gbx-R-123456", "title": "Software Engineer - AI Engineer", "board": "workday:gbx",
                         "company": "Globex", "verdict": "keep"},
        "gbx-R-123999": {"id": "gbx-R-123999", "title": "Software Engineer - AI Engineer", "board": "workday:gbx",
                         "company": "Globex", "verdict": "keep"},
        "1000001": {"id": "1000001", "title": "Senior Backend Engineer", "board": "greenhouse:initech",
                    "company": "Initech Software", "verdict": "keep"},
    }
    (prof.var / "triage-results.json").write_text(json.dumps({"postings": postings}))
    return prof


def test_confirming_clears_the_flag_until_something_new_is_unsure(root, ledger):
    feed(ledger, mail("sam@connect.hackajob.com", "Thanks for applying", "Thanks for applying.", name="Sam"))
    app_id = next(iter(ledger["apps"]))
    A.save(root, ledger)
    # Corrected: the next email still says "Sam", and must find this record.
    assert not A.confirm(root, app_id, company="Acme")["look"]
    led = A.load(root)
    feed(led, mail("sam@connect.hackajob.com", "Next steps", "Please complete the assessment.",
                   name="Sam", date="2099-01-01T00:00:00+00:00"))
    assert led["apps"][app_id]["look"]


def test_same_as_merges_two_records(root, ledger):
    """Two records the rules could not join - here, two role names that are
    in fact one application. "Same as..." puts them together."""
    vd = "vandelay@myworkday.com"
    feed(ledger, mail(vd, "Time to schedule your interview with Vandelay!",
                      "Please schedule your interview for the AI / ML Engineer role.", name="Vandelay Careers"))
    feed(ledger, mail(vd, "Next steps for your Vandelay application",
                      "Your interview for Large Language Models Specialist is scheduled.", name="Vandelay Careers",
                      date="2026-09-16T10:00:00+00:00"))
    A.save(root, ledger)
    ids = sorted(ledger["apps"])
    assert len(ids) == 2
    merged = A.merge(root, ids[1], ids[0])
    assert len(A.load(root)["apps"]) == 1 and len(merged["events"]) == 2


def test_a_merged_record_still_answers_to_the_names_it_came_under(root, ledger):
    """Two records known by senders' names, each corrected to Acme, then
    merged: the next email signed "Sam" must find the merged record, not
    start a new one because the name left with the removed record."""
    feed(ledger, mail("sam@connect.hackajob.com", "Thanks for applying", "Thanks for applying.", name="Sam"))
    feed(ledger, mail("priya@connect.hackajob.com", "Next steps", "Please complete the assessment.",
                      name="Priya", date="2026-09-16T10:00:00+00:00"))
    A.save(root, ledger)
    sam = next(k for k, a in ledger["apps"].items() if a["company"] == "Sam")
    priya = next(k for k, a in ledger["apps"].items() if a["company"] == "Priya")
    A.confirm(root, sam, company="Acme")
    A.confirm(root, priya, company="Acme")
    assert set(A.merge(root, sam, priya)["aliases"]) == {"Sam", "Priya"}
    led = A.load(root)
    feed(led, mail("sam@connect.hackajob.com", "Following up", "Please complete the assessment.",
                   name="Sam", date="2026-09-17T10:00:00+00:00", thread="another"))
    assert list(led["apps"]) == [priya]


def test_a_correction_during_a_sync_is_kept_and_no_mail_is_lost(root, ledger, monkeypatch):
    """"Looks right" clicked while the sync holds the ledger in memory. Before
    the edit lock, one save overwrote the other: the click was lost, or the
    new email was - and the saved inbox position meant it was never re-read."""
    from jobsearch import mail_sync
    from jobsearch.mailbox import Mail
    feed(ledger, mail("sam@connect.hackajob.com", "Thanks for applying", "Thanks for applying.", name="Sam"))
    (flagged,) = ledger["apps"]
    A.save(root, ledger)
    new = Mail(uid=7, message_id="<new>", thread="t", date="2026-09-25T10:00:00+00:00", from_addr=GBX,
               from_name="Globex HR", subject="Globex Careers: Thank you for applying")

    class FakeInbox:
        def __init__(self, root):
            self.user, self.uidvalidity = "me@example.com", "1"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def new_uids(self, state, days):
            return [7]

        def headers(self, uids):
            return [new]

        def bodies(self, uids):
            return {7: "Thank you for applying for the position of R-123456 Software Engineer - AI Engineer."}

    monkeypatch.setattr(mail_sync, "Inbox", FakeInbox)
    real_link, clicks = A.link_postings, []

    def link_while_clicking(root_, led):                  # between the sync's load and save
        t = threading.Thread(target=lambda: A.confirm(root, flagged, company="Acme"))
        t.start()
        t.join(0.5)                                       # unlocked, the click completes here
        clicks.append(t)
        return real_link(root_, led)

    monkeypatch.setattr(A, "link_postings", link_while_clicking)
    mail_sync.sync(root, say=lambda *_: None)
    clicks[0].join(5)
    final = A.load(root)["apps"]
    assert final[flagged]["company"] == "Acme"
    assert any(a["req_id"] == "R-123456" for a in final.values())
    assert pipeline.status(pipeline.load(root), "gbx-R-123456") == "applied"


# ── tracked postings ─────────────────────────────────────────────────────────

def test_a_different_requisition_never_links_however_alike_the_title(root, ledger):
    """The first real dry run linked one requisition to another's posting on
    its title - and would have closed the wrong posting."""
    feed(ledger, gbx_receipt("R-111111", "2026-09-15T10:00:00+00:00"))
    feed(ledger, gbx_receipt("R-123456", "2026-09-24T10:00:00+00:00"))
    links = dict(A.link_postings(root, ledger))
    assert links == {by_req(ledger, "R-123456")["id"]: "gbx-R-123456"}


def test_an_oracle_job_number_is_a_requisition_too(root, ledger):
    """A rejection for "Applied AI ML Associate" linked on its title to
    "Applied AI/ML Associate Senior" - a different, open job - and closed it."""
    path = root.var / "triage-results.json"
    blob = json.loads(path.read_text())
    for rid, title in (("210000003", "Applied AI/ML Associate Senior"), ("210000001", "Software Engineer II")):
        blob["postings"][f"initech-{rid}"] = {"id": f"initech-{rid}", "title": title, "verdict": "keep",
                                              "board": "oracle:initech.fa.oraclecloud.com@CX_1@India",
                                              "company": "Initech"}
    path.write_text(json.dumps(blob))
    oracle = "no-reply@workflow.mail.us2.cloud.oracle.com"
    for n, role in (("210000002", "Applied AI ML Associate"), ("210000001", "Software Engineer II")):
        feed(ledger, mail(oracle, f"Your job application status (Job number: {n})",
                          f"Thank you for your interest in the {role} position at Initech. We're moving "
                          "forward with other candidates.", name="Initech Corp. Human Resources"))
    links = dict(A.link_postings(root, ledger))
    assert links == {by_req(ledger, "210000001")["id"]: "initech-210000001"}
    assert A.posting_req("initech-210000003") == "210000003" and A.posting_req("1000001") is None


def test_a_rejection_closes_the_tracked_posting_with_a_note(root, ledger):
    pipeline.set_status(root, "1000001", "applied")
    feed(ledger, mail("no-reply@initech.example", "Thank you for applying to Initech - Senior Backend Engineer",
                      "We received your application.", date="2026-09-23T10:00:00+00:00"))
    feed(ledger, mail("no-reply@initech.example", "Thank you for your interest in Initech || Senior Backend Engineer",
                      "We have decided to proceed with other candidates.", date="2026-09-25T10:00:00+00:00"))
    A.link_postings(root, ledger)
    (change,) = A.sync_pipeline(root, ledger)
    assert change["from"] == "applied" and change["to"] == "closed"
    entry = pipeline.load(root)["1000001"]
    assert entry["status"] == "closed" and "rejected" in entry["history"][-1]["note"]


def test_the_pipeline_never_moves_backwards_or_over_the_persons_decision(root, ledger):
    pipeline.set_status(root, "gbx-R-123456", "interviewing")
    feed(ledger, gbx_receipt("R-123456", "2026-09-24T10:00:00+00:00"))     # a late receipt
    A.link_postings(root, ledger)
    assert A.sync_pipeline(root, ledger) == []
    assert pipeline.status(pipeline.load(root), "gbx-R-123456") == "interviewing"

    pipeline.set_status(root, "gbx-R-123456", "skipped")
    feed(ledger, gbx_rejection("R-123456", "2026-09-30T10:00:00+00:00"))
    assert A.sync_pipeline(root, ledger) == []
    assert pipeline.status(pipeline.load(root), "gbx-R-123456") == "skipped"


def test_an_unreadable_ledger_is_an_error_not_an_empty_one(root):
    """Empty would rebuild from sixty days of mail and lose every correction."""
    A.path(root).write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        A.load(root)


# ── a guess is not a status ─────────────────────────────────────────────────

def _ev(day, status, confidence="high"):
    return {"date": f"2026-09-{day}T10:00:00+00:00", "status": status, "confidence": confidence}


def test_an_unsure_reading_alone_is_checking_not_a_status():
    """A marketplace's "53 roles awaiting your response" showed as an interview."""
    assert A.status_of([_ev(29, "interviewing", "low")]) == "checking"


def test_an_unsure_reading_never_moves_a_status_on():
    assert A.status_of([_ev(10, "applied"), _ev(29, "rejected", "low")]) == "applied"


def test_a_confirmed_reading_counts():
    evs = [_ev(10, "applied"), _ev(20, "interviewing", "low")]
    assert A.status_of(evs, confirmed_at="2026-09-21T00:00:00+00:00") == "interviewing"
    assert A.status_of(evs, confirmed_at="2026-09-15T00:00:00+00:00") == "applied"   # confirmed before it came


def test_checking_is_open_and_moves_no_posting():
    assert "checking" in A.OPEN and "checking" not in A.STAGE_OF
