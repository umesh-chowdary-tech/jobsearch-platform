"""Role families from titles (roles.py), the offline match score (scoring.py),
and a person's verdicts read again when their profile changes."""
from __future__ import annotations

import argparse
import json
from datetime import date

import pytest

from jobsearch import pool, rank, triage_cli
from jobsearch import watch as W
from jobsearch.models import Posting
from jobsearch.roles import role_of, roles_of, skipped_families
from jobsearch.scoring import AI_MIN, ai_min, match, person_roles
from jobsearch.skills import load_index
from jobsearch.triage import FLAG, KEEP, NO_MATCH, REJECT, Triaged, load_profile, triage_posting
from samples import make_root


@pytest.mark.parametrize("title, role", [
    ("Senior Backend Engineer", "backend"),
    ("Software Engineer - Backend (Python)", "backend"),
    ("Software Engineer II", "software"),
    ("SDE 2", "software"),
    ("Data Engineer", "data"),
    ("Senior Data Scientist", "data"),
    ("Machine Learning Engineer", "ml"),
    ("Senior Engineer, AI Platform", "ml"),
    ("Engineering Manager, Data Platform", "management"),
    ("Sales Engineer", "solutions"),
    ("Solutions Architect", "solutions"),
    ("QA Automation Engineer", "qa"),
    ("SDET II", "qa"),
    ("Penetration Tester", "security"),
    ("Android Developer", "mobile"),
    ("iOS Engineer", "mobile"),
    ("Frontend Engineer (React)", "frontend"),
    ("Full Stack Developer", "fullstack"),
    ("Site Reliability Engineer", "devops"),
    ("Firmware Engineer", "embedded"),
    ("Product Designer", "design"),
    ("Product Manager, Payments", "product"),
    ("Technical Program Manager", "program"),
    ("Account Executive", "non-tech"),
    ("Business Development Manager", "non-tech"),
    ("Senior Recruiter", "non-tech"),
    ("Kotlin Backend Engineer", "backend"),
    ("Maintenance Supervisor", "other"),            # "ai" is a word, not three letters anywhere
    ("Chief of Staff", "other"),
    ("", "other"),
])
def test_a_title_names_a_kind_of_work(title, role):
    assert role_of(title) == role


def test_a_persons_own_titles_most_common_first():
    assert roles_of(["Backend Engineer", "Senior Backend Engineer", "Data Engineer", "Sales Manager"]) == ["backend", "data"]
    store = {"identity": {"title": {"official": "Software Engineer II",
                                    "approved_descriptors": ["Backend Software Engineer"]},
                          "employment": [{"title": "Backend Engineer"}]}}
    assert person_roles(store) == ["backend", "software"]
    assert person_roles({}) == []


def test_what_setup_skipped_is_known_by_family():
    from jobsearch.onboard import SKIP
    profile = {"reject_function": ["sales"] + SKIP["qa"] + SKIP["mobile"] + SKIP["data"][:1]}
    assert skipped_families(profile) == {"qa", "mobile"}           # data only partly: not skipped


# ── the score ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def prof(tmp_path_factory):
    return make_root(tmp_path_factory.mktemp("scoring"))


def _t(prof, title="Backend Engineer", location="Pune, India", description="Python, FastAPI and PostgreSQL. 3+ years.",
       posted="2026-10-14"):
    index, profile = load_index(prof), load_profile(prof.dir)
    t = triage_posting(index, profile, {"id": "1", "title": title, "location": location, "description": description,
                                        "board": "lever:acme", "company": "Acme", "posted_at": posted})
    return t, profile


def test_the_parts_add_up_and_stay_in_range(prof):
    t, profile = _t(prof)
    assert t.verdict == KEEP
    score, parts = match(t, profile, ["backend"])
    assert set(parts) == {"skills", "gaps", "role", "place", "level", "fresh"}
    assert score == max(0, min(100, sum(parts.values()))) and 0 < score <= 100
    assert parts["role"] == 15 and parts["place"] == 15 and parts["fresh"] == 10


def test_a_better_fit_scores_higher(prof):
    good, profile = _t(prof)
    worse, _ = _t(prof, location="Remote", description="Python. Rust, Haskell, Erlang and Elixir required. 3+ years.",
                  posted="2026-09-01")
    assert match(good, profile, ["backend"])[0] > match(worse, profile, ["backend"])[0]


def test_rejected_and_unmatched_score_nothing(prof):
    profile = load_profile(prof.dir)
    assert match(Triaged({}, REJECT), profile, []) == (0, {})
    assert match(Triaged({}, NO_MATCH), profile, []) == (0, {})


def test_a_skipped_kind_of_work_earns_no_role_points(prof):
    t, profile = _t(prof, title="Data Engineer")
    assert match(t, profile, ["backend"], skipped={"data"})[1].get("role", 0) == 0


def test_more_years_than_had_and_a_junior_title_cost_level_points(prof):
    t, profile = _t(prof, description="Python and FastAPI. 6+ years of experience.")
    assert match(t, profile, [])[1]["level"] == 8
    t.verdict = FLAG
    assert match(t, profile, [])[1]["level"] == 4


def test_the_bar_for_the_model_can_be_set_per_profile():
    assert ai_min({}) == AI_MIN
    assert ai_min({"scoring": {"ai_min": 55}}) == 55
    assert ai_min({"scoring": {"ai_min": "x"}}) == AI_MIN


def test_the_model_reads_only_what_clears_the_bar_best_first():
    blob = {"postings": {
        "low": {"id": "low", "verdict": "keep", "match": 20, "score": 9},
        "high": {"id": "high", "verdict": "keep", "match": 80, "score": 1},
        "mid": {"id": "mid", "verdict": "keep", "match": 50, "score": 5},
        "old": {"id": "old", "verdict": "keep", "score": 3}}}          # scored before the match existed
    assert [p["id"] for p in rank.unread(blob, floor=40)] == ["high", "mid", "old"]
    assert [p["id"] for p in sorted(blob["postings"].values(), key=rank.sort_key)] == ["high", "mid", "low", "old"]


# ── through triage, from the pool ────────────────────────────────────────────

BODY = "Python, FastAPI and PostgreSQL. 3+ years of experience."


def _pool(prof, *posts):
    pool.update(prof.shared, [W.BoardWatch(provider="lever", slug="acme", company="Acme", status=W.SEEDED,
                                           current=[Posting(id=i, title=t, location="Pune, India", url=f"u{i}",
                                                            updated_at="2026-10-14", description=BODY)
                                                    for i, t in posts])])


def _results(prof):
    return json.loads((prof.var / "triage-results.json").read_text(encoding="utf-8"))["postings"]


def _triage(prof):
    return triage_cli.run_triage(prof, argparse.Namespace(all=False, board=None, quiet=True))


def test_triage_stores_the_role_and_the_score(tmp_path):
    prof = make_root(tmp_path)
    _pool(prof, ("1", "Backend Engineer"))
    assert pool.load_board(prof.shared, "lever:acme")["1"]["role"] == "backend"
    _triage(prof)
    rec = _results(prof)["1"]
    assert rec["role"] == "backend" and rec["match"] > 0 and set(rec["match_parts"]) >= {"skills", "role"}


def test_a_changed_profile_reads_everything_again(tmp_path):
    prof = make_root(tmp_path)
    _pool(prof, ("1", "Backend Engineer"), ("2", "Platform Engineer"))
    _triage(prof)
    first = _results(prof)
    assert set(first) == {"1", "2"}
    assert _triage(prof) == 0                                    # nothing new, nothing read
    # The person rules out platform titles: the stored verdict must follow.
    f = prof.file("search-profile.yaml")
    f.write_text(f.read_text(encoding="utf-8").replace("reject_function: [", "reject_function: [platform, ", 1),
                 encoding="utf-8")
    _triage(prof)
    after = _results(prof)
    assert after["1"]["verdict"] == KEEP
    assert after["2"]["verdict"] == REJECT and after["2"]["match"] == 0   # kept as a record, marked
    assert json.loads((prof.var / "triage-results.json").read_text(encoding="utf-8"))["reject_reasons"]


def test_the_ai_judgement_survives_a_reread(tmp_path):
    prof = make_root(tmp_path)
    _pool(prof, ("1", "Backend Engineer"))
    _triage(prof)
    path = prof.var / "triage-results.json"
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["postings"]["1"]["ai"] = {"fit": "strong", "why": "x"}
    path.write_text(json.dumps(blob), encoding="utf-8")
    (prof.var / triage_cli.FINGERPRINT).unlink()                 # as after this change is deployed
    _triage(prof)
    assert _results(prof)["1"]["ai"]["fit"] == "strong" and "match" in _results(prof)["1"]
