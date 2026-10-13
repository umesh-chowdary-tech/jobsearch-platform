"""A profile made after the boards were first read catches up on what's
open on them, once (cycle.py); and pass 1 runs on the title and location
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
    fetched = []
    monkeypatch.setattr(triage_cli, "default_fetcher", lambda var: None)
    monkeypatch.setattr(triage_cli, "fetch_details",
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
    monkeypatch.setattr(C, "sync_closed", lambda v, live: ([], []))
    monkeypatch.setattr(C, "unread", lambda blob, lead="": [])
    def render(p):
        (p.var / "dashboard.html").write_text("x")
        return p.var / "dashboard.html"
    monkeypatch.setattr(C, "render", render)
    assert C.run_cycle(prof.root, quiet=True, profiles=[prof]) == 0
    return next((prof.shared / "logs").glob("cycle-*.log")).read_text(encoding="utf-8")


def test_a_new_profile_reads_whats_already_open_once(prof, monkeypatch):
    assert not C.caught_up(prof)
    log = _cycle(prof, monkeypatch, [post(pid="old-1"), post("Sales Manager", pid="old-2")])
    assert "catch-up read=2 kept=1" in log and C.caught_up(prof)
    results = json.loads((prof.var / "triage-results.json").read_text(encoding="utf-8"))["postings"]
    assert set(results) == {"old-1"} and results["old-1"]["first_seen"] is None
    # Once: the next cycle reads only what's new, through the queue.
    _cycle(prof, monkeypatch, [post(pid="old-3")])
    results = json.loads((prof.var / "triage-results.json").read_text(encoding="utf-8"))["postings"]
    assert "old-3" not in results


def test_a_board_that_failed_this_run_adds_nothing_to_catch_up_on():
    w = W.WatchRun(boards=[W.BoardWatch(provider="lever", slug="x", company="X", status=W.SKIPPED,
                                        current=[Posting(id="1", title="t", location="", url="")])])
    assert w.live() == []
