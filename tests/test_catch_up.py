"""A profile made after the boards were first read reads what's open on them
from the pool (cycle.py, pool.py); and pass 1 runs on the title and location
before any description is fetched (triage.rejected_unread)."""
from __future__ import annotations

import argparse
import json

import pytest

import jobsearch.cycle as C
from jobsearch import triage_cli
from jobsearch import watch as W
from jobsearch.models import Posting
from jobsearch.skills import load_index
from jobsearch.triage import REJECT, load_profile, rejected_unread, triage_posting
from samples import make_root

REMOTE_BODY = "This role is fully remote. Python, PostgreSQL and FastAPI. 3+ years."


@pytest.fixture
def prof(tmp_path):
    return make_root(tmp_path)


def post(title="Backend Engineer", location="Pune, India", description=REMOTE_BODY, pid="1", board="lever:acme"):
    return {"id": pid, "title": title, "location": location, "url": f"https://jobs.example/{pid}",
            "description": description, "board": board, "company": "Acme", "first_seen": None}


@pytest.mark.parametrize("title, location", [
    ("Sales Manager", "Pune, India"),                 # not an engineering role
    ("Backend Engineer", "San Francisco, CA"),        # a place named: the body is never read for it
    ("Principal Backend Engineer", "Pune, India"),    # above the level, by title
])
def test_what_fails_on_title_or_place_is_rejected_without_the_description(prof, title, location):
    profile, index = load_profile(prof.dir), load_index(prof)
    early = rejected_unread(profile, post(title, location, description=""))
    full = triage_posting(index, profile, post(title, location))
    assert early is not None and early.verdict == full.verdict == REJECT
    assert early.reasons[-1] == full.reasons[-1]


@pytest.mark.parametrize("location", ["", "Hybrid", "Pune, India", "Remote"])
def test_what_the_description_could_decide_waits_for_it(prof, location):
    assert rejected_unread(load_profile(prof.dir), post(location=location, description="")) is None


def test_no_description_is_fetched_for_a_posting_already_rejected(prof, monkeypatch):
    import jobsearch.providers
    fetched = []
    monkeypatch.setattr(triage_cli, "default_fetcher", lambda var: object())
    monkeypatch.setattr(jobsearch.providers, "fetch_details",
                        lambda f, provider, slug, p: fetched.append(p["id"]) or {"description": REMOTE_BODY})
    queue = [post("Sales Manager", pid="s", description="", board="greenhouse:acme"),
             post("Backend Engineer", pid="b", description="", board="greenhouse:acme")]
    kept = triage_cli.run_triage(prof, argparse.Namespace(all=False, board=None, quiet=True, postings=queue))
    assert fetched == ["b"] and kept == 1


def _cycle(prof, monkeypatch, live):
    board = W.BoardWatch(provider="lever", slug="acme", company="Acme", status=W.POLLED,
                         current=[Posting(**{k: v for k, v in p.items() if k in ("id", "title", "location", "url", "description")})
                                  for p in live])
    import jobsearch.mailbox
    monkeypatch.setattr(jobsearch.mailbox, "configured", lambda prof: False)
    monkeypatch.setattr(C, "load_boards", lambda v: [{"provider": "lever", "slug": "acme"}])
    monkeypatch.setattr(C, "default_fetcher", lambda v: None)
    monkeypatch.setattr(C, "watch_all", lambda f, v, b: W.WatchRun(boards=[board]))
    monkeypatch.setattr(C, "unread", lambda blob, lead="", floor=0: [])
    def render(p):
        (p.var / "dashboard.html").write_text("x")
        return p.var / "dashboard.html"
    monkeypatch.setattr(C, "render", render)
    assert C.run_cycle(prof.root, quiet=True, profiles=[prof]) == 0
    return next((prof.shared / "logs").glob("cycle-*.log")).read_text(encoding="utf-8")


def _results(prof):
    return json.loads((prof.var / "triage-results.json").read_text(encoding="utf-8"))["postings"]


def test_a_new_profile_reads_whats_already_open_from_the_pool(prof, monkeypatch):
    assert not C.caught_up(prof)
    log = _cycle(prof, monkeypatch, [post(pid="old-1"), post("Sales Manager", pid="old-2")])
    assert "triage kept=1" in log and C.caught_up(prof)
    assert set(_results(prof)) == {"old-1"}
    # The pool keeps what the list gave, so nobody fetches it again.
    from jobsearch import pool
    assert pool.load_text(prof.shared, "lever:acme", "old-1")["description"] == REMOTE_BODY


def test_a_posting_is_read_once_and_a_later_one_still_arrives(prof, monkeypatch):
    _cycle(prof, monkeypatch, [post(pid="old-1")])
    before = _results(prof)["old-1"]["triaged_at"]
    _cycle(prof, monkeypatch, [post(pid="old-1"), post(pid="new-1")])
    results = _results(prof)
    assert set(results) == {"old-1", "new-1"}
    assert results["old-1"]["triaged_at"] == before          # not read again


def test_a_posting_that_leaves_the_board_is_closed_for_everyone(prof, monkeypatch):
    _cycle(prof, monkeypatch, [post(pid="old-1")])
    log = _cycle(prof, monkeypatch, [])
    assert "closed marked=1" in log
    assert _results(prof)["old-1"]["closed_at"]
