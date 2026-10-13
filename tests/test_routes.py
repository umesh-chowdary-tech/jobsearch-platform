"""The API both hosts share (web/routes.py): what each scope may ask, the
"good match?" answers, and the actions behind Run."""
from __future__ import annotations

import json

import pytest

from jobsearch import feedback
from jobsearch.web import actions, routes
from jobsearch.web.routes import HOSTED, LOCAL, ApiError
from jobsearch.web.runner import Runner
from samples import make_root


@pytest.fixture
def prof(tmp_path):
    p = make_root(tmp_path)
    p.var.mkdir(parents=True, exist_ok=True)
    posting = {"id": "1000001", "title": "Backend Engineer", "company": "Globex", "board": "greenhouse:globex",
               "verdict": "keep", "location": "Pune", "matched": {"Python": "core"}, "coverage": "1/2",
               "ai": {"fit": "strong", "why": "fits", "by": "Claude"}, "description": "text"}
    (p.var / "triage-results.json").write_text(json.dumps({"postings": {"1000001": posting}}))
    return p


def test_the_hosted_scope_answers_only_its_own_screens(prof):
    run = Runner(prof)
    assert routes.get(prof, "/api/today", {}, run, scope=HOSTED)["decide"]
    for path in ("/api/system", "/api/applications/x/sent"):
        with pytest.raises(ApiError) as e:
            routes.get(prof, path, {}, run, scope=HOSTED)
        assert e.value.status == 404, path
    for path in ("/api/stop", "/api/schedule/pause", "/api/escalations/resolve"):
        with pytest.raises(ApiError) as e:
            routes.post(prof, path, {}, run, scope=HOSTED)
        assert e.value.status == 404, path


def _with_resume(prof):
    from jobsearch.assemble import Variant
    lib = [Variant(id="backend", angle="backend", sent_to=[{"posting_id": "1000001", "date": "2026-10-12"}]).to_dict()]
    (prof.var / "resume-variants.json").write_text(json.dumps(lib))
    return prof


def test_a_referral_is_drafted_listed_and_marked_on_the_hosted_service(prof):
    run = Runner(_with_resume(prof))
    leads = routes.get(prof, "/api/postings/1000001/leads", {}, run, scope=HOSTED)
    assert leads["leads"] and all(x["url"].startswith("https://www.linkedin.com/search/") for x in leads["leads"])
    _, d = routes.post(prof, "/api/outreach/draft", {"posting": "1000001", "to": "Priya Shah", "how": "cold"},
                       run, scope=HOSTED)
    assert d["status"] == "drafted" and d["message"]
    assert routes.get(prof, "/api/outreach", {}, run, scope=HOSTED)[0]["need"] == "review and send"
    routes.post(prof, f"/api/outreach/{d['id']}/mark", {"status": "sent"}, run, scope=HOSTED)
    assert routes.get(prof, "/api/postings/1000001", {}, run, scope=HOSTED)["drafts"][0]["status"] == "sent"


@pytest.mark.parametrize("field, size", [("to", 81), ("context", 301), ("employer", 121), ("note", 301)])
def test_a_referral_ask_holds_a_line_not_a_document(prof, field, size):
    run = Runner(_with_resume(prof))
    body = {"posting": "1000001", "to": "Priya Shah", "how": "cold", field: "x" * size}
    path = "/api/outreach/x/mark" if field == "note" else "/api/outreach/draft"
    with pytest.raises(ApiError) as e:
        routes.post(prof, path, body, run, scope=HOSTED)
    assert e.value.status == 400 and "too long" in e.value.message


def test_the_hosted_scope_runs_only_resume_building_and_ranking(prof):
    run = Runner(prof)
    assert set(routes.get(prof, "/api/actions", {}, run, scope=HOSTED)) == {"assemble", "rank"}
    with pytest.raises(ApiError, match="unknown action 'cycle'"):
        routes.post(prof, "/api/run", {"action": "cycle"}, run, scope=HOSTED)
    assert "cycle" in routes.get(prof, "/api/actions", {}, run, scope=LOCAL)


def test_the_hosted_overview_has_no_pc_scheduler_and_no_one_elses_run(prof):
    from jobsearch.busy import busy
    run = Runner(prof)
    with busy(prof.shared, "app: assemble for someone else"):
        o = routes.get(prof, "/api/overview", {}, run, scope=HOSTED)
    assert set(o) == {"busy", "last_cycle", "now"} and o["busy"]["what"] == "updating"


def test_an_unexpected_error_on_the_hosted_service_says_nothing_about_the_server():
    status, body = routes.as_error(OSError(r"C:\secret\path\var\x.json"), scope=HOSTED)
    assert status == 500 and "secret" not in body["error"]
    assert "secret" in routes.as_error(OSError(r"C:\secret"), scope=LOCAL)[1]["error"]


def test_a_good_match_answer_is_kept_with_what_was_judged(prof):
    run = Runner(prof)
    status, e = routes.post(prof, "/api/postings/1000001/feedback", {"good": False, "note": "  wants   Go  "}, run,
                            scope=HOSTED)
    assert status == 200 and e["good"] is False and e["note"] == "wants Go"
    assert e["fit"] == "strong" and e["judged_by"] == "Claude" and e["title"] == "Backend Engineer"
    routes.post(prof, "/api/postings/1000001/feedback", {"good": True}, run)
    assert feedback.latest(prof)["1000001"]["good"] is True                       # the latest counts
    assert len(feedback.path(prof).read_text(encoding="utf-8").splitlines()) == 2   # every answer kept
    assert routes.get(prof, "/api/postings/1000001", {}, run)["feedback"]["good"] is True


@pytest.mark.parametrize("body", [{}, {"good": "yes"}, {"good": 1}])
def test_a_good_match_answer_must_be_yes_or_no(prof, body):
    with pytest.raises(ApiError) as e:
        routes.post(prof, "/api/postings/1000001/feedback", body, Runner(prof))
    assert e.value.status == 400


def test_no_answer_for_a_posting_that_is_not_there(prof):
    with pytest.raises(ApiError) as e:
        routes.post(prof, "/api/postings/nope/feedback", {"good": True}, Runner(prof))
    assert e.value.status == 404


# ── the actions behind Run ───────────────────────────────────────────────────

def test_watch_reads_the_shared_board_state(prof, capsys):
    """Run > Watch called cmd_watch with prof=prof.prof, an attribute Profile
    doesn't have; cmd_watch reads `root`."""
    assert actions._watch(prof, {}) == 1                       # no boards known yet - not a crash
    assert "no boards known" in capsys.readouterr().err


def test_the_apps_cycle_is_this_persons(prof, monkeypatch):
    seen = {}
    monkeypatch.setattr("jobsearch.cycle.run_cycle",
                        lambda root, quiet, lock, profiles: seen.update(root=root, lock=lock, profiles=profiles) or 0)
    assert actions._cycle(prof, {}) == 0
    assert seen == {"root": prof.root, "lock": False, "profiles": [prof]}


def test_search_needs_a_company(prof, capsys):
    assert actions._search(prof, {"companies": " , "}) == 2


def test_a_file_is_found_by_its_exact_name_in_the_folder_only(tmp_path):
    (tmp_path / "v1.pdf").write_bytes(b"%PDF")
    (tmp_path.parent / "secret.txt").write_text("no", encoding="utf-8")
    assert routes.pick_file(tmp_path, "v1.pdf") == tmp_path / "v1.pdf"
    for name in ("../secret.txt", "..", ".", "", "V1.PDF", "v1.pdf/"):
        assert routes.pick_file(tmp_path, name) is None, name
    assert routes.pick_file(tmp_path / "missing", "v1.pdf") is None
