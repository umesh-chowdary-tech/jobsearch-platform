"""Editing the profile on its own page (onboard.add_item, set_prefs, resave):
what a person adds is theirs, confirmed by writing it; once the profile is
saved, every change writes it again; removing something removes it. The
person and the resume are made up (test_onboard.py)."""
from __future__ import annotations

import pytest
import yaml

from jobsearch import onboard as O
from jobsearch.assemble import load_store
from jobsearch.web import routes
from jobsearch.web.routes import HOSTED
from jobsearch.profile import Profile
from jobsearch.web.runner import Runner
from samples import ROOT
from test_onboard import _confirm_all, _drafted


@pytest.fixture
def new(tmp_path):
    """A root with the shared data, and a profile that doesn't exist yet."""
    import shutil
    shutil.copytree(ROOT / "data", tmp_path / "data")
    return Profile(tmp_path, "neha")


def _saved(prof):
    _drafted(prof)
    _confirm_all(prof)
    O.save(prof)
    return prof


def _step(prof, step, body):
    status, out = routes.post(prof, f"/api/setup/{step}", body, Runner(prof), scope=HOSTED)
    assert status == 200
    return out


def _yaml(prof, name):
    return yaml.safe_load(prof.file(name).read_text(encoding="utf-8"))


def _entry(prof, eid):
    return next((e for e in load_store(prof)["entries"] if e["id"] == eid), None)


def test_what_a_person_adds_is_confirmed_and_theirs(new):
    _saved(new)
    job = O.add_item(new, "job", {"employer": "Acme Labs", "title": "Intern", "start": "2018-05", "end": "2018-07"})
    work = O.add_item(new, "entry", {"type": "project", "title": "Expense splitter", "headline": "A bill-splitting app"})
    O.add_item(new, "fact", {"text": "Used by my hostel floor"}, work["id"])
    O.add_item(new, "metric", {"claim": "about 40 people used it", "measured": False}, work["id"])
    O.add_item(new, "tech", {"name": "Flutter"}, work["id"])
    assert job["state"] == work["state"] == "confirmed" and job["by"] == work["by"] == "you"
    O.save(new, replace=True)
    store = load_store(new)
    assert "Acme Labs" in [j["employer"] for j in store["identity"]["employment"]]
    mine = _entry(new, work["id"])
    assert mine["facts"] == ["Used by my hostel floor"] and mine["tech"] == ["Flutter"]
    assert mine["metrics"] == [{"measured": False, "claim": "about 40 people used it", "estimate": True}]
    assert mine["sources"][0].startswith("typed by you")


def test_a_result_says_whether_it_was_measured(new):
    _drafted(new)
    with pytest.raises(O.SetupError, match="measured"):
        O.add_item(new, "metric", {"claim": "cut costs by 30%"}, "e1")


@pytest.mark.parametrize("kind, fields, why", [
    ("job", {"employer": "Acme", "start": "July 2021"}, "YYYY-MM"),
    ("job", {"title": "Engineer"}, "who you worked for"),
    ("entry", {"type": "hobby", "headline": "x"}, "type must be"),
    ("identity", {"field": "aadhaar", "value": "x"}, "field must be"),
    ("fact", {"text": "x" * 601}, "under 600"),
    ("fact", {"text": "x", "source": "made up"}, "can't be given"),
])
def test_what_cant_be_added_says_why(new, kind, fields, why):
    _drafted(new)
    with pytest.raises(O.SetupError, match=why):
        O.add_item(new, kind, fields, "e1")


def test_a_detail_the_draft_has_is_changed_in_place(new):
    _drafted(new)
    O.add_item(new, "identity", {"field": "phone", "value": "+91 00000 22222"})
    phones = [it for it in O.load_draft(new)["items"] if it.get("field") == "phone"]
    assert len(phones) == 1 and phones[0]["value"] == "+91 00000 22222" and phones[0]["state"] == "confirmed"


def test_a_skill_already_named_under_the_work_is_confirmed_not_added_twice(new):
    _drafted(new)
    O.add_item(new, "tech", {"name": "go"}, "e1")
    gos = [it for it in O.load_draft(new)["items"] if it["kind"] == "tech" and it["entry"] == "e1"
           and it["name"].lower() == "go"]
    assert len(gos) == 1 and gos[0]["state"] == "confirmed"


def test_before_the_first_save_a_change_writes_nothing(new):
    _drafted(new)
    _confirm_all(new)
    _step(new, "add", {"kind": "fact", "entry": "e1", "fields": {"text": "On call for payments"}})
    assert not new.exists()


def test_once_saved_each_change_writes_the_profile_again(new):
    _saved(new)
    _step(new, "add", {"kind": "fact", "entry": "e1", "fields": {"text": "On call for payments"}})
    assert "On call for payments" in _entry(new, "e1")["facts"]
    _step(new, "item", {"id": "e1-f1", "fields": {"text": "handling about 40,000 refunds a day"}})
    assert "handling about 40,000 refunds a day" in _entry(new, "e1")["facts"]


def test_removing_on_the_profile_page_removes_it(new):
    _saved(new)
    assert _entry(new, "e2") and "React" in yaml.safe_dump(_yaml(new, "skills.yaml"))
    _step(new, "item", {"id": "e2", "state": "removed"})
    assert _entry(new, "e2") is None
    assert "React" not in yaml.safe_dump(_yaml(new, "skills.yaml"))       # it came only from that work
    phone = next(it["id"] for it in O.load_draft(new)["items"] if it.get("field") == "phone")
    _step(new, "item", {"id": phone, "state": "removed"})
    assert not load_store(new)["identity"]["phone"]


def test_a_removed_result_and_an_unticked_cut_first_stay_gone(new):
    _saved(new)
    m = O.add_item(new, "metric", {"claim": "handling 40,000 refunds a day", "measured": True}, "e1")
    _step(new, "labels", {"entry": "e1", "cut_first": True})
    assert _entry(new, "e1")["metrics"] and _entry(new, "e1")["priority"] == "low"
    _step(new, "item", {"id": m["id"], "state": "removed"})
    _step(new, "labels", {"entry": "e1", "cut_first": False})
    assert not _entry(new, "e1")["metrics"] and "priority" not in _entry(new, "e1")


def test_a_skill_marked_not_mine_leaves_the_profile(new):
    _saved(new)
    _step(new, "skills", {"tiers": {"PostgreSQL": "remove"}})
    assert "PostgreSQL" not in yaml.safe_dump(_yaml(new, "skills.yaml"))


def test_preferences_are_kept_and_written_into_the_job_filters(new):
    _saved(new)
    s = _step(new, "prefs", {"prefs": {"preferred": "Pune, Remote", "acceptable": "", "watch": "Globex",
                                       "remote": False, "years": 4, "max_years": 6, "level": "ic", "skip": ["qa"]}})
    assert s["prefs"]["preferred"] == ["Pune", "Remote"] and s["prefs"]["watch"] == ["Globex"]
    search = _yaml(new, "search-profile.yaml")
    assert search["location"]["preferred"] == ["Pune", "Remote"] and search["location"]["remote_terms"] == []
    assert search["seniority"]["years_experience"] == 4 and search["companies_to_watch"] == ["Globex"]
    assert "qa engineer" in search["reject_function"]


@pytest.mark.parametrize("prefs, why", [({"level": "boss"}, "level"), ({"years": "x"}, "number"),
                                        ({"years": 99}, "between"), ({"skip": ["nope"]}, "skip"),
                                        ({"preferred": ["x" * 81]}, "under 80")])
def test_preferences_that_dont_make_sense_are_refused(new, prefs, why):
    _drafted(new)
    with pytest.raises(O.SetupError, match=why):
        O.set_prefs(new, prefs)


def test_a_change_that_leaves_nothing_to_write_waits_and_says_why(new):
    _saved(new)
    s = _step(new, "item", {"id": "id-name", "state": "removed"})
    assert s["blocked"] == "add your name first"
    assert load_store(new)["identity"]["name"] == "Neha Example"           # the saved profile is untouched
    s = _step(new, "item", {"id": "id-name", "state": "confirmed"})
    assert s["blocked"] == "" and s["saved_at"]


def test_a_piece_of_work_and_what_is_listed_under_it_are_added_together(new):
    _drafted(new)
    _confirm_all(new)
    O.save(new)
    _step(new, "item", {"id": "e2", "state": "removed"})
    under = [it["id"] for it in O.load_draft(new)["items"] if it.get("entry") == "e2"]
    _step(new, "items", {"ids": ["e2", *under], "state": "confirmed"})
    assert _entry(new, "e2")["tech"]
    with pytest.raises(routes.ApiError):
        _step(new, "items", {"ids": "e2", "state": "confirmed"})


def test_a_line_the_ai_couldnt_use_is_added_word_for_word_or_let_go(new):
    d, _ = _drafted(new)
    lines = [n for n, g in enumerate(d["dropped"]) if g.get("entry") and g.get("source")]
    s = _step(new, "restore", {"index": lines[0], "add": True})
    mine = next(it for it in s["items"] if it.get("by") == "resume line")
    assert mine["state"] == "confirmed" and mine["text"] == mine["source"]
    left = len(s["dropped"])
    assert len(_step(new, "dismiss", {"index": 0})["dropped"]) == left - 1
    with pytest.raises(O.SetupError, match="no such"):
        _step(new, "dismiss", {"index": 99})


def test_the_hosted_service_takes_the_new_steps():
    for step in ("add", "prefs", "items", "dismiss"):
        assert routes.allowed("POST", f"/api/setup/{step}", HOSTED)


def test_a_saved_change_to_skills_is_seen_at_once_by_a_running_service(new):
    """The skill index was cached by path: a hosted process went on matching
    jobs against the skills from before the person's last save."""
    from jobsearch.skills import load_index
    _saved(new)
    assert load_index(new).claims["PostgreSQL"].tier == "working"
    _step(new, "skills", {"tiers": {"PostgreSQL": "core"}})
    assert load_index(new).claims["PostgreSQL"].tier == "core"
