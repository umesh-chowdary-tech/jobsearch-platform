"""The shared job pool (pool.py): every posting once for everyone, its
description fetched once, the 60-day window, closed postings, and the
descriptions dropped 90 days after a posting closes."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from jobsearch import pool, statestore
from jobsearch import watch as W
from jobsearch.http import Result
from jobsearch.models import Posting
from jobsearch.triage import sync_pool
from jobsearch.web import actions, routes, state
from jobsearch.web.routes import HOSTED
from jobsearch.web.runner import Runner
from samples import make_root

TODAY = date(2026, 10, 16)          # conftest fixes pool._today to this
REAL_ASK = pool._ask_board          # conftest stands in for it in every test


@pytest.fixture
def shared(tmp_path):
    d = tmp_path / "var" / "_shared"
    d.mkdir(parents=True)
    return d


def board(status=W.POLLED, posts=(), added=(), provider="lever", slug="acme"):
    return W.BoardWatch(provider=provider, slug=slug, company="Acme", status=status,
                        current=list(posts), added=list(added))


def P(pid, title="Backend Engineer", posted=None, description=""):
    return Posting(id=pid, title=title, location="Pune, India", url=f"https://jobs.example/{pid}",
                   updated_at=posted, description=description)


# ── dates and the window ─────────────────────────────────────────────────────

@pytest.mark.parametrize("value, day", [
    ("2026-10-02T11:31:50-04:00", "2026-10-02"),          # Greenhouse first_published
    ("1727740800000", "2024-10-01"),                       # Lever: milliseconds
    (1727740800000, "2024-10-01"),
    ("2026-09-24", "2026-09-24"),                          # Workday, Oracle, Amazon
    ("", None), (None, None), ("Posted 30+ Days Ago", None), ("2026-13-40", None),
])
def test_a_boards_date_in_any_shape(value, day):
    assert pool.posted_day(value) == day


def test_the_window_counts_from_the_posting_date_then_first_seen():
    assert pool.within_window({"posted_at": "2026-08-17"})                 # 60 days: in
    assert not pool.within_window({"posted_at": "2026-08-16"})             # 61: out
    assert not pool.within_window({"first_seen": "2026-07-01T00:00:00+00:00"})
    # The board's date wins over a later first sight.
    assert not pool.within_window({"posted_at": "2026-06-01", "first_seen": "2026-10-15T00:00:00+00:00"})
    assert pool.within_window({})                                           # undated: nothing says old


# ── after a poll ─────────────────────────────────────────────────────────────

def test_a_new_board_is_recorded_whole_and_its_descriptions_kept(shared):
    got = pool.update(shared, [board(W.SEEDED, [P("1", posted="2026-10-10", description="Python."),
                                                P("2", posted="2026-01-01", description="Old.")])])
    recs = pool.load_board(shared, "lever:acme")
    assert set(recs) == {"1", "2"} and got["added"] == 2
    assert recs["1"]["posted_at"] == "2026-10-10" and recs["1"]["company"] == "Acme"
    assert pool.load_text(shared, "lever:acme", "1")["description"] == "Python."
    assert pool.load_text(shared, "lever:acme", "2") is None               # outside the window: not kept


def test_a_board_watched_before_the_pool_existed_keeps_its_old_dates(shared):
    W.save_seen(W.seen_path(shared, "lever", "acme"), ["1", "2"], {"seeded_at": "2026-09-20T00:00:00+00:00"})
    (shared / "new-postings.jsonl").write_text(json.dumps(
        {"board": "lever:acme", "id": "2", "first_seen": "2026-10-01T00:00:00+00:00"}) + "\n", encoding="utf-8")
    pool.update(shared, [board(W.POLLED, [P("1"), P("2"), P("3")], added=[P("3")])], now="2026-10-16T00:00:00+00:00")
    recs = pool.load_board(shared, "lever:acme")
    assert recs["1"]["first_seen"] == "2026-09-20T00:00:00+00:00"          # the board's first read
    assert recs["2"]["first_seen"] == "2026-10-01T00:00:00+00:00"          # the queue's date
    assert recs["3"]["first_seen"] == "2026-10-16T00:00:00+00:00"          # new this poll


def test_a_new_posting_on_a_board_that_was_empty_is_new(shared):
    pool.update(shared, [board(W.SEEDED, [])])
    assert pool.board_path(shared, "lever:acme").exists()                 # met, though empty
    pool.update(shared, [board(W.POLLED, [P("9")], added=[P("9")])], now="2026-10-16T00:00:00+00:00")
    assert pool.load_board(shared, "lever:acme")["9"]["first_seen"] == "2026-10-16T00:00:00+00:00"


def test_gone_closes_back_reopens_and_a_failed_poll_changes_nothing(shared):
    pool.update(shared, [board(W.SEEDED, [P("1"), P("2")])])
    got = pool.update(shared, [board(W.POLLED, [P("1")])])
    assert got["closed"] == 1 and pool.is_open(shared, "lever:acme", "2") is False
    pool.update(shared, [board(W.SKIPPED, [])])                            # a timeout closes nothing
    assert pool.is_open(shared, "lever:acme", "1") is True
    got = pool.update(shared, [board(W.POLLED, [P("1"), P("2")])])
    assert got["reopened"] == 1 and pool.is_open(shared, "lever:acme", "2") is True
    assert pool.is_open(shared, "lever:acme", "nope") is None


def test_a_quiet_poll_writes_nothing(shared):
    pool.update(shared, [board(W.SEEDED, [P("1")])])
    path = pool.board_path(shared, "lever:acme")
    before = path.stat().st_mtime_ns
    path_text = path.read_text(encoding="utf-8")
    pool.update(shared, [board(W.POLLED, [P("1")])])
    assert path.read_text(encoding="utf-8") == path_text and path.stat().st_mtime_ns == before


def test_a_board_no_longer_watched_is_closed(shared):
    pool.update(shared, [board(W.SEEDED, [P("1")]), board(W.SEEDED, [P("7")], slug="globex")])
    assert pool.retire(shared, {"lever:acme"}) == 1
    assert pool.is_open(shared, "lever:globex", "7") is False and pool.is_open(shared, "lever:acme", "1")


def test_triage_reads_open_postings_in_the_window(shared):
    pool.update(shared, [board(W.SEEDED, [P("1", posted="2026-10-01"), P("2", posted="2026-01-01"), P("3")])])
    pool.update(shared, [board(W.POLLED, [P("1", posted="2026-10-01"), P("2", posted="2026-01-01")])])
    assert [p["id"] for p in pool.open_postings(shared)] == ["1"]          # 2 too old, 3 closed


def test_the_queue_no_longer_carries_descriptions(shared):
    run = W.WatchRun(boards=[board(W.POLLED, [P("1", description="long text")], added=[P("1", description="long text")])])
    W.record_new(shared, run)
    line = json.loads((shared / "new-postings.jsonl").read_text(encoding="utf-8"))
    assert line["id"] == "1" and "description" not in line
    assert pool.load_text(shared, "lever:acme", "1")["description"] == "long text"


# ── descriptions, fetched once ───────────────────────────────────────────────

def test_a_description_is_fetched_once_for_everyone(shared, monkeypatch):
    import jobsearch.providers as providers
    pool.update(shared, [board(W.SEEDED, [P("5")], provider="greenhouse")])
    calls = []
    monkeypatch.setattr(providers, "fetch_details", lambda f, provider, slug, p: calls.append(p["id"])
                        or {"description": "Go and Kubernetes.", "updated_at": "2026-10-05"})
    rec = pool.load_board(shared, "greenhouse:acme")["5"]
    assert pool.details(shared, object(), rec)["description"] == "Go and Kubernetes."
    assert pool.details(shared, object(), rec)["description"] == "Go and Kubernetes."
    assert calls == ["5"]
    assert pool.load_board(shared, "greenhouse:acme")["5"]["posted_at"] == "2026-10-05"


def test_a_failed_fetch_is_not_kept(shared, monkeypatch):
    import jobsearch.providers as providers
    monkeypatch.setattr(providers, "fetch_details", lambda f, provider, slug, p: {})
    assert pool.details(shared, object(), {"board": "greenhouse:acme", "id": "5"}) == {}
    assert pool.load_text(shared, "greenhouse:acme", "5") is None


def test_an_id_that_is_no_file_name_gets_one(shared):
    pool.save_text(shared, "workday:x.wd1.Site", "../../etc/passwd", {"description": "d"})
    path = pool.text_path(shared, "workday:x.wd1.Site", "../../etc/passwd")
    assert path.parent.name == "workday__x.wd1.Site" and ".." not in path.name and path.exists()
    assert pool.text_path(shared, "b:x", "a/b") != pool.text_path(shared, "b:x", "a_b")


# ── 90 days after closing ────────────────────────────────────────────────────

def test_descriptions_go_90_days_after_closing_records_stay(shared):
    pool.update(shared, [board(W.SEEDED, [P("1", description="a"), P("2", description="b")])])
    pool.update(shared, [board(W.POLLED, [])], now="2026-07-01T00:00:00+00:00")
    recs = pool.load_board(shared, "lever:acme")
    recs["2"]["closed_at"] = "2026-10-01T00:00:00+00:00"
    pool._save_board(shared, "lever:acme", recs)
    assert pool.shrink(shared, now=datetime(2026, 10, 16, tzinfo=timezone.utc)) == 1
    assert pool.load_text(shared, "lever:acme", "1") is None
    assert pool.load_text(shared, "lever:acme", "2")["description"] == "b"
    assert set(pool.load_board(shared, "lever:acme")) == {"1", "2"}


def test_a_persons_results_take_dates_and_lose_old_excerpts(tmp_path):
    var = tmp_path / "p"
    var.mkdir()
    (var / "triage-results.json").write_text(json.dumps({"postings": {
        "1": {"board": "lever:acme", "description": "d", "requirements": "r", "closed_at": "2026-06-01T00:00:00+00:00",
              "ai": {"fit": "strong"}},
        "2": {"board": "lever:acme", "description": "d", "closed_at": "2026-10-01T00:00:00+00:00"},
        "3": {"board": "lever:acme", "description": "d"}}}), encoding="utf-8")
    done = sync_pool(var, lambda b, pid: "2026-10-03" if pid == "3" else None,
                     now=datetime(2026, 10, 16, tzinfo=timezone.utc))
    recs = json.loads((var / "triage-results.json").read_text(encoding="utf-8"))["postings"]
    assert done == {"dated": 1, "shrunk": 1}
    assert "description" not in recs["1"] and "requirements" not in recs["1"] and recs["1"]["ai"]["fit"] == "strong"
    assert recs["2"]["description"] == "d" and recs["3"]["posted_at"] == "2026-10-03"


# ── the database follows a removed description ───────────────────────────────

class Remote:
    def __init__(self):
        self.rows: dict[str, tuple[bytes, str]] = {}

    def lock(self):
        import contextlib
        return contextlib.nullcontext()

    def hashes(self):
        return {p: h for p, (_, h) in self.rows.items()}

    def get(self, paths):
        return {p: self.rows[p][0] for p in paths if p in self.rows}

    def put(self, rows):
        for p, data, h in rows:
            self.rows[p] = (data, h)

    def delete(self, paths):
        for p in paths:
            self.rows.pop(p, None)


def test_a_shrunk_description_leaves_the_database_too(tmp_path):
    var = tmp_path / "var"
    shared = var / "_shared"
    shared.mkdir(parents=True)
    pool.save_text(shared, "lever:acme", "1", {"description": "x"})
    (shared / "boards.json").write_text("{}", encoding="utf-8")
    remote = Remote()
    statestore.push(var, remote)
    text = "_shared/pool/text/lever__acme/1.json"
    assert text in remote.rows
    pool.text_path(shared, "lever:acme", "1").unlink()
    (shared / "boards.json").unlink()                     # anything else: never deleted there
    rep = statestore.push(var, remote)
    assert rep.removed == [text] and text not in remote.rows and "_shared/boards.json" in remote.rows


def test_a_fresh_disk_pulls_descriptions_back_and_deletes_nothing(tmp_path):
    remote = Remote()
    a = tmp_path / "a"
    pool.save_text(a / "_shared", "lever:acme", "1", {"description": "x"})
    statestore.push(a, remote)
    b = tmp_path / "b"                                    # Render after a sleep: an empty disk
    rep = statestore.push(b, remote)
    assert rep.removed == [] and pool.load_text(b / "_shared", "lever:acme", "1")["description"] == "x"


# ── is it still open? ────────────────────────────────────────────────────────

class Fetch:
    def __init__(self, status):
        self.status, self.urls = status, []

    def get(self, url):
        self.urls.append(url)
        return Result(url=url, status=self.status, json={}, text="")


@pytest.mark.parametrize("status, answer", [(200, True), (404, False), (410, False), (500, None), (429, None)])
def test_asking_greenhouse_and_lever_about_one_posting(monkeypatch, status, answer):
    f = Fetch(status)
    assert REAL_ASK(f, "greenhouse:acme", "5", None) is answer
    assert f.urls == ["https://boards-api.greenhouse.io/v1/boards/acme/jobs/5"]
    assert REAL_ASK(Fetch(status), "lever:acme", "ab-1", None) is answer


def test_a_board_that_cant_answer_plainly_is_not_asked(monkeypatch):
    f = Fetch(404)
    assert REAL_ASK(f, "workday:x.wd1.Site", "1", None) is None and f.urls == []


def test_closed_on_the_board_closes_it_in_the_pool_and_is_remembered(shared, monkeypatch):
    pool.update(shared, [board(W.SEEDED, [P("1")], provider="greenhouse")])
    asked = []
    monkeypatch.setattr(pool, "_ask_board", lambda f, b, pid, c: asked.append(pid) or False)
    assert pool.check_live(shared, None, "greenhouse:acme", "1") is False
    assert pool.is_open(shared, "greenhouse:acme", "1") is False
    assert pool.check_live(shared, None, "greenhouse:acme", "1") is False and asked == ["1"]


def test_a_board_polled_minutes_ago_is_not_asked(shared, monkeypatch):
    pool.update(shared, [board(W.SEEDED, [P("1")], provider="greenhouse")])
    monkeypatch.setattr(pool, "_ask_board", lambda *a: pytest.fail("asked"))
    recent = datetime.now(timezone.utc).isoformat(timespec="seconds")
    assert pool.check_live(shared, None, "greenhouse:acme", "1", last_poll=lambda b: recent) is True


def test_a_request_that_fails_answers_nothing(shared, monkeypatch):
    def boom(*a):
        raise OSError("no network")
    monkeypatch.setattr(pool, "_ask_board", boom)
    assert pool.check_live(shared, None, "greenhouse:acme", "1") is None


# ── what a person sees ───────────────────────────────────────────────────────

def _prof(tmp_path, **recs):
    prof = make_root(tmp_path)
    prof.var.mkdir(parents=True, exist_ok=True)
    base = {"title": "Backend Engineer", "company": "Globex", "board": "greenhouse:globex", "verdict": "keep",
            "location": "Pune", "matched": {"Python": "core"}, "coverage": "1/2", "description": "text"}
    (prof.var / "triage-results.json").write_text(json.dumps(
        {"postings": {pid: {**base, "id": pid, **r} for pid, r in recs.items()}}), encoding="utf-8")
    return prof


def test_postings_older_than_the_window_leave_the_lists_unless_touched(tmp_path):
    from jobsearch import pipeline
    prof = _prof(tmp_path, new={"posted_at": "2026-10-10"}, old={"posted_at": "2026-05-01"},
                 old_starred={"posted_at": "2026-05-01"})
    pipeline.set_favorite(prof, "old_starred", True)
    assert {r["id"] for r in state.postings(prof)} == {"new", "old_starred"}
    assert state.posting(prof, "old") is not None                          # its own page still opens


def test_opening_a_posting_the_board_has_closed_says_so_and_drops_it(tmp_path, monkeypatch):
    prof = _prof(tmp_path, **{"1000001": {"posted_at": "2026-10-10"}})
    monkeypatch.setattr(pool, "_ask_board", lambda f, b, pid, c: False)
    data = routes.get(prof, "/api/postings/1000001", {}, Runner(prof), scope=HOSTED)
    assert data["closed"] is True and data["live"] is False      # the page says "closed on the board"
    assert all(r["id"] != "1000001" or r["closed"] for r in state.postings(prof))
    assert not routes.get(prof, "/api/today", {}, Runner(prof), scope=HOSTED)["decide"]


def test_no_resume_is_built_for_a_closed_posting(tmp_path, monkeypatch, capsys):
    prof = _prof(tmp_path, **{"1000001": {"posted_at": "2026-10-10"}})
    monkeypatch.setattr(pool, "_ask_board", lambda f, b, pid, c: False)
    import jobsearch.assemble_cli
    monkeypatch.setattr(jobsearch.assemble_cli, "run_assemble", lambda *a, **k: pytest.fail("built"))
    assert actions._assemble(prof, {"posting": "1000001"}) == 1
    assert "closed" in capsys.readouterr().out


def test_greenhouse_dates_are_when_it_was_first_published():
    from jobsearch.providers import parse_greenhouse
    got = parse_greenhouse({"jobs": [{"id": 1, "title": "t", "location": {"name": "x"}, "absolute_url": "u",
                                      "updated_at": "2026-10-15T15:24:46-04:00",
                                      "first_published": "2026-07-22T13:38:40-04:00"}]})
    assert pool.posted_day(got[0].updated_at) == "2026-07-22"
