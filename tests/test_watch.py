"""Watcher tests. The fetch is stubbed; the diff and the state handling are real."""
from __future__ import annotations

import json

import pytest

from jobsearch import watch as W
from jobsearch.models import ERROR, NOT_FOUND, OK, BoardProbe, Posting

BOARD = {"provider": "lever", "slug": "cred", "company": "CRED"}


def _posts(*ids):
    return [Posting(str(i), f"Role {i}", "Hyderabad", f"https://x.invalid/{i}") for i in ids]


@pytest.fixture
def stub(monkeypatch):
    """Replace the network probe with a scripted sequence of results."""
    state = {"queue": []}

    def fake_probe(fetcher, provider, slug, company=None, with_board_name=True):
        item = state["queue"].pop(0)
        if isinstance(item, Exception):
            raise item
        status, posts = item
        p = BoardProbe(company=company or slug, provider=provider, slug=slug,
                       status=status, url="https://x.invalid")
        p.postings = posts
        if status not in (OK, "empty"):
            p.error = "stubbed failure"
        return p

    monkeypatch.setattr(W, "probe", fake_probe)
    return state


def _seen(tmp_path):
    return W.load_seen(W.seen_path(tmp_path, "lever", "cred"))


# ── seeding ──────────────────────────────────────────────────────────────────

def test_first_sight_seeds_and_reports_nothing(tmp_path, stub):
    """Otherwise the first run emits all 4,869 postings and buries the first
    genuine diff in noise."""
    stub["queue"] = [(OK, _posts(1, 2, 3))]
    w = W.watch_board(None, tmp_path, BOARD)
    assert w.status == W.SEEDED
    assert w.added == [] and w.removed == []
    assert w.total == 3
    assert set(_seen(tmp_path)["ids"]) == {"1", "2", "3"}


def test_second_poll_with_no_change_reports_nothing(tmp_path, stub):
    stub["queue"] = [(OK, _posts(1, 2, 3)), (OK, _posts(1, 2, 3))]
    W.watch_board(None, tmp_path, BOARD)
    w = W.watch_board(None, tmp_path, BOARD)
    assert w.status == W.POLLED
    assert w.added == [] and w.removed == []


# ── the diff ─────────────────────────────────────────────────────────────────

def test_a_new_posting_is_reported(tmp_path, stub):
    stub["queue"] = [(OK, _posts(1, 2)), (OK, _posts(1, 2, 3))]
    W.watch_board(None, tmp_path, BOARD)
    w = W.watch_board(None, tmp_path, BOARD)
    assert [p.id for p in w.added] == ["3"]
    assert w.removed == []


def test_a_closed_posting_is_reported_separately(tmp_path, stub):
    stub["queue"] = [(OK, _posts(1, 2, 3)), (OK, _posts(1, 3))]
    W.watch_board(None, tmp_path, BOARD)
    w = W.watch_board(None, tmp_path, BOARD)
    assert w.added == [] and w.removed == ["2"]


def test_additions_and_removals_in_one_poll(tmp_path, stub):
    stub["queue"] = [(OK, _posts(1, 2)), (OK, _posts(2, 3))]
    W.watch_board(None, tmp_path, BOARD)
    w = W.watch_board(None, tmp_path, BOARD)
    assert [p.id for p in w.added] == ["3"] and w.removed == ["1"]


def test_an_edited_title_is_not_a_new_posting(tmp_path, stub):
    """The diff is on the ATS id, which is stable. Diffing on title or url would
    report every copy-edit as a new job."""
    stub["queue"] = [
        (OK, [Posting("1", "SDE", "Hyderabad", "https://x.invalid/1")]),
        (OK, [Posting("1", "SDE II", "Bengaluru", "https://x.invalid/1-renamed")]),
    ]
    W.watch_board(None, tmp_path, BOARD)
    w = W.watch_board(None, tmp_path, BOARD)
    assert w.added == [] and w.removed == []


# ── failure must not corrupt the baseline ────────────────────────────────────

def test_an_error_does_not_touch_the_baseline(tmp_path, stub):
    """If a timeout overwrote the baseline with nothing, the next successful
    poll would report the entire board as new."""
    stub["queue"] = [(OK, _posts(1, 2, 3)), (ERROR, []), (OK, _posts(1, 2, 3))]
    W.watch_board(None, tmp_path, BOARD)
    before = _seen(tmp_path)["ids"]

    w = W.watch_board(None, tmp_path, BOARD)
    assert w.status == W.SKIPPED
    assert _seen(tmp_path)["ids"] == before      # untouched

    w = W.watch_board(None, tmp_path, BOARD)
    assert w.added == []                          # and no false flood


def test_a_404_is_skipped_with_a_pointed_message(tmp_path, stub):
    stub["queue"] = [(OK, _posts(1)), (NOT_FOUND, [])]
    W.watch_board(None, tmp_path, BOARD)
    w = W.watch_board(None, tmp_path, BOARD)
    assert w.status == W.SKIPPED
    assert "discovery" in w.error


def test_a_corrupt_baseline_reseeds_rather_than_flooding(tmp_path, stub):
    stub["queue"] = [(OK, _posts(1, 2, 3)), (OK, _posts(1, 2, 3))]
    W.watch_board(None, tmp_path, BOARD)
    W.seen_path(tmp_path, "lever", "cred").write_text("{tru", encoding="utf-8")
    w = W.watch_board(None, tmp_path, BOARD)
    assert w.status == W.SEEDED and w.added == []


def test_save_seen_is_atomic_and_leaves_no_temp_files(tmp_path):
    path = W.seen_path(tmp_path, "lever", "cred")
    W.save_seen(path, ["a", "b"], None)
    W.save_seen(path, ["a", "b", "c"], W.load_seen(path))
    assert W.load_seen(path)["poll_count"] == 2
    assert list(path.parent.glob("*.tmp")) == []


def test_poll_count_and_seeded_at_survive(tmp_path, stub):
    stub["queue"] = [(OK, _posts(1)), (OK, _posts(1)), (OK, _posts(1))]
    for _ in range(3):
        W.watch_board(None, tmp_path, BOARD)
    s = _seen(tmp_path)
    assert s["poll_count"] == 3
    assert s["seeded_at"] <= s["last_poll"]


# ── run level ────────────────────────────────────────────────────────────────

def test_summary_counts_each_status(tmp_path, stub):
    stub["queue"] = [(OK, _posts(1)), (ERROR, [])]
    run = W.watch_all(None, tmp_path, [BOARD, {**BOARD, "slug": "meesho"}])
    s = run.summary()
    assert s["boards"] == 2 and s["seeded"] == 1 and s["skipped"] == 1


def test_a_board_that_raises_is_skipped_and_the_rest_still_record(tmp_path, stub):
    """2026-09-24: a stale app process met a board type it did not know. The
    ValueError aborted the run after the boards before it had saved their new
    baselines, so anything new on them was marked seen and never recorded."""
    other = {**BOARD, "slug": "meesho"}
    stub["queue"] = [(OK, _posts(1)), (OK, _posts(7)),                      # seed both
                     (OK, _posts(1, 2)), ValueError("unknown provider: x")]  # then poll
    W.watch_all(None, tmp_path, [BOARD, other])
    before = W.load_seen(W.seen_path(tmp_path, "lever", "meesho"))["ids"]

    run = W.watch_all(None, tmp_path, [BOARD, other])
    assert [p.id for _, p in run.added] == ["2"]
    bad = run.boards[1]
    assert bad.status == W.SKIPPED and "unknown provider" in bad.error
    assert W.load_seen(W.seen_path(tmp_path, "lever", "meesho"))["ids"] == before
    assert run.summary()["skipped"] == 1


def test_record_new_appends_one_line_per_posting(tmp_path, stub):
    stub["queue"] = [(OK, _posts(1)), (OK, _posts(1, 2, 3))]
    W.watch_board(None, tmp_path, BOARD)
    run = W.WatchRun(boards=[W.watch_board(None, tmp_path, BOARD)])
    path = W.record_new(tmp_path, run)
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["board"] == "lever:cred" and rows[0]["company"] == "CRED"
    assert "first_seen" in rows[0]


def test_load_boards_without_a_file_is_empty_not_an_error(tmp_path):
    assert W.load_boards(tmp_path) == []
