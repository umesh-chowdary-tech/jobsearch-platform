"""Several documents, one person: the same work becomes one item quoting each,
and where the documents disagree the person chooses. Everyone here is made up."""
from __future__ import annotations

import json
import shutil

import pytest
import yaml

from jobsearch import onboard as O
from jobsearch.llm import Answer
from jobsearch.profile import Profile
from jobsearch.setup_merge import MERGE_SYSTEM
from samples import ROOT

OLD = """Neha Example
Hyderabad, India | neha.example@example.com
Software Engineer, Globex Payments 2021 - 2023
Helped build the refund service, handling 40,000 refunds a day
SKILLS
Go, PostgreSQL, Docker, Kubernetes
"""
NEW = """Neha Example
Hyderabad, India
Senior Software Engineer, Globex Payments 2023 - Present
Software Engineer, Globex Payments 2021 - 2023
Led the refund service, handling 45,000 refunds a day
Built the ledger export in Go
"""

ANSWERS = {
    "Helped build": {
        "name": {"value": "Neha Example", "source": "Neha Example"},
        "jobs": [{"employer": "Globex Payments", "title": "Software Engineer", "start": "2021", "end": "2023",
                  "source": "Software Engineer, Globex Payments 2021 - 2023"}],
        "entries": [{"type": "work", "where": "Globex Payments", "title": "Refund service", "period": "2021 to 2023",
                     "headline": {"text": "Helped build the refund service",
                                  "source": "Helped build the refund service, handling 40,000 refunds a day"},
                     "facts": [{"text": "handling 40,000 refunds a day",
                                "source": "Helped build the refund service, handling 40,000 refunds a day"}],
                     "tech": [{"name": "Kubernetes", "source": "Go, PostgreSQL, Docker, Kubernetes"}]}]},
    "Led the refund": {
        "name": {"value": "Neha Example", "source": "Neha Example"},
        "jobs": [{"employer": "Globex Payments", "title": "Senior Software Engineer", "start": "2023",
                  "end": "present", "source": "Senior Software Engineer, Globex Payments 2023 - Present"},
                 {"employer": "Globex Payments", "title": "Software Engineer", "start": "2021", "end": "2023",
                  "source": "Software Engineer, Globex Payments 2021 - 2023"}],
        "entries": [{"type": "work", "where": "Globex Payments", "title": "Refund service", "period": "2021 to 2023",
                     "headline": {"text": "Led the refund service",
                                  "source": "Led the refund service, handling 45,000 refunds a day"},
                     "facts": [{"text": "handling 45,000 refunds a day",
                                "source": "Led the refund service, handling 45,000 refunds a day"}],
                     "tech": []},
                    {"type": "work", "where": "Globex Payments", "title": "Ledger export", "period": "",
                     "headline": {"text": "Built the ledger export in Go", "source": "Built the ledger export in Go"},
                     "facts": [], "tech": [{"name": "Go", "source": "Built the ledger export in Go"}]}]},
}
PAIRS = {"same_entry": [{"new": "s2-e1", "old": "e1"}, {"new": "s2-e2", "old": "e1"}],    # the 2nd pairing is refused
         "same_fact": [{"new": "s2-e1-f1", "old": "e1-f1"}, {"new": "nope", "old": "e1-f1"}]}


class Model:
    """Answers each document by what it says, and the merge with PAIRS."""
    available = True

    def __init__(self, pairs=PAIRS, merge_ok=True):
        self.pairs, self.merge_ok, self.calls = pairs, merge_ok, []

    def ask_json(self, system, user, **kw):
        self.calls.append((system, kw))
        if system == MERGE_SYSTEM:
            return Answer(ok=self.merge_ok, data=self.pairs, provider="claude", model="claude-opus-5-5")
        key = next(k for k in ANSWERS if k in user)
        return Answer(ok=True, data=ANSWERS[key], provider="claude", model="claude-opus-5-5")


@pytest.fixture
def new(tmp_path):
    shutil.copytree(ROOT / "data", tmp_path / "data")
    return Profile(tmp_path, "neha")


def _both(new, model=None):
    model = model or Model()
    O.upload(new, OLD.encode(), "neha-2021.txt")
    O.upload(new, NEW.encode(), "neha-2024.txt")
    O.draft(new, llm=model)
    return O.draft(new, llm=model), model


def _items(d):
    return {it["id"]: it for it in d["items"]}


def test_each_document_is_read_by_the_setup_model_thinking_hard(new):
    _, model = _both(new)
    drafts = [kw for system, kw in model.calls if system != MERGE_SYSTEM]
    assert len(drafts) == 2 and all(kw["effort"] == "high" and kw["max_tokens"] == 16000 for kw in drafts)
    assert [s["drafted"] for s in O.state(new)["sources"]] == [True, True]


def test_the_same_work_becomes_one_item_quoting_both(new):
    d, _ = _both(new)
    it = _items(d)
    assert [s["src"] for s in it["id-name"]["sources"]] == ["s1", "s2"]
    jobs = [i for i in d["items"] if i["kind"] == "job"]
    assert sorted(j["title"] for j in jobs) == ["Senior Software Engineer", "Software Engineer"]   # a promotion
    se = next(j for j in jobs if j["title"] == "Software Engineer")
    assert [s["src"] for s in se["sources"]] == ["s1", "s2"]
    assert "s2-e2" in it and it["s2-e2"]["type"] == "work"            # paired once only: a new piece of work
    assert it["s2-e2-t1"]["entry"] == "s2-e2"


def test_where_documents_disagree_the_person_chooses(new):
    d, _ = _both(new)
    it = _items(d)
    fact, entry = it["e1-f1"], it["e1"]
    assert fact["conflict"] == "your files give different numbers"
    assert [a["text"] for a in fact["alternatives"]] == ["handling 40,000 refunds a day", "handling 45,000 refunds a day"]
    assert "says 'led'" in entry["conflict"]                          # the newer resume claims more
    with pytest.raises(O.SetupError, match="pick the true version"):
        O.update_item(new, "e1-f1", "confirmed")
    got = O.update_item(new, "e1-f1", "confirmed", choose=0)
    assert got["text"] == "handling 40,000 refunds a day" and got["state"] == "confirmed" and "conflict" not in got
    assert O.state(new)["progress"]["conflicts"] == 1
    # Neither version? Their own words settle it too.
    got = O.update_item(new, "e1", "confirmed", fields={"headline": "Worked on the refund service"})
    assert got["edited"] and got["state"] == "confirmed"


def test_a_skill_named_only_in_a_skills_list_is_marked(new):
    d, _ = _both(new)
    it = _items(d)
    assert it["e1-t1"]["name"] == "Kubernetes" and it["e1-t1"]["listed_only"] is True
    assert it["s2-e2-t1"]["listed_only"] is False


def test_a_confirmed_fact_a_new_document_contradicts_is_checked_again(new):
    model = Model()
    O.upload(new, OLD.encode(), "neha-2021.txt")
    O.draft(new, llm=model)
    O.update_item(new, "e1-f1", "confirmed")
    O.upload(new, NEW.encode(), "neha-2024.txt")
    d = O.draft(new, llm=model)
    assert _items(d)["e1-f1"]["state"] == "pending" and _items(d)["e1-f1"]["conflict"]


def test_no_comparison_means_no_pairing_and_says_so(new):
    d, _ = _both(new, Model(merge_ok=False))
    assert "s2-e1" in _items(d) and "e1" in _items(d)
    assert any("couldn't be compared" in n for n in O.state(new)["merge_notes"])


def test_removing_a_document_takes_what_only_it_said(new):
    _both(new)
    O.remove_source(new, "s2")
    it = _items(O.load_draft(new))
    assert "Senior Software Engineer" not in {i.get("title") for i in it.values() if i["kind"] == "job"}
    assert "s2-e2" not in it and "s2-e2-t1" not in it
    assert "conflict" not in it["e1-f1"] and it["e1-f1"]["text"] == "handling 40,000 refunds a day"
    assert [s["src"] for s in it["id-name"]["sources"]] == ["s1"]


def test_removing_the_first_document_keeps_the_others_version(new):
    _both(new)
    O.remove_source(new, "s1")
    fact = _items(O.load_draft(new))["e1-f1"]
    assert fact["text"] == "handling 45,000 refunds a day" and [s["src"] for s in fact["sources"]] == ["s2"]


def test_the_same_file_twice_and_a_sixth_resume_are_refused(new, monkeypatch):
    O.upload(new, OLD.encode(), "a.txt")
    with pytest.raises(O.SetupError, match="already added"):
        O.upload(new, OLD.encode(), "copy.txt")
    monkeypatch.setattr(O, "MAX_FILES", 1)
    with pytest.raises(O.SetupError, match="1 resumes already"):
        O.upload(new, NEW.encode(), "b.txt")


def test_a_file_is_named_and_its_personal_details_never_kept(new):
    text = OLD + "PERSONAL DETAILS\nDate of Birth: 01/01/1998\nFather's Name: R Example\n"
    info = O.upload(new, text.encode(), "Neha CV (2021).txt")
    s = O.state(new)["sources"][0]
    assert s["name"] == "Neha CV (2021).txt" and "date of birth" in " ".join(s["notes"])
    stored = (O.sources_dir(new) / "s1.txt").read_text(encoding="utf-8")
    for secret in ("01/01/1998", "R Example"):
        assert secret not in stored and secret not in info["will_send"]
    assert not list(O.setup_dir(new).glob("*.txt")) or not (O.setup_dir(new) / "resume.txt").exists()


def test_links_the_person_gives_are_theirs(new):
    O.add_link(new, "linkedin", "linkedin.com/in/neha-example-0/")
    it = _items(O.load_draft(new))["id-linkedin"]
    assert it["value"] == "https://www.linkedin.com/in/neha-example-0" and it["state"] == "confirmed"
    with pytest.raises(O.SetupError, match="aren't read"):
        O.add_link(new, "portfolio", "https://www.linkedin.com/in/neha-example-0")


def test_github_becomes_a_source_with_a_link_to_open(new):
    from test_setup_sources import Resp
    repos = [{"name": "tariff-helper", "description": "RAG over tariff schedules", "language": "Python",
              "fork": False, "pushed_at": "2025-06-01T00:00:00Z"}]
    O.add_link(new, "github", "https://github.com/neha-example",
               github_get=lambda url, accept: Resp(200, repos) if "sort=pushed" in url else Resp(404))
    s = O.state(new)
    assert s["sources"][0]["kind"] == "github" and s["links"]["github"] == "https://github.com/neha-example"
    assert "Repository: tariff-helper" in s["sources"][0]["will_send"]


def test_a_note_replaces_the_last_one(new):
    O.set_note(new, "I also built the internal on-call bot for the payments team.")
    O.set_note(new, "I mentored two interns on the payments team in 2023.")
    notes = [s for s in O.state(new)["sources"] if s["kind"] == "note"]
    assert len(notes) == 1 and "mentored" in (O.sources_dir(new) / f"{notes[0]['id']}.txt").read_text(encoding="utf-8")


def test_an_old_one_resume_draft_still_opens(new):
    d = O.setup_dir(new)
    d.mkdir(parents=True)
    (d / "resume.txt").write_text(OLD, encoding="utf-8")
    (d / "draft.json").write_text(json.dumps({"status": "drafted", "file": "resume.pdf", "lines": 6, "items": [
        {"id": "id-name", "kind": "identity", "field": "name", "value": "Neha Example", "source": "Neha Example",
         "state": "confirmed"}], "dropped": [], "skills": {}}), encoding="utf-8")
    s = O.state(new)
    assert [x["id"] for x in s["sources"]] == ["s1"] and s["sources"][0]["drafted"]
    assert s["items"][0]["sources"] == [{"src": "s1", "quote": "Neha Example"}]


def test_saving_again_keeps_what_was_added_by_hand(new):
    _both(new)
    for it in O.load_draft(new)["items"]:
        if it.get("alternatives") and it.get("conflict"):
            O.update_item(new, it["id"], choose=0)
        O.update_item(new, it["id"], "confirmed")
    O.save(new, O.state(new)["prefs"])
    store = yaml.safe_load(new.file("master-store.yaml").read_text(encoding="utf-8"))
    e1 = next(e for e in store["entries"] if e["id"] == "e1")
    assert any(s.startswith("neha-2021.txt: ") for s in e1["sources"])
    e1["notes"], e1["angles"] = "the refund service ran on AWS - I didn't set it up", ["backend"]
    store["entries"].append({"id": "hand-1", "type": "project", "title": "Typed by hand", "headline": "x",
                             "facts": [], "tech": []})
    store["identity"]["title"].update(descriptor_flexes=True, approved_descriptors=["Backend Engineer"])
    new.file("master-store.yaml").write_text(yaml.safe_dump(store), encoding="utf-8")
    skills = yaml.safe_load(new.file("skills.yaml").read_text(encoding="utf-8"))
    skills["banned"] = ["Kubernetes"]
    new.file("skills.yaml").write_text(yaml.safe_dump(skills), encoding="utf-8")

    O.save(new, O.state(new)["prefs"], replace=True)
    store = yaml.safe_load(new.file("master-store.yaml").read_text(encoding="utf-8").split("\n\n", 1)[1])
    e1 = next(e for e in store["entries"] if e["id"] == "e1")
    assert e1["notes"].startswith("the refund service") and e1["angles"] == ["backend"]
    assert any(e["id"] == "hand-1" for e in store["entries"])
    assert store["identity"]["title"]["approved_descriptors"] == ["Backend Engineer"]
    assert yaml.safe_load(new.file("skills.yaml").read_text(encoding="utf-8").split("\n\n", 1)[1])["banned"] == ["Kubernetes"]
