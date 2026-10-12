"""Questions written by the model for one person, kept with their answers;
answers that become facts only through the same checks as everything else.
Everyone here is made up."""
from __future__ import annotations

import shutil

import pytest
import yaml

from jobsearch import onboard as O
from jobsearch import setup_interview as Q
from jobsearch.grounding import untraced_bullets
from jobsearch.llm import Answer
from jobsearch.profile import Profile
from samples import ROOT
from test_setup_merge import ANSWERS, MERGE_SYSTEM, OLD

QUESTIONS = {
    "questions": [
        {"text": "What was your own part in building the refund service?", "why": "Recruiters look for ownership.",
         "about": ["e1", "made-up-id"], "kind": "ownership", "options": ["Designed it", "Built parts of it"]},
        {"text": "What is your current salary and notice period?", "why": "x", "about": [], "kind": "other"},
        {"text": "Where did you use Kubernetes, if anywhere?", "why": "It's only in your skills list.",
         "about": ["e1-t1"], "kind": "skill"},
        {"text": "What was your own part in building the refund service?", "about": ["e1"]},            # repeated
        {"text": "Short?", "about": []},
        {"text": "Do you know how many refunds a day it handled at peak, and was that measured?",
         "about": ["e1-f1"], "kind": "impact"},
    ],
    "angles": {"e1": ["backend", "made-up-angle", "data"], "nope": ["backend"]},
    "cut_first": ["e1", "nope"],
}
ANSWER_TEXT = {
    "q1": "I designed the retry logic and built the refund API myself.",
    "q2": "I deployed it to Kubernetes for the refund service.",
    "q3": "Our dashboard showed 52,000 refunds a day at peak, tracked in Grafana. Client was Acme Bank.",
}
UPDATES = {"updates": [
    {"question": "q1", "action": "add_fact", "entry": "e1", "text": "designed the retry logic and built the refund API",
     "source": "I designed the retry logic and built the refund API myself."},
    {"question": "q1", "action": "add_fact", "entry": "e1", "text": "led the refund API",                  # stronger
     "source": "I designed the retry logic and built the refund API myself."},
    {"question": "q2", "action": "used_skill", "tech": "e1-t1", "source": "I deployed it to Kubernetes"},
    {"question": "q3", "action": "add_metric", "entry": "e1", "claim": "52,000 refunds a day at peak",
     "measured": True, "source": "Our dashboard showed 52,000 refunds a day at peak, tracked in Grafana."},
    {"question": "q3", "action": "add_metric", "entry": "e1", "claim": "60,000 refunds a day",              # not said
     "measured": True, "source": "Our dashboard showed 52,000 refunds a day at peak"},
    {"question": "q3", "action": "set_field", "item": "job-1", "field": "client", "value": "Acme Bank",
     "source": "Client was Acme Bank."},
    {"question": "q3", "action": "set_field", "item": "job-1", "field": "client", "value": "Hooli",
     "source": "Client was Acme Bank."},
    {"question": "q3", "action": "add_note", "entry": "e1", "text": "numbers tracked in Grafana",
     "source": "tracked in Grafana"},
    {"question": "q9", "action": "add_fact", "entry": "e1", "text": "x", "source": "x"},                   # never asked
    {"question": "q1", "action": "rewrite_everything", "source": "I designed"},
]}


class Model:
    available = True

    def __init__(self, questions=QUESTIONS, updates=UPDATES):
        self.questions, self.updates, self.sent = questions, updates, []

    def ask_json(self, system, user, **kw):
        self.sent.append((system, user, kw))
        if system == Q.QUESTION_SYSTEM:
            return Answer(ok=True, data=self.questions, provider="claude", model="claude-opus-5-5")
        if system == Q.APPLY_SYSTEM:
            return Answer(ok=True, data=self.updates, provider="claude", model="claude-opus-5-5")
        if system == MERGE_SYSTEM:
            return Answer(ok=True, data={}, provider="claude", model="claude-opus-5-5")
        return Answer(ok=True, data=ANSWERS["Helped build"], provider="claude", model="claude-opus-5-5")


@pytest.fixture
def drafted(tmp_path):
    shutil.copytree(ROOT / "data", tmp_path / "data")
    prof = Profile(tmp_path, "neha")
    O.upload(prof, OLD.encode(), "neha-2021.txt")
    O.draft(prof, llm=Model())
    return prof


def _qs(prof):
    return O.state(prof)["interview"]["questions"]


def test_questions_come_from_the_model_within_the_rules(drafted):
    m = Model()
    O.ask(drafted, llm=m)
    qs = _qs(drafted)
    assert [q["id"] for q in qs] == ["q1", "q2", "q3"]
    assert [q["kind"] for q in qs] == ["ownership", "skill", "impact"]
    assert qs[0]["about"] == ["e1"] and qs[0]["options"] == ["Designed it", "Built parts of it"]
    assert not any("salary" in q["text"] for q in qs)                       # never asked, whatever the model says
    system, user, kw = m.sent[-1]
    assert kw["effort"] == "high" and "neha.example@example.com" not in user and "Ask at most 8" in user
    e1 = next(it for it in O.load_draft(drafted)["items"] if it["id"] == "e1")
    assert e1["angles"] == ["backend", "data"] and e1["priority"] == "low"


def test_open_questions_come_before_more_and_nothing_is_asked_twice(drafted):
    m = Model()
    O.ask(drafted, llm=m)
    with pytest.raises(O.SetupError, match="answer or skip"):
        O.ask(drafted, llm=m)
    O.answer(drafted, "q1", ANSWER_TEXT["q1"])
    O.answer(drafted, "q2", skip=True)
    O.answer(drafted, "q3", "I don't know.")
    O.ask(drafted, llm=m)
    assert [q["id"] for q in _qs(drafted)] == ["q1", "q2", "q3"]            # the model's repeats are dropped
    _, user, kw = m.sent[-1]
    assert "I designed the retry logic" in user and "(skipped)" in user and "Ask at most 5" in user


def test_answers_become_items_only_through_the_checks(drafted):
    O.ask(drafted, llm=Model())
    for qid, text in ANSWER_TEXT.items():
        O.answer(drafted, qid, text)
    d = O.apply_answers(drafted, llm=Model())
    items = {it["id"]: it for it in d["items"]}
    new = [it for it in d["items"] if it.get("by") == "answer"]
    assert sorted(it["kind"] for it in new) == ["fact", "metric", "note"]
    metric = next(it for it in new if it["kind"] == "metric")
    assert metric["claim"] == "52,000 refunds a day at peak" and metric["measured"] is True
    assert metric["sources"] == [{"src": "q3", "quote": metric["source"]}] and metric["state"] == "pending"
    assert items["e1-t1"]["listed_only"] is False and items["e1-t1"]["answered"] == "q2"
    assert items["job-1"]["client"] == "Acme Bank"
    why = {g["what"]: g["why"] for g in d["dropped"] if g.get("src", "").startswith("q")}
    assert any("'led'" in w for w in why.values())
    assert any("60,000" in w for w in why.values())
    assert "its value isn't in your answer" in why.values() and "not an update setup knows" in why.values()
    assert all(q["applied"] for q in _qs(drafted))
    with pytest.raises(O.SetupError, match="already in your profile"):
        O.answer(drafted, "q1", "changed my mind")


def test_an_answer_that_contradicts_a_file_is_a_choice_not_an_overwrite(drafted):
    O.ask(drafted, llm=Model())
    O.answer(drafted, "q1", "I was there from 2020 to 2023.")
    upd = {"updates": [{"question": "q1", "action": "set_field", "item": "job-1", "field": "start", "value": "2020",
                        "source": "I was there from 2020 to 2023."}]}
    d = O.apply_answers(drafted, llm=Model(updates=upd))
    job = next(it for it in d["items"] if it["id"] == "job-1")
    assert job["conflict"] == "your answer differs from your files"
    assert [a["start"] for a in job["alternatives"]] == ["2021", "2020"]


def test_what_answers_add_reaches_the_profile_and_invents_nothing(drafted):
    O.ask(drafted, llm=Model())
    for qid, text in ANSWER_TEXT.items():
        O.answer(drafted, qid, text)
    O.apply_answers(drafted, llm=Model())
    for it in O.load_draft(drafted)["items"]:
        O.update_item(drafted, it["id"], "confirmed")
    O.set_labels(drafted, "e1", angles=["backend"], cut_first=False)
    O.save(drafted, O.state(drafted)["prefs"])
    store = yaml.safe_load(drafted.file("master-store.yaml").read_text(encoding="utf-8").split("\n\n", 1)[1])
    e1 = next(e for e in store["entries"] if e["id"] == "e1")
    assert e1["metrics"] == [{"measured": True, "claim": "52,000 refunds a day at peak"}]
    assert e1["notes"] == "numbers tracked in Grafana" and e1["angles"] == ["backend"] and "priority" not in e1
    assert "designed the retry logic and built the refund API" in e1["facts"]
    assert any(s.startswith('your answer to "') for s in e1["sources"])
    assert store["identity"]["employment"][0]["client"] == "Acme Bank"
    bullets = "\n".join(f"- {f}" for f in e1["facts"]) + "\n- 52,000 refunds a day at peak"
    assert untraced_bullets(bullets, store) == []


def test_an_estimate_is_kept_but_never_printed_as_a_number(drafted):
    O.ask(drafted, llm=Model())
    O.answer(drafted, "q3", "Roughly 52,000 refunds a day, my guess.")
    upd = {"updates": [{"question": "q3", "action": "add_metric", "entry": "e1", "claim": "52,000 refunds a day",
                        "measured": False, "source": "Roughly 52,000 refunds a day, my guess."}]}
    O.apply_answers(drafted, llm=Model(updates=upd))
    for it in O.load_draft(drafted)["items"]:
        O.update_item(drafted, it["id"], "confirmed")
    O.save(drafted, O.state(drafted)["prefs"])
    store = yaml.safe_load(drafted.file("master-store.yaml").read_text(encoding="utf-8").split("\n\n", 1)[1])
    e1 = next(e for e in store["entries"] if e["id"] == "e1")
    assert e1["metrics"] == [{"measured": False, "claim": "52,000 refunds a day", "estimate": True}]
    assert untraced_bullets("- 52,000 refunds a day", store) == ["52,000 refunds a day"]


def test_the_person_says_whether_a_result_was_measured(drafted):
    O.ask(drafted, llm=Model())
    O.answer(drafted, "q3", "52,000 refunds a day at peak.")
    upd = {"updates": [{"question": "q3", "action": "add_metric", "entry": "e1", "claim": "52,000 refunds a day at peak",
                        "measured": None, "source": "52,000 refunds a day at peak."}]}
    d = O.apply_answers(drafted, llm=Model(updates=upd))
    mid = next(it["id"] for it in d["items"] if it["kind"] == "metric")
    assert O.update_item(drafted, mid, fields={"measured": True})["measured"] is True
    with pytest.raises(O.SetupError, match="yes or no"):
        O.update_item(drafted, mid, fields={"measured": "maybe"})


@pytest.mark.parametrize("text", [
    "What is your date of birth?", "Are you married?", "What is your current CTC?", "What is your notice period?",
    "Which religion do you follow?", "What is your Aadhaar number?", "Can you share your father's name?"])
def test_personal_questions_are_never_asked(drafted, text):
    O.ask(drafted, llm=Model(questions={"questions": [{"text": text, "about": []}]}))
    assert _qs(drafted) == []


@pytest.mark.parametrize("text", ["Was Globex the parent company of the team you worked in?",
                                  "Was the refund service for a healthcare client?"])
def test_work_questions_that_brush_those_words_still_are(drafted, text):
    O.ask(drafted, llm=Model(questions={"questions": [{"text": text, "about": ["e1"]}]}))
    assert [q["text"] for q in _qs(drafted)] == [text]


def test_the_person_sets_the_labels_from_the_list(drafted):
    O.set_labels(drafted, "e1", angles=["ml", "ml", "data"], cut_first=True)
    e1 = next(it for it in O.load_draft(drafted)["items"] if it["id"] == "e1")
    assert e1["angles"] == ["ml", "data"] and e1["priority"] == "low" and e1["labels_by"] == "you"
    with pytest.raises(O.SetupError, match="angles must be from"):
        O.set_labels(drafted, "e1", angles=["rockstar"])
    O.ask(drafted, llm=Model())                                             # the model doesn't overrule them
    e1 = next(it for it in O.load_draft(drafted)["items"] if it["id"] == "e1")
    assert e1["angles"] == ["ml", "data"]


def test_the_hosted_service_offers_the_question_steps():
    from jobsearch.web.routes import HOSTED, allowed
    for step in ("ask", "answer", "apply", "labels"):
        assert allowed("POST", f"/api/setup/{step}", HOSTED)


def test_nothing_is_asked_before_anything_is_read(tmp_path):
    shutil.copytree(ROOT / "data", tmp_path / "data")
    prof = Profile(tmp_path, "neha")
    O.upload(prof, OLD.encode(), "a.txt")
    with pytest.raises(O.SetupError, match="read your files first"):
        O.ask(prof, llm=Model())
