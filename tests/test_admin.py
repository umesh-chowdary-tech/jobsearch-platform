"""The admin page: its numbers, its switches, and that only admins reach them."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")

from jobsearch.hosted import accounts  # noqa: E402
from jobsearch.hosted.admin import LOG  # noqa: E402
from jobsearch.limits import Meter, load_limits  # noqa: E402
from jobsearch.llm import LLM, load_routes  # noqa: E402
from jobsearch.profile import shared_var  # noqa: E402
from samples import ROOT  # noqa: E402
from test_hosted import BOB, auth, joined, make_env, token  # noqa: E402

BOSS = auth(token("33333333-3333-4333-8333-333333333333", "boss@example.com"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    return make_env(tmp_path, monkeypatch)


def ready(prof):
    """A person who has finished setup: the sample profile, as theirs."""
    import shutil
    shutil.copytree(ROOT / "profiles" / "sample-backend", prof.dir)
    prof.var.mkdir(parents=True, exist_ok=True)
    return prof


def do(client, **body):
    return client.post("/api/admin/do", json=body, headers=BOSS)


def test_an_admin_joins_without_a_code_and_nobody_else_does(env):
    """Invites are made on the admin page, so a new service's first admin
    would otherwise have no way in."""
    from jobsearch.hosted.accounts import CONSENT_VERSION
    client, root = env
    yes = {"code": "", "consent": CONSENT_VERSION}
    assert client.post("/api/invite", json=yes, headers=auth(token())).status_code == 400
    r = client.post("/api/invite", json=yes, headers=BOSS)
    assert r.status_code == 200 and r.json()["member"] is True
    assert client.post("/api/invite", json=yes, headers=BOSS).status_code == 200    # already in
    assert accounts.load(shared_var(root))["invites"] == {}


def test_only_admins_reach_the_switches(env):
    client, root = env
    alice = joined(client, root)
    r = client.post("/api/admin/do", json={"action": "suspend", "profile": alice.id}, headers=auth(token()))
    assert r.status_code == 403
    assert accounts.load(shared_var(root))["users"][next(iter(accounts.load(shared_var(root))["users"]))].get(
        "suspended") is None


def test_the_overview_counts_people_and_how_far_they_got(env):
    client, root = env
    alice = ready(joined(client, root))
    joined(client, root, BOB, "bob@example.com")
    (alice.var / "triage-results.json").write_text(json.dumps({"postings": {
        "1": {"id": "1", "ai": {"fit": "strong"}}, "2": {"id": "2", "ai": {"fit": "possible"}}, "3": {"id": "3"}}}))
    (alice.var / "pipeline.json").write_text(json.dumps({"1": {"status": "applied"}, "2": {"status": "skipped"}}))
    Meter(root, alice.id).record("claude", 1000, "rank", usd=0.02, model="claude-haiku-5-5")
    Meter(root, alice.id).record("claude", 3000, "resume", usd=0.05, model="claude-sonnet-5-5")
    o = client.get("/api/admin/overview", headers=BOSS).json()

    assert [p["email"] for p in o["people"]] == ["alice@example.com", "bob@example.com"]
    a = o["people"][0]
    assert a["profile_ready"] and a["counts"] == {"ranked": 2, "strong": 1, "possible": 1, "resumes": 0,
                                                "applied": 1, "answers": 0, "good": 0}
    assert o["people"][1]["counts"] is None                 # Bob hasn't set up yet
    assert [f["n"] for f in o["funnel"]] == [2, 1, 1, 0, 1]
    assert o["active"] == {"week": 2, "month": 2}             # joining counts as using it today
    assert sum(w["n"] for w in o["joins"]) == 2
    ai = o["ai"]
    assert ai["month_usd"] == 0.07 and ai["days"][-1]["usd"] == 0.07 and len(ai["days"]) == 30
    assert {r["job"]: r["usd"] for r in ai["by_job"]} == {"resume": 0.05, "rank": 0.02}
    assert {r["model"]: r["requests"] for r in ai["by_model"]} == {"claude-sonnet-5-5": 1, "claude-haiku-5-5": 1}
    assert ai["budget_usd"] == load_limits(root)["claude"]["usd_per_month"]
    assert o["health"]["keys"].keys() == {"claude", "groq"} and o["health"]["database"] is None
    assert [r["job"] for r in o["settings"]["routes"]] == ["rank", "resume", "setup"]


def test_the_overview_shows_counts_never_what_a_person_holds(env):
    client, root = env
    alice = ready(joined(client, root))
    (alice.var / "triage-results.json").write_text(json.dumps({"postings": {
        "1": {"id": "1", "title": "Secret Title", "company": "Secret Co", "ai": {"fit": "strong", "why": "x"}}}}))
    text = client.get("/api/admin/overview", headers=BOSS).text
    assert "Secret Title" not in text and "Secret Co" not in text


def test_a_paused_account_can_take_its_data_but_nothing_else(env):
    client, root = env
    alice = ready(joined(client, root))
    a = auth(token())
    assert do(client, action="suspend", profile=alice.id).status_code == 200
    r = client.get("/api/today", headers=a)
    assert r.status_code == 403 and r.json()["suspended"] is True
    assert client.get("/api/me", headers=a).json()["suspended"] is True
    assert client.get("/api/account/export", headers=a).status_code == 200
    assert do(client, action="unsuspend", profile=alice.id).status_code == 200
    assert client.get("/api/today", headers=a).status_code == 200


def test_an_admin_deletes_an_account_only_when_told_twice(env):
    client, root = env
    alice = ready(joined(client, root))
    assert do(client, action="delete", profile=alice.id).status_code == 400
    assert alice.var.exists()
    r = do(client, action="delete", profile=alice.id, confirm="delete")
    assert r.status_code == 200 and r.json()["overview"]["people"] == []
    assert not alice.var.exists()
    assert do(client, action="delete", profile=alice.id, confirm="delete").status_code == 400   # gone


def test_ai_can_be_paused_for_everyone(env):
    client, root = env
    alice = joined(client, root)
    assert do(client, action="ai_pause").json()["overview"]["settings"]["ai_paused"] is True
    assert Meter(root, alice.id).check("claude", 10) == "AI is paused on the admin page"
    do(client, action="ai_resume")
    assert Meter(root, alice.id).check("claude", 10) == ""


def test_caps_change_within_a_ceiling(env):
    client, root = env
    joined(client, root)
    o = do(client, action="caps", usd_per_month=8, usd_per_day=1.25, person_usd_per_day=0.5).json()["overview"]
    assert o["settings"]["caps"] == {"usd_per_month": 8.0, "usd_per_day": 1.25, "person_usd_per_day": 0.5,
                                     "person_usd_per_month": 10.0}
    claude = load_limits(root)["claude"]
    assert claude["usd_per_month"] == 8.0 and claude["per_profile"]["usd_per_day"] == 0.5
    assert claude["per_profile"]["requests_per_day"] == 40                 # the file's other caps stand
    for bad in (-1, 10_000, "5", True, None):
        assert do(client, action="caps", usd_per_month=bad).status_code == 400
    assert do(client, action="caps").status_code == 400                    # nothing to change


def test_one_person_can_be_given_more_room(env):
    client, root = env
    alice = joined(client, root)
    m = Meter(root, alice.id)
    m.record("claude", 1000, "resume", usd=0.29)
    assert "daily spend limit" in m.check("claude", 100, "resume", usd=0.05)
    assert do(client, action="person_limit", profile=alice.id, usd_per_day=1).status_code == 200
    assert m.check("claude", 100, "resume", usd=0.05) == ""
    assert do(client, action="person_limit", profile=alice.id, usd_per_day=None).status_code == 200
    assert "daily spend limit" in m.check("claude", 100, "resume", usd=0.05)


def test_a_job_can_be_given_another_model_but_only_an_allowed_one(env):
    client, root = env
    alice = joined(client, root)
    assert do(client, action="route", job="resume", choice="claude:claude-haiku-5-5").status_code == 200
    assert load_routes(root)["resume"] == {"claude": "claude-haiku-5-5"}
    assert [(p.name, p.model) for p in LLM(alice, purpose="resume").chain] == [("claude", "claude-haiku-5-5")]
    # An uploaded resume's text goes to Anthropic only, as the privacy notice says.
    assert do(client, action="route", job="setup", choice="groq").status_code == 400
    assert do(client, action="route", job="rank", choice="claude:claude-unpriced").status_code == 400
    assert do(client, action="route", job="nope", choice="groq").status_code == 400
    o = do(client, action="route", job="rank", choice="groq").json()["overview"]
    assert {r["job"]: r["current"] for r in o["settings"]["routes"]}["rank"] == "groq"


def test_groq_for_everything_is_one_switch_that_turns_itself_off(env):
    client, root = env
    alice = joined(client, root)
    fm = do(client, action="free_mode", on=True, hours=1).json()["overview"]["settings"]["free_mode"]
    assert fm["on"] and fm["until"] and not fm["dev"]
    routes = load_routes(root)
    assert routes["rank"] == routes["resume"] == routes["outreach"] == {"groq": ""}
    assert [p.name for p in LLM(alice, purpose="resume").chain] == ["groq"]
    # On the live service resumes still go to Anthropic only, as the privacy notice says.
    assert routes["setup"] == {"claude": "claude-opus-5-5"}
    assert do(client, action="free_mode", on=True, hours=5).status_code == 400
    # Its time up, every job is back on its own model, without anyone turning it off.
    p = shared_var(root) / "free-mode.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    p.write_text(json.dumps({**doc, "until": "2000-01-01T00:00:00+00:00"}), encoding="utf-8")
    assert load_routes(root)["resume"] == {"claude": "claude-sonnet-5-5"}
    do(client, action="free_mode", on=True, hours=None)
    assert load_routes(root)["rank"] == {"groq": ""}
    o = do(client, action="free_mode", on=False).json()["overview"]
    assert not o["settings"]["free_mode"]["on"] and load_routes(root)["rank"] == {"claude": "claude-haiku-5-5"}
    assert [r["detail"] for r in o["log"] if r["action"] == "free_mode"][:2] == ["off", "on until turned off"]


def test_on_a_dev_server_the_switch_reads_resumes_with_groq_too():
    from jobsearch.hosted.admin import free_jobs
    assert "setup" in free_jobs(dev=True) and "setup" not in free_jobs(dev=False)


def test_invites_unused_can_be_cancelled_and_used_ones_name_who(env):
    client, root = env
    joined(client, root)
    spare = accounts.create_invites(shared_var(root), 1)[0]
    o = client.get("/api/admin/overview", headers=BOSS).json()
    assert [i["email"] for i in o["invites_used_by"]] == ["alice@example.com"]
    used = o["invites_used_by"][0]["code"]
    assert do(client, action="revoke_invite", code=used).status_code == 400
    assert do(client, action="revoke_invite", code=spare).status_code == 200
    assert spare not in accounts.load(shared_var(root))["invites"]


def test_job_updates_can_be_paused_and_run_now(env, monkeypatch):
    client, root = env
    joined(client, root)
    ran = []
    monkeypatch.setattr("jobsearch.cycle.run_cycle", lambda root, quiet, lock: ran.append(lock))
    o = do(client, action="cycle_pause").json()["overview"]
    assert o["health"]["cycle_paused"] is not None
    assert do(client, action="cycle_run").json()["started"] is True        # someone asked: it runs anyway
    client.app.state.service.cycle.join(5)
    assert ran == [False]
    assert do(client, action="cycle_resume").json()["overview"]["health"]["cycle_paused"] is None


def test_a_new_copy_checks_the_registrys_boards_once(env, monkeypatch):
    """A new copy of the service knows no boards: "Check job boards" probes the
    registry's and keeps the ones that answer for the job updates to watch."""
    from jobsearch import discovery
    from jobsearch.discovery import DiscoveryRun
    from jobsearch.models import OK, BoardProbe
    client, root = env
    seen = []

    def fake_verify(fetcher, known):
        known = list(known)
        seen.extend(known)
        provider, slug, company = known[0]
        return DiscoveryRun(probes=[BoardProbe(provider=provider, slug=slug, company=company, status=OK,
                                               board_name="Sample Board", url="https://example.com")])
    monkeypatch.setattr(discovery, "verify_known", fake_verify)
    monkeypatch.setattr("jobsearch.http.default_fetcher", lambda var: None)
    assert client.get("/api/admin/overview", headers=BOSS).json()["jobs"]["boards"]["total"] == 0
    assert do(client, action="boards_check").json()["started"] is True
    client.app.state.service.cycle.join(5)
    assert len(seen) > 1                                          # every board in data/ats-registry.yaml
    assert client.get("/api/admin/overview", headers=BOSS).json()["jobs"]["boards"]["total"] == 1


def test_every_change_is_written_down(env):
    client, root = env
    alice = joined(client, root)
    do(client, action="suspend", profile=alice.id)
    do(client, action="ai_pause")
    log = [json.loads(line) for line in (shared_var(root) / LOG).read_text().splitlines()]
    assert [(e["action"], e["by"]) for e in log] == [("suspend", "boss@example.com"), ("ai_pause", "boss@example.com")]
    o = client.get("/api/admin/overview", headers=BOSS).json()
    assert [e["action"] for e in o["log"]] == ["ai_pause", "suspend"]


def test_job_update_runs_are_read_from_the_log(env):
    client, root = env
    logs = shared_var(root) / "logs"
    logs.mkdir(parents=True)
    from datetime import date
    today = date.today().isoformat()
    (logs / f"cycle-{today[:4]}{today[5:7]}.log").write_text(
        f"{today}T01:00:00+00:00  watch boards=3 new=4 closed=0 skipped=0 | rank ok | took 42s\n"
        f"{today}T01:15:00+00:00  cycle SKIPPED busy: x since y\n"
        f"{today}T01:30:00+00:00  watch FAILED ConnectionError: x | took 3s\n"
        f"{today}T01:30:00+00:00  watch traceback\n", encoding="utf-8")
    jobs = client.get("/api/admin/overview", headers=BOSS).json()["jobs"]
    assert jobs["runs"] == 2 and jobs["failed"] == 1 and jobs["avg_took"] == 22
    assert jobs["days"][-1] == {"day": today, "runs": 2, "failed": 1, "new": 4}
