"""Learned rules: what may be proposed, what the replay shows, and what
approving does. A rule is approved on what it changes, never on its words."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from jobsearch import applications as A
from jobsearch import mail_learn as L
from jobsearch import mail_rules as R
from samples import make_root

ROOT = Path(__file__).resolve().parents[1]
DATA = str(ROOT / "data")
AGENCY = ("Good news! Our partner recruitment agency just forwarded your profile to the hiring team, and "
          "they will review it this week.")


# ── what may be proposed ─────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase, means, why", [
    ("forwarded your profile", "received", "4 to 15 words"),
    ("just forwarded your profile to the hiring team", "offerish", "isn't an outcome"),
    ("forwarded your profile to [you]", "received", "removed detail"),
    ("they will call you tomorrow morning", "received", "isn't in the email"),
    ("forwarded your profile to the Acme hiring team", "received", "names the company"),
])
def test_a_phrase_that_would_not_carry_is_refused(phrase, means, why):
    text = AGENCY.replace("the hiring team", "the Acme hiring team") if "Acme" in phrase else AGENCY
    assert why in (L.check_phrase(phrase, means, [text], "Acme", "Data Engineer", "unclear") or "")


def test_a_phrase_the_rules_already_know_is_refused():
    why = L.check_phrase("thank you for applying to us", "received", ["Thank you for applying to us today."],
                         "Acme", "", already="received")
    assert "already" in why


def test_a_general_phrase_is_accepted():
    assert L.check_phrase("just forwarded your profile to the hiring team", "received", [AGENCY],
                          "Acme", "Data Engineer", "unclear") is None


@pytest.mark.parametrize("address, company, key", [
    ("gbx@myworkday.com", "Globex", "gbx@myworkday.com"),          # a Workday tenant
    ("noreply@myworkday.com", "Globex", None),                       # Workday's shared sender
    ("no-reply@us.greenhouse-mail.io", "Initech", None),             # a platform: every company
    ("sam@connect.hackajob.com", "Acme", None),                      # a person at a job board
    ("jane@staffing-agency.example", "Globex", None),                # an agency, not the employer
    ("someone@gmail.com", "Acme", None),
    ("interviews@globex.com", "Globex", "globex.com"),               # the company's own domain
    # Keka's hiring platform mails for many employers: sharing a name with
    # one of them (Keka Technologies) doesn't make it that one's domain.
    ("careers@kekamail.com", "Keka Technologies", None),
])
def test_only_a_sender_that_speaks_for_one_company_can_be_learned(address, company, key):
    assert L.sender_key(address, company, DATA) == key


def test_a_sender_already_seen_writing_for_another_company_is_refused():
    ledger = {"apps": {"a": {"id": "a", "company": "Umbrella", "company_source": "manual", "ignored": False,
                             "events": [{"sender": "hr@acme-jobs.com"}]}}}
    assert "Umbrella" in L.sender_conflict(ledger, "acme-jobs.com", "Acme")


# ── the replay ───────────────────────────────────────────────────────────────

def _mail(i, subject, body, sender="hr@example.com"):
    return SimpleNamespace(message_id=f"<m{i}>", date="2026-09-20T10:00:00+00:00", subject=subject, body=body,
                           from_addr=sender, domain=sender.rsplit("@", 1)[-1])


def test_the_replay_lists_exactly_the_emails_it_would_change():
    mails = [_mail(1, "Update", AGENCY),
             _mail(2, "Update", "If our agency just forwarded your profile to the hiring team, expect a call."),
             _mail(3, "Thank you for applying", "We received your application.")]
    ledger = {"apps": {"x": {"id": "x", "company": "Acme", "events": [{"message_id": "<m1>"}]}}}
    changes = L.phrase_changes(mails, DATA, "received", "just forwarded your profile to the hiring team", ledger)
    assert [c["message_id"] for c in changes] == ["<m1>"]           # not the if-clause, not the receipt
    assert (changes[0]["from"], changes[0]["to"], changes[0]["app_id"]) == ("unclear", "received", "x")


# ── approving and rejecting ──────────────────────────────────────────────────

@pytest.fixture
def root(tmp_path):
    prof = make_root(tmp_path)
    # Start from no learned rules: the sample's file has some (checked by
    # tests/test_mail_learned.py), and these tests count entries.
    prof.file("mail-learned.yaml").write_text("phrases: []\nsenders: []\n", encoding="utf-8")
    ev = {"date": "2026-09-20T10:00:00+00:00", "kind": "unclear", "status": "applied", "confidence": "low",
          "subject": "Update", "sender": "gbx@myworkday.com", "message_id": "<m1>", "thread": "",
          "evidence": "", "matched_by": "new"}
    A.save(prof, {"apps": {"x": {"id": "x", "company": "", "company_source": "", "role": "", "req_id": "",
                                     "status": "applied", "first_seen": "2026-09-20", "updated": "2026-09-20",
                                     "ignored": False, "look": ["no company name in the email"], "events": [ev]}},
                      "seen": [], "naukri": {"applied": {}, "status_pings": [], "neo": {}}})
    return prof


def _phrase_proposal(root):
    p = {"id": L._key("phrase", "just forwarded your profile to the hiring team", "received"), "type": "phrase",
         "status": "pending", "phrase": "just forwarded your profile to the hiring team", "means": "received",
         "example": AGENCY.split(", and")[0], "replayed": 40, "proposed_at": "2026-09-26T10:00:00+00:00",
         "learned_from": {"app_id": "x", "company": "", "subject": "Update", "date": "2026-09-20"},
         "changes": [{"message_id": "<m1>", "app_id": "x", "date": "2026-09-20", "subject": "Update",
                      "company": "", "from": "unclear", "from_confidence": "low", "to": "received",
                      "to_confidence": "high", "evidence": AGENCY}]}
    assert L._add(root, p)
    return p


def test_approving_a_phrase_writes_it_applies_it_and_reads_new_mail_with_it(root):
    p = _phrase_proposal(root)
    L.approve(root, p["id"])
    doc = yaml.safe_load(L.learned_path(root).read_text(encoding="utf-8"))
    assert doc["phrases"][0]["phrase"] == p["phrase"] and doc["phrases"][0]["replay"] == "changed 1 of 40 emails"
    e = A.load(root)["apps"]["x"]["events"][0]
    assert (e["kind"], e["confidence"]) == ("received", "high")
    v = R.classify("hr@other.com", "Update", "Great news: we just forwarded your profile to the hiring team.",
                   root)
    assert (v.kind, v.confidence) == ("received", "high")
    with pytest.raises(ValueError):
        L.approve(root, p["id"])                                    # decided once


def test_approving_a_sender_names_the_records_it_listed(root):
    p = {"id": L._key("sender", "gbx@myworkday.com"), "type": "sender", "status": "pending",
         "sender": "gbx@myworkday.com", "company": "Globex", "example": "a career with Globex",
         "learned_from": {"app_id": "x", "company": "", "subject": "Update", "date": "2026-09-20"},
         "changes": L.sender_changes(A.load(root), "gbx@myworkday.com", "Globex"),
         "proposed_at": "2026-09-26T10:00:00+00:00"}
    assert [c["app_id"] for c in p["changes"]] == ["x"]
    L._add(root, p)
    L.approve(root, p["id"])
    a = A.load(root)["apps"]["x"]
    assert (a["company"], a["company_source"]) == ("Globex", "learned")
    assert R.company("gbx@myworkday.com", "", "Thanks", root) == ("Globex", "learned")


def test_a_rejected_rule_is_never_proposed_again(root):
    p = _phrase_proposal(root)
    L.reject(root, p["id"])
    assert not L._add(root, {**p, "status": "pending"})
    assert not L.learned_path(root).read_text(encoding="utf-8").count("forwarded")
    assert L.pending(root) == []


def test_a_phrase_that_changes_nothing_is_not_proposed(root, monkeypatch):
    monkeypatch.setattr(L, "replay_phrase", lambda *a, **k: ([], 40))
    app = A.load(root)["apps"]["x"]
    emails = [(app["events"][0], "", AGENCY)]
    payload = {"emails": [{"subject": "Update", "text": AGENCY}]}
    made = L.propose_from(root, app, emails, {"phrase": "just forwarded your profile to the hiring team",
                                              "phrase_means": "received"}, {}, payload, root, say=lambda *_: None)
    assert made == 0 and L.load_proposals(root) == {}


def test_the_sync_reads_with_approved_rules(root):
    """mail_sync passes the profile, so an approved phrase is live."""
    p = _phrase_proposal(root)
    L.approve(root, p["id"])
    src = (ROOT / "src" / "jobsearch" / "mail_sync.py").read_text(encoding="utf-8")
    assert "rules.classify(m.from_addr, m.subject, m.body, data)" in src
