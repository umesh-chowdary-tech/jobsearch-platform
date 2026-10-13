"""What the Jobs, Profile and Resumes screens are given (web/state.py), on the
hosted scope as well as the local one."""
from __future__ import annotations

import json

import pytest

from jobsearch.assemble import Variant, load_store
from jobsearch.triage import CORE, KEEP, Coverage, Triaged, write_results
from jobsearch.web import routes, state
from jobsearch.web.routes import HOSTED
from jobsearch.web.runner import Runner
from samples import make_root

SEEN = "2026-10-12T05:00:00+00:00"


@pytest.fixture
def prof(tmp_path):
    p = make_root(tmp_path)
    posting = {"id": "1000001", "title": "Backend Engineer", "company": "globexindiaprivatelimited",
               "board": "greenhouse:globex", "verdict": "keep", "location": "Pune", "first_seen": SEEN,
               "matched": {"Python": "core"}, "coverage": "1/2", "description": "text"}
    (p.var / "triage-results.json").write_text(json.dumps({"postings": {"1000001": posting}}))
    (p.shared / "boards.json").write_text(json.dumps(
        {"greenhouse:globex": {"slug": "globex", "company": "Globex"}}))
    lib = [Variant(id="backend", angle="backend", title_descriptor="Backend Engineer",
                   sent_to=[{"posting_id": "1000001", "company": "globexindiaprivatelimited",
                             "role": "Backend Engineer", "date": "2026-10-12"},
                            {"posting_id": "gone", "company": "Initech", "role": "SRE", "date": "2026-10-01"}]
                   ).to_dict()]
    (p.var / "resume-variants.json").write_text(json.dumps(lib))
    return p


def test_a_job_keeps_the_day_it_was_first_seen_through_a_recheck(tmp_path):
    var = tmp_path / "var"
    cov = Coverage(named={"Python"}, matched={"Python": CORE})
    post = {"id": "1", "title": "Backend Engineer", "company": "acme", "board": "lever:acme",
            "location": "Pune", "description": "Python", "first_seen": SEEN}
    write_results(var, [Triaged(post, KEEP, ["ok"], cov)])
    # A recheck reads the board again: no stamp of its own.
    write_results(var, [Triaged({**post, "first_seen": None}, KEEP, ["ok"], cov)])
    rec = json.loads((var / "triage-results.json").read_text(encoding="utf-8"))["postings"]["1"]
    assert rec["first_seen"] == SEEN


def test_a_job_row_says_when_it_was_first_seen(prof):
    assert state.postings(prof)[0]["first_seen"] == SEEN


def test_skill_gaps_come_as_rows_for_the_page_to_draw(prof):
    (prof.var / "skill-gaps.json").write_text(json.dumps({
        "absent": {"Kubernetes": {"required_count": 4, "nice_to_have_count": 1}},
        "shallow": {"Go": {"required_count": 2, "nice_to_have_count": 0}}}))
    m = state.market(prof)
    assert m["gaps"] == [{"skill": "Kubernetes", "bucket": "absent", "required": 4, "nice": 1},
                         {"skill": "Go", "bucket": "shallow", "required": 2, "nice": 0}]
    assert "Kubernetes" in m["chart_html"]          # the local app's chart is unchanged


def test_the_profile_shows_what_was_confirmed_and_what_is_sought(prof):
    p = state.profile(prof)
    assert p["identity"]["name"] and p["employment"][0]["employer"]
    assert p["languages"] and p["entries"] and p["tiers"]
    assert {"sources", "evidence", "notes", "status"} <= set(p["entries"][0])
    assert p["prefs"]["preferred"] and p["prefs"]["years"] is not None
    assert isinstance(p["prefs"]["watch"], list) and isinstance(p["prefs"]["remote"], bool)


def test_the_profile_never_carries_contact_details(prof):
    """The connector hands `identity` to a chat; the page doesn't need them."""
    text = json.dumps(state.profile(prof))
    idn = load_store(prof)["identity"]
    for key in ("email", "phone"):
        assert idn[key] and idn[key] not in text and f'"{key}"' not in text


def test_a_profile_without_search_settings_still_shows(prof):
    (prof.dir / "search-profile.yaml").unlink()
    assert state.profile(prof)["prefs"] is None


def test_resumes_name_the_company_and_say_which_jobs_still_open(prof):
    used = state.resumes(prof)["variants"][0]["sent_to"]
    assert used[0]["company"] == "Globex" and used[0]["known"] is True
    assert used[1]["company"] == "Initech" and used[1]["known"] is False


@pytest.mark.parametrize("path", ["/api/postings", "/api/profile", "/api/resumes", "/api/market"])
def test_the_new_screens_are_served_to_a_signed_in_person(prof, path):
    assert routes.get(prof, path, {}, Runner(prof), scope=HOSTED) is not None
