"""Jev: held to its limit, and scored only against answers we can stand behind."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from jobsearch import applications as A
from jobsearch import mail_eval as E
from jobsearch.jev import Jev
from jobsearch.limits import Meter
from jobsearch.mailbox import Mail
from samples import make_root


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "test-key")
    monkeypatch.setattr("jobsearch.jev.time.sleep", lambda s: None)
    return make_root(tmp_path)


class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        return self._body


ANSWER = {"model": "jev-1.13.0", "usage": {"input_tokens": 321, "output_tokens": 4},
          "answers": {"kind": {"type": "choice", "choice": "rejected", "confidence": 0.97,
                               "probabilities": {"rejected": 0.98}}}}


def test_an_answer_and_its_cost_are_recorded(root, monkeypatch):
    sent = []
    monkeypatch.setattr("jobsearch.jev.requests.post", lambda url, **k: sent.append(k["json"]) or _Resp(200, ANSWER))
    ans = Jev(root).ask("We regret to inform you.", E.QUESTION)
    assert ans.ok and ans.answers["kind"]["choice"] == "rejected" and ans.model == "jev-1.13.0"
    assert sent[0]["model"] == "jev-latest" and sent[0]["questions"] == E.QUESTION
    assert Meter(root.root).used("jev") == {"requests": 1, "tokens": 321}
    assert Meter(root.root, root.id).used_by("jev", root.id)["tokens"] == 321      # counted against her


def test_overloaded_is_retried_then_answered(root, monkeypatch):
    replies = [_Resp(529), _Resp(429), _Resp(200, ANSWER)]
    monkeypatch.setattr("jobsearch.jev.requests.post", lambda url, **k: replies.pop(0))
    assert Jev(root).ask("x", E.QUESTION).ok and Meter(root.root).used("jev")["requests"] == 3


def test_past_the_cap_nothing_is_sent(root, monkeypatch):
    (root.data / "llm-limits.yaml").write_text("jev: {requests_per_day: 1, tokens_per_day: 1000}\n")
    Meter(root.root).record("jev", 10)
    monkeypatch.setattr("jobsearch.jev.requests.post", lambda *a, **k: pytest.fail("sent past the cap"))
    ans = Jev(root).ask("x", E.QUESTION)
    assert not ans.ok and ans.limited.startswith("Jev: daily request limit reached")


def test_without_a_key_nothing_is_sent(root, monkeypatch):
    monkeypatch.delenv("JEV_API_KEY")
    monkeypatch.setattr("jobsearch.jev.requests.post", lambda *a, **k: pytest.fail("sent without a key"))
    assert Jev(root).ask("x", E.QUESTION).error == "JEV_API_KEY not set"


# ── what counts as a known answer ───────────────────────────────────────────

def _ev(mid, kind, confidence="high", date="2026-09-10T00:00:00+00:00", **kw):
    return {"message_id": mid, "kind": kind, "confidence": confidence, "date": date, **kw}


def test_answers_are_graded_by_how_they_were_settled():
    ledger = {"apps": {
        "a": {"id": "a", "events": [_ev("<1>", "rejected"),                         # the rules were sure
                                    _ev("<2>", "interview", "low"),                 # a guess
                                    _ev("<3>", "received", read_by="groq, confirmed"),
                                    _ev("<4>", "viewed")]},                         # not scorable
        "b": {"id": "b", "confirmed_at": "2026-09-11T00:00:00+00:00",
              "events": [_ev("<5>", "interview", "low")]},                          # the person confirmed it
        "c": {"id": "c", "ignored": True, "events": [_ev("<6>", "interview", "low")]}}}
    k = E.known_answers(ledger)
    assert (k["<1>"]["truth"], k["<1>"]["source"]) == ("rejected", "rules")
    assert k["<2>"]["source"] == "unsure" and "<4>" not in k
    assert k["<3>"]["source"] == k["<5>"]["source"] == "confirmed"
    assert (k["<6>"]["truth"], k["<6>"]["source"]) == ("not_an_application", "confirmed")


def test_a_guess_is_never_scored_only_shown():
    rows = [{"truth": "interview", "source": "unsure", "jev": "not_an_application", "rules": "interview",
             "confidence": 0.95, "date": "d", "domain": "x", "subject": "s"},
            {"truth": "rejected", "source": "confirmed", "jev": "rejected", "rules": "rejected",
             "confidence": 0.99, "date": "d", "domain": "x", "subject": "s"}]
    s = E.summarise(rows)
    assert s["jev_overall"] == "1/1" and len(s["unsure"]) == 1 and s["wrong"] == []


# ── what leaves, and what is kept ───────────────────────────────────────────

BODY = "Dear Kavya, we regret to inform you. Call 0000011111 or write to hr.person@acme.com."


class FakeInbox:
    def __init__(self, root):
        self.user = "kavya.example@example.com"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def new_uids(self, state, days):
        return [1, 2]

    def headers(self, uids):
        return [Mail(1, "<1>", "t", "2026-09-10T00:00:00+00:00", "no-reply@acme.com", "Acme", "Your application"),
                Mail(2, "<2>", "t", "2026-09-11T00:00:00+00:00", "alerts@hirist.tech", "Hirist", "New jobs for you")]

    def bodies(self, uids):
        return {1: BODY, 2: "Ten new Python roles near you."}


def test_only_redacted_text_leaves_and_no_body_is_kept(root, monkeypatch):
    A.save(root, {"apps": {"a": {"id": "a", "events": [_ev("<1>", "rejected")]}}, "seen": [],
                  "naukri": {"applied": {}, "status_pings": [], "neo": {}}})
    monkeypatch.setattr("jobsearch.mailbox.Inbox", FakeInbox)
    sent = []
    monkeypatch.setattr("jobsearch.jev.requests.post", lambda url, **k: sent.append(k["json"]["state"]) or _Resp(200, ANSWER))
    res = E.run(root, negatives=1, say=lambda *_: None)
    assert len(sent) == 2 and "From: Acme (@acme.com)" in sent[0]
    assert not any(s in sent[0] for s in ("0000011111", "hr.person", "Kavya"))
    saved = (root.var / "mail" / "jev-eval.json").read_text()
    assert "regret to inform" not in saved and "Ten new Python roles" not in saved
    rows = json.loads(saved)["rows"]
    assert [r["source"] for r in rows] == ["rules", "dropped"] and all(len(r["sha256"]) == 64 for r in rows)
    assert res["summary"]["jev_overall"] == "1/2"            # right on the rejection, wrong on the alert
