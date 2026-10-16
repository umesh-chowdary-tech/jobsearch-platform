"""The AI queue (ai_queue.py): pass 3 read until nothing is left, without
holding up the job update, a resume being built, or anyone's clicks; and the
model's own checks of place, level and skills (rank.py)."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

import jobsearch.rank as rank
from jobsearch import ai_queue as Q
from jobsearch.busy import busy
from jobsearch.llm import Answer
from jobsearch.web import actions, routes
from jobsearch.web.routes import HOSTED
from jobsearch.web.runner import Runner
from samples import make_root


@pytest.fixture
def prof(tmp_path):
    p = make_root(tmp_path)
    p.var.mkdir(parents=True, exist_ok=True)
    return p


def _results(prof, n=10, **extra):
    recs = {str(i): {"id": str(i), "title": "Backend Engineer", "company": "Acme", "board": "lever:acme",
                     "verdict": "keep", "match": 60, "score": 5, "location": "Pune", **extra} for i in range(n)}
    (prof.var / "triage-results.json").write_text(json.dumps({"postings": recs}), encoding="utf-8")


class Fake:
    """apply_to_results, scripted: one entry per turn."""

    def __init__(self, prof, turns):
        self.prof, self.turns, self.calls = prof, list(turns), 0

    def __call__(self, prof, budget=None, **kw):
        self.calls += 1
        ranked, stopped = self.turns.pop(0) if self.turns else (0, "")
        path = prof.var / "triage-results.json"
        blob = json.loads(path.read_text(encoding="utf-8"))
        todo = [p for p in blob["postings"].values() if "ai" not in p][:ranked]
        for p in todo:
            p["ai"] = {"fit": "possible", "why": "x", "by": "test", "prompt": rank.PROMPT_VERSION}
        path.write_text(json.dumps(blob), encoding="utf-8")
        left = sum(1 for p in blob["postings"].values() if "ai" not in p)
        return {"ranked": ranked, "considered": ranked + (left if stopped else 0) or left, "left": left,
                "stopped": stopped, "attribution": "test-model via test"}


@pytest.fixture
def keyed(monkeypatch):
    import jobsearch.llm
    monkeypatch.setattr(jobsearch.llm.LLM, "key_check", lambda self: (True, ""))


def _drain(prof, monkeypatch, turns, **kw):
    fake = Fake(prof, turns)
    monkeypatch.setattr(rank, "apply_to_results", fake)
    slept: list[float] = []
    res = Q.drain(prof.root, [prof], sleep=slept.append, **kw)
    return res, fake, slept


def test_it_reads_until_nothing_is_left(prof, monkeypatch, keyed):
    _results(prof, 10)
    res, fake, _ = _drain(prof, monkeypatch, [(4, "time budget"), (4, "time budget"), (2, "")])
    assert res["ranked"] == 10 and res["ended"] == "all read" and fake.calls == 3
    doc = Q.status(prof)
    assert doc["state"] == "done" and doc["done"] == 10 and Q.view(prof) is None
    assert Q.pending(prof) == 0


def test_a_short_rate_limit_is_waited_out(prof, monkeypatch, keyed):
    _results(prof, 6)
    res, fake, slept = _drain(prof, monkeypatch,
                              [(4, "Groq: tokens per minute (TPM) - try again in 7.5s"), (2, "")])
    assert res["ranked"] == 6 and slept and 8 <= slept[0] < 10


def test_a_long_limit_or_a_daily_cap_ends_the_run_and_says_until_when(prof, monkeypatch, keyed):
    _results(prof, 6)
    res, _, slept = _drain(prof, monkeypatch, [(4, "Groq: tokens per day (TPD) - try again in 2h3m")])
    assert res["ended"] == "1 waiting for a limit"
    doc = Q.status(prof)
    assert doc["state"] == "waiting" and doc["left"] == 2 and not slept
    until = datetime.fromisoformat(doc["until"])
    assert timedelta(hours=2) < until - datetime.now(timezone.utc) < timedelta(hours=2, minutes=5)
    assert Q.view(prof)["state"] == "waiting"

    _results(prof, 6)
    _drain(prof, monkeypatch, [(0, "Claude: daily spend limit reached ($0.30 of $0.30, data/llm-limits.yaml)")])
    doc = Q.status(prof)
    assert doc["state"] == "waiting" and datetime.fromisoformat(doc["until"]) > datetime.now(timezone.utc)


def test_ai_paused_on_the_admin_page_ends_the_run(prof, monkeypatch, keyed):
    _results(prof, 3)
    res, _, _ = _drain(prof, monkeypatch, [(0, "Claude: AI is paused on the admin page")])
    assert Q.status(prof)["state"] == "waiting" and Q.status(prof)["until"] is None


def test_a_model_that_never_answers_does_not_loop_forever(prof, monkeypatch, keyed):
    _results(prof, 3)
    res, fake, _ = _drain(prof, monkeypatch, [(0, "")] * 10)
    assert fake.calls == Q.IDLE_TURNS and Q.status(prof)["state"] == "waiting"


def test_no_key_says_so_and_sends_nothing(prof, monkeypatch):
    import jobsearch.llm
    monkeypatch.setattr(jobsearch.llm.LLM, "key_check", lambda self: (False, "no key set"))
    _results(prof, 3)
    res, fake, _ = _drain(prof, monkeypatch, [])
    assert fake.calls == 0 and "no key" in res["ended"] and "no key set" in Q.status(prof)["why"]


def test_stop_ends_it_between_requests(prof, monkeypatch, keyed):
    _results(prof, 8)
    res, fake, _ = _drain(prof, monkeypatch, [(4, "time budget")] * 3, should_stop=lambda: True)
    assert res["ended"] == "stopped" and fake.calls == 0


def test_it_steps_aside_while_someone_builds_a_resume(prof, monkeypatch, keyed):
    _results(prof, 4)
    asked = iter([True, True, False])
    res, fake, slept = _drain(prof, monkeypatch, [(4, "")], stepping_aside=lambda: next(asked, False))
    assert slept == [Q.STEP_ASIDE, Q.STEP_ASIDE] and res["ranked"] == 4


def test_only_one_queue_at_a_time(prof, monkeypatch, keyed):
    _results(prof, 2)
    Q._lock.acquire()
    try:
        res, fake, _ = _drain(prof, monkeypatch, [(2, "")])
    finally:
        Q._lock.release()
    assert res["ended"] == "already running" and fake.calls == 0


def test_nothing_waiting_clears_an_old_status(prof, monkeypatch, keyed):
    _results(prof, 0)
    Q._save(prof, state="waiting", why="limit", until=None)
    res, _, _ = _drain(prof, monkeypatch, [])
    assert res["ended"] == "nothing waiting" and Q.status(prof)["state"] == "done"


@pytest.mark.parametrize("why, secs", [("try again in 7.5s", 7.5), ("try again in 1m2s", 62.0),
                                       ("try again in 450ms", 0.45), ("try again in 2h3m", 7380.0),
                                       ("daily token limit reached", None), ("", None)])
def test_the_wait_a_limit_asks_for(why, secs):
    assert Q.wait_seconds(why) == secs


def test_a_running_status_from_a_dead_process_says_nothing(prof):
    Q._save(prof, state="running", done=1, total=5)
    assert Q.view(prof)["state"] == "running"
    doc = Q.status(prof)
    doc["updated"] = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec="seconds")
    (prof.var / Q.STATUS).write_text(json.dumps(doc), encoding="utf-8")
    assert Q.view(prof) is None


# ── the screens ──────────────────────────────────────────────────────────────

def test_the_overview_carries_the_queue(prof):
    _results(prof, 1)
    Q._save(prof, state="running", done=2, total=9, left=7)
    got = routes.get(prof, "/api/overview", {}, Runner(prof), scope=HOSTED)
    assert got["ai_queue"]["done"] == 2 and got["ai_queue"]["total"] == 9


def test_have_it_read_now_is_not_started_twice(prof, monkeypatch, capsys):
    Q._save(prof, state="running", done=2, total=9, left=7)
    monkeypatch.setattr(actions, "_cli", lambda: pytest.fail("ranked twice"))
    assert actions._rank(prof, {}) == 0
    assert "already reading" in capsys.readouterr().out


def test_a_resume_is_built_while_a_job_update_holds_the_lock(prof, monkeypatch):
    seen = {}
    monkeypatch.setitem(actions.ACTIONS, "assemble",
                        ("Build", lambda p, o: seen.setdefault("working", actions.someone_working()) and 0))
    run = actions.under_lock("assemble", actions.ACTIONS["assemble"][1])
    with busy(prof.shared, "scheduled cycle") as got:
        assert got
        assert run(prof, {"posting": "1"}) == 0                  # not refused
    assert seen["working"] is True and not actions.someone_working()


def test_other_actions_still_wait_for_the_lock(prof):
    run = actions.under_lock("triage", lambda p, o: pytest.fail("ran under someone else's lock"))
    with busy(prof.shared, "scheduled cycle"):
        assert run(prof, {}) == 3


# ── the cycle and the service ────────────────────────────────────────────────

def test_the_hosted_cycle_leaves_ranking_to_the_queue(prof, monkeypatch):
    import jobsearch.cycle as C
    calls = []
    monkeypatch.setattr(C, "_cycle", lambda root, profiles, quiet, inline=True: calls.append(inline) or 0)
    monkeypatch.setattr(Q, "drain", lambda *a, **k: pytest.fail("drained inside the hosted cycle"))
    assert C.run_cycle(prof.root, quiet=True, rank="none", profiles=[prof]) == 0
    assert calls == [False]


def test_the_pc_cycle_lets_go_of_its_lock_then_drains(prof, monkeypatch):
    import jobsearch.cycle as C
    from jobsearch.busy import holder
    seen = {}
    monkeypatch.setattr(C, "_cycle", lambda root, profiles, quiet, inline=True: 0)
    monkeypatch.setattr(Q, "drain", lambda root, profiles: seen.setdefault("holder", holder(prof.shared))
                        or {"ranked": 0, "people": 0, "ended": "nothing waiting"})
    assert C.run_cycle(prof.root, quiet=True, profiles=[prof]) == 0
    assert "holder" in seen and seen["holder"] is None
    assert "ai-queue" in next((prof.shared / "logs").glob("cycle-*.log")).read_text(encoding="utf-8")


def test_the_service_runs_one_queue_thread(prof, monkeypatch):
    from jobsearch.hosted.app import Service
    gate = threading.Event()
    monkeypatch.setattr(Q, "drain", lambda *a, **k: gate.wait(5) and {"ranked": 0})
    svc = Service(prof.root, verifier=None)
    assert svc.start_queue() is True
    assert svc.start_queue() is False                           # already reading
    gate.set()
    svc.queue.join(5)
    assert not svc.queue.is_alive()


# ── ranking: only its own fields, and the model's checks bind it ─────────────

def test_a_ranking_save_keeps_what_the_job_update_changed_meanwhile(prof, monkeypatch):
    _results(prof, 1)
    path = prof.var / "triage-results.json"

    def shortlist(prof_, top, llm=None, deadline=None, on_batch=None):
        # The job update closes and re-scores the posting while the model reads.
        blob = json.loads(path.read_text(encoding="utf-8"))
        blob["postings"]["0"].update({"closed_at": "2026-10-16T00:00:00+00:00", "match": 75})
        path.write_text(json.dumps(blob), encoding="utf-8")
        top[0]["ai"] = {"fit": "strong", "why": "y", "by": "t", "prompt": rank.PROMPT_VERSION}
        on_batch(top, {"0"})
        return top, Answer(ok=True, provider="t", model="m")

    monkeypatch.setattr(rank, "rank_shortlist", shortlist)
    blob = json.loads(path.read_text(encoding="utf-8"))
    rank._one_pass(prof, path, list(blob["postings"].values()), None, None)
    rec = json.loads(path.read_text(encoding="utf-8"))["postings"]["0"]
    assert rec["ai"]["fit"] == "strong" and rec["closed_at"] and rec["match"] == 75


class Model:
    def __init__(self, rankings):
        self.rankings = rankings
        self.asked = []

    def ask_json(self, system, user, max_tokens=0, cached_prefix=""):
        self.asked.append((system, cached_prefix, user))
        return Answer(ok=True, data={"rankings": self.rankings}, provider="test", model="m")


@pytest.mark.parametrize("checks, said, fit", [
    ({"place": "no", "level": "ok", "skills": "ok"}, "strong", "weak"),
    ({"place": "ok", "level": "no", "skills": "ok"}, "possible", "weak"),
    ({"place": "ok", "level": "ok", "skills": "no"}, "strong", "possible"),
    ({"place": "ok", "level": "ok", "skills": "ok"}, "strong", "strong"),
    ({"place": "maybe"}, "strong", "strong"),                      # anything else reads as unsure
])
def test_the_models_own_checks_bind_its_fit(prof, checks, said, fit):
    model = Model([{"id": "1", "fit": said, "why": "w", "checks": checks}])
    got, _ = rank.rank_shortlist(prof, [{"id": "1", "title": "Backend Engineer", "company": "Acme"}], llm=model)
    assert got[0]["ai"]["fit"] == fit and got[0]["ai"]["prompt"] == rank.PROMPT_VERSION
    assert set(got[0]["ai"]["checks"]) == {"place", "level", "skills"}
    system, prefix, user = model.asked[0]
    assert "CHECK FIRST" in system and "PLACE: " in prefix and '"checks"' in user


def test_the_place_line_is_the_filters_places():
    class P:
        def __init__(self, path):
            self.path = path

        def file(self, name):
            return self.path
    import tempfile
    from pathlib import Path
    d = Path(tempfile.mkdtemp())
    (d / "s.yaml").write_text("location:\n  preferred: [Pune]\n  acceptable: [India, Mumbai]\n  remote_terms: [remote]\n",
                              encoding="utf-8")
    line = rank.candidate_place(P(d / "s.yaml"))
    assert line.startswith("PLACE: Pune; also fine: India, Mumbai.") and "not remote from another country" in line
    (d / "s.yaml").write_text("location: {}\n", encoding="utf-8")
    assert rank.candidate_place(P(d / "s.yaml")) == "PLACE: anywhere."
