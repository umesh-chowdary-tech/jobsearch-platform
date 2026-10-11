"""The two pieces of state the app adds: decisions, and who is writing."""
from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from jobsearch import pipeline
from jobsearch.busy import EditTimeout, busy, editing, holder
from jobsearch.cycle import run_cycle
from jobsearch.profile import Profile
from samples import make_root

SRC = Path(__file__).resolve().parents[1] / "src"


def P(root: Path) -> Profile:
    """A profile in `root`. Decisions need no facts, only the profile's var/."""
    return Profile(root, "someone")


# ── pipeline ─────────────────────────────────────────────────────────────────

def test_an_undecided_posting_is_new(tmp_path):
    assert pipeline.status(pipeline.load(P(tmp_path)), "123") == "new"


def test_a_decision_is_recorded_with_history(tmp_path):
    pipeline.set_status(P(tmp_path), "123", "shortlisted")
    e = pipeline.set_status(P(tmp_path), "123", "applied", note="via portal", variant="fullstack")
    assert e["status"] == "applied" and e["variant"] == "fullstack"
    assert [h["status"] for h in e["history"]] == ["shortlisted", "applied"]
    assert e["history"][-1]["note"] == "via portal"
    assert pipeline.status(pipeline.load(P(tmp_path)), "123") == "applied"


def test_a_misclick_is_undone_by_clicking_again_and_both_are_kept(tmp_path):
    pipeline.set_status(P(tmp_path), "123", "skipped")
    e = pipeline.set_status(P(tmp_path), "123", "shortlisted")
    assert e["status"] == "shortlisted" and len(e["history"]) == 2


def test_a_double_click_is_not_an_event(tmp_path):
    pipeline.set_status(P(tmp_path), "123", "skipped")
    e = pipeline.set_status(P(tmp_path), "123", "skipped")
    assert len(e["history"]) == 1


def test_unknown_status_is_refused(tmp_path):
    with pytest.raises(pipeline.PipelineError):
        pipeline.set_status(P(tmp_path), "123", "ghosted")


def test_a_corrupt_file_is_an_error_not_an_empty_pipeline(tmp_path):
    """Empty would put every skipped posting back on the Today screen."""
    P(tmp_path).var.mkdir(parents=True)
    pipeline.path(P(tmp_path)).write_text("{not json", encoding="utf-8")
    with pytest.raises(pipeline.PipelineError):
        pipeline.load(P(tmp_path))


# ── busy: across processes, which is the point ───────────────────────────────

def _child(var: Path, sleep: float = 0) -> str:
    return textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, r'{SRC}')
        from pathlib import Path
        from jobsearch.busy import busy
        with busy(Path(r'{var}'), 'child') as got:
            print(got, flush=True)
            time.sleep({sleep})
    """)


def test_another_process_is_refused_while_it_is_held(tmp_path):
    with busy(tmp_path, "scheduled cycle") as got:
        assert got
        out = subprocess.run([sys.executable, "-c", _child(tmp_path)],
                             capture_output=True, text=True).stdout.strip()
        assert out == "False"
        assert holder(tmp_path)["what"] == "scheduled cycle"
    assert holder(tmp_path) is None


def test_a_killed_holder_leaves_no_stale_lock(tmp_path):
    p = subprocess.Popen([sys.executable, "-c", _child(tmp_path, sleep=30)],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "True"
    assert holder(tmp_path)["what"] == "child"
    p.kill()
    p.wait()
    # Windows releases a dead process's byte-range locks asynchronously ("the
    # time it takes ... depends upon available system resources" - LockFileEx
    # docs). Under a loaded test run this failed once with no wait at all.
    # The property is "frees itself within moments", not "instantly".
    deadline = time.monotonic() + 5
    while holder(tmp_path) is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert holder(tmp_path) is None
    with busy(tmp_path, "next") as got:
        assert got


def test_a_scheduled_cycle_skips_its_tick_while_the_app_is_busy(tmp_path):
    var = tmp_path / "var" / "_shared"
    out = subprocess.Popen([sys.executable, "-c", _child(var, sleep=30)],
                           stdout=subprocess.PIPE, text=True)
    try:
        assert out.stdout.readline().strip() == "True"
        assert run_cycle(tmp_path, quiet=True) == 0      # skipped, not failed
        log = next((var / "logs").glob("cycle-*.log")).read_text(encoding="utf-8")
        assert "cycle SKIPPED busy: child" in log
    finally:
        out.kill()
        out.wait()


# ── editing: short, and waited for ───────────────────────────────────────────

def _editor(root: Path, sleep: float) -> str:
    """A child that holds the edit lock with a stale copy of pipeline.json,
    then saves it - what the inbox sync does with the files."""
    return textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, r'{SRC}')
        from pathlib import Path
        from jobsearch import pipeline
        from jobsearch.busy import editing
        from jobsearch.profile import Profile
        prof = Profile(Path(r'{root}'), 'someone')
        with editing(prof.var):
            data = pipeline.load(prof)
            print('held', flush=True)
            time.sleep({sleep})
            data['from-child'] = {{'status': 'shortlisted', 'history': []}}
            pipeline._save(prof, data)
    """)


def test_an_edit_waits_for_another_process_and_both_changes_survive(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", _editor(tmp_path, 0.8)], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        t0 = time.monotonic()
        pipeline.set_status(P(tmp_path), "from-app", "applied")
        assert time.monotonic() - t0 > 0.3                       # it waited its turn
    finally:
        child.wait(10)
    data = pipeline.load(P(tmp_path))
    # Without the lock the child saves its stale copy last, and "applied" is gone.
    assert data["from-child"]["status"] == "shortlisted" and data["from-app"]["status"] == "applied"


def test_an_edit_inside_an_edit_does_not_wait_for_itself(tmp_path):
    with editing(P(tmp_path).var, timeout=1):
        pipeline.set_status(P(tmp_path), "1", "applied")            # the sync moving a posting
    assert pipeline.status(pipeline.load(P(tmp_path)), "1") == "applied"


def test_a_lock_held_too_long_is_an_error_not_a_hang(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", _editor(tmp_path, 30)], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        with pytest.raises(EditTimeout):
            with editing(P(tmp_path).var, timeout=0.3):
                pass
    finally:
        child.kill()
        child.wait()


# ── favorite and save-for-later ──────────────────────────────────────────────

from datetime import datetime, timedelta, timezone  # noqa: E402

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def test_a_favorite_is_a_flag_not_a_stage(tmp_path):
    pipeline.set_status(P(tmp_path), "1", "applied")
    e = pipeline.set_favorite(P(tmp_path), "1", True)
    assert e["status"] == "applied" and e["favorite"]
    assert pipeline.set_favorite(P(tmp_path), "1", False).get("favorite") is None


def test_favoriting_twice_keeps_the_first_time(tmp_path):
    first = pipeline.set_favorite(P(tmp_path), "1", True)["favorite"]
    assert pipeline.set_favorite(P(tmp_path), "1", True)["favorite"] == first


@pytest.mark.parametrize("days, now, expect", [
    (7, NOW + timedelta(days=6), "waiting"),
    (7, NOW + timedelta(days=7), "due"),
    (None, NOW + timedelta(days=400), "someday"),
])
def test_later_comes_back_on_its_date(tmp_path, days, now, expect):
    e = pipeline.save_for_later(P(tmp_path), "1", days, now=NOW)
    assert pipeline.later_state(e, now=now) == expect


def test_deciding_clears_later(tmp_path):
    """Later meant "decide later"; a decision answers it."""
    pipeline.save_for_later(P(tmp_path), "1", 7)
    assert "later" not in pipeline.set_status(P(tmp_path), "1", "shortlisted")


def test_bring_back_now(tmp_path):
    pipeline.save_for_later(P(tmp_path), "1", 14)
    assert pipeline.later_state(pipeline.clear_later(P(tmp_path), "1")) is None


@pytest.mark.parametrize("days", [0, -3, 91, 2.5, "7", True])
def test_nonsense_days_are_refused(tmp_path, days):
    """True included: bool is an int in Python, and would mean one day."""
    with pytest.raises(pipeline.PipelineError):
        pipeline.save_for_later(P(tmp_path), "1", days)


# ── later, from a calendar ───────────────────────────────────────────────────

IST = timezone(timedelta(hours=5, minutes=30))


def test_a_picked_date_comes_back_at_local_midnight(tmp_path):
    """Oct 3 in Hyderabad starts at 18:30 UTC on Oct 2 - that is when it returns."""
    oct3 = datetime(2026, 10, 3, tzinfo=IST)
    e = pipeline.save_for_later(P(tmp_path), "1", until=oct3, now=NOW)
    assert e["later"]["until"] == "2026-10-03T00:00:00+05:30"
    assert pipeline.later_state(e, now=datetime(2026, 10, 2, 18, 29, tzinfo=timezone.utc)) == "waiting"
    assert pipeline.later_state(e, now=datetime(2026, 10, 2, 18, 30, tzinfo=timezone.utc)) == "due"


@pytest.mark.parametrize("until, why", [
    (NOW - timedelta(hours=1), "after today"),
    (NOW + timedelta(days=92), "within 90"),
    (datetime(2026, 10, 3), "timezone"),                 # naive: ambiguous by a day
])
def test_a_bad_date_is_refused(tmp_path, until, why):
    with pytest.raises(pipeline.PipelineError, match=why):
        pipeline.save_for_later(P(tmp_path), "1", until=until, now=NOW)


@pytest.mark.parametrize("tz", [IST, timezone(timedelta(hours=-8)), timezone(timedelta(hours=14))])
def test_the_calendars_last_day_is_allowed_and_the_next_is_not(tmp_path, tz):
    """The page offers local today + 90. That must pass in any zone, and 91 must not."""
    midnight = NOW.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    pipeline.save_for_later(P(tmp_path), "1", until=midnight + timedelta(days=90), now=NOW)
    with pytest.raises(pipeline.PipelineError, match="within 90"):
        pipeline.save_for_later(P(tmp_path), "1", until=midnight + timedelta(days=91), now=NOW)


def test_days_and_a_date_together_is_refused(tmp_path):
    with pytest.raises(pipeline.PipelineError):
        pipeline.save_for_later(P(tmp_path), "1", days=3, until=NOW + timedelta(days=3), now=NOW)


def test_a_cycle_frozen_by_sleep_reports_how_long_it_was_paused(tmp_path, monkeypatch):
    """A laptop asleep mid-cycle: the app said "started 14 h ago" as if the
    cycle had worked all that time. The heartbeat counts the gap instead."""
    import time as real_time
    from jobsearch import busy as B
    monkeypatch.setattr(B, "BEAT", 0.05)
    monkeypatch.setattr(B, "ASLEEP", 0.15)
    jump = [0.0]
    monkeypatch.setattr(B.time, "time", lambda: real_time.monotonic() + jump[0])
    with B.busy(tmp_path / "var", "scheduled cycle") as got:
        assert got
        real_time.sleep(0.12)
        assert B.holder(tmp_path / "var")["paused_s"] == 0
        jump[0] += 6 * 3600                                     # the PC sleeps six hours
        real_time.sleep(0.2)
        info = B.holder(tmp_path / "var")
        assert 6 * 3600 - 5 <= info["paused_s"] <= 6 * 3600 and info["beat"] >= info["since"]
    assert B.holder(tmp_path / "var") is None and not (tmp_path / "var" / ".busy.json").exists()


# ── pause and stop, from the app ─────────────────────────────────────────────

def test_a_paused_schedule_skips_its_tick_until_resumed(tmp_path):
    from jobsearch.cycle import pause, paused, resume
    var = tmp_path / "var" / "_shared"
    pause(var, None)
    assert run_cycle(tmp_path, quiet=True) == 0
    log = next((var / "logs").glob("cycle-*.log")).read_text(encoding="utf-8")
    assert "cycle SKIPPED paused until resumed" in log and "no boards" not in log
    resume(var)
    assert paused(var) is None
    assert run_cycle(tmp_path, quiet=True) == 1                 # ran: no boards known
    assert "watch SKIPPED no boards" in next((var / "logs").glob("cycle-*.log")).read_text(encoding="utf-8")


def test_a_pause_that_has_run_out_is_no_pause(tmp_path):
    from jobsearch.cycle import pause, paused
    pause(tmp_path, 2)
    assert paused(tmp_path)["until"]
    pause(tmp_path, -1)                                         # ended an hour ago
    assert paused(tmp_path) is None


def test_the_app_running_a_cycle_ignores_the_pause(tmp_path):
    from jobsearch.cycle import pause
    pause(tmp_path / "var" / "_shared", None)
    assert run_cycle(tmp_path, quiet=True, lock=False) == 1      # ran, found no boards


def test_a_stop_is_for_the_run_it_was_asked_of(tmp_path):
    from jobsearch.busy import request_stop, stop_requested
    var = tmp_path / "var"
    assert request_stop(var) is None                             # nothing running
    with busy(var, "scheduled cycle"):
        assert not stop_requested(var)
        assert request_stop(var)["what"] == "scheduled cycle"
        assert stop_requested(var)
    assert not (var / ".stop.json").exists()
    with busy(var, "scheduled cycle"):
        assert not stop_requested(var)                           # the next run goes ahead


def test_a_stale_stop_never_stops_a_later_run(tmp_path):
    import json
    from jobsearch.busy import stop_requested
    var = tmp_path / "var"
    var.mkdir()
    (var / ".stop.json").write_text(json.dumps({"since": "2026-01-01T00:00:00+00:00"}))
    with busy(var, "scheduled cycle"):
        assert not stop_requested(var)


def test_watch_stops_between_boards_and_leaves_the_rest_untouched(tmp_path, monkeypatch):
    from jobsearch import watch as W
    from jobsearch.busy import request_stop
    var = tmp_path / "var"
    polled = []

    def fake(fetcher, var_dir, board):
        polled.append(board["slug"])
        request_stop(var_dir)                                    # asked during the first board
        return W.BoardWatch(provider="greenhouse", slug=board["slug"], company=board["slug"], status="ok")

    monkeypatch.setattr(W, "watch_board", fake)
    with busy(var, "scheduled cycle"):
        run = W.watch_all(None, var, [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}])
    assert polled == ["a"] and [b.slug for b in run.boards] == ["a"]


def test_a_stopped_cycle_records_what_it_saw_and_ends(tmp_path, monkeypatch):
    from jobsearch import cycle as C
    from jobsearch import watch as W
    from jobsearch.busy import request_stop
    var = tmp_path / "var" / "_shared"

    def fake_watch(fetcher, var_dir, boards):
        request_stop(var_dir)
        return W.WatchRun()

    def never(*a, **k):
        raise AssertionError("ran after the stop")

    monkeypatch.setattr(C, "load_boards", lambda v: [{"slug": "a"}])
    monkeypatch.setattr(C, "default_fetcher", lambda v: None)
    monkeypatch.setattr(C, "watch_all", fake_watch)
    for name in ("sync_closed", "run_triage", "apply_to_results", "render"):
        monkeypatch.setattr(C, name, never)
    assert run_cycle(tmp_path, quiet=True) == 0
    log = next((var / "logs").glob("cycle-*.log")).read_text(encoding="utf-8")
    assert "STOPPED by you after watch" in log
    assert (var / "new-postings.jsonl").exists()                 # what watch saw was recorded


def test_a_cycle_that_ranks_runs_to_the_end(tmp_path, monkeypatch):
    """`stopped` was both the stop check and the rank step's note: once a
    model ranked anything, the next check called a string and the cycle died
    before the inbox, every 15 minutes."""
    from jobsearch import cycle as C
    from jobsearch import watch as W
    prof = make_root(tmp_path)
    var = prof.shared
    (prof.var / "triage-results.json").write_text('{"postings": {}}', encoding="utf-8")

    class Key:
        def __init__(self, prof):
            pass

        def key_check(self):
            return True, ""

    import jobsearch.mailbox
    # The inbox steps follow ranking; with mail set up on this PC they would
    # read the real mailbox settings. Off, so this tests the cycle alone.
    monkeypatch.setattr(jobsearch.mailbox, "configured", lambda prof: False)
    monkeypatch.setattr(C, "load_boards", lambda v: [{"slug": "a"}])
    monkeypatch.setattr(C, "default_fetcher", lambda v: None)
    monkeypatch.setattr(C, "watch_all", lambda f, v, b: W.WatchRun())
    monkeypatch.setattr(C, "sync_closed", lambda v, live: ([], []))
    monkeypatch.setattr(C, "unread", lambda blob, lead="": [{"id": "1"}])
    monkeypatch.setattr(C, "LLM", Key)
    monkeypatch.setattr(C, "apply_to_results", lambda prof, budget=None: {
        "ranked": 1, "left": 0, "attribution": "x via groq", "stopped": "time budget"})
    def render(p):
        (p.var / "dashboard.html").write_text("x")
        return p.var / "dashboard.html"
    monkeypatch.setattr(C, "render", render)
    code = run_cycle(tmp_path, quiet=True)
    log = next((var / "logs").glob("cycle-*.log")).read_text(encoding="utf-8")
    assert code == 0, log
    assert "rank read=1" in log and "stopped='time budget'" in log and "dashboard" in log


def test_a_cycle_that_raises_is_not_run_twice(tmp_path, monkeypatch):
    """The database fallback is for an unreachable database - not a second
    poll of every board after the cycle itself failed."""
    import contextlib
    from jobsearch import cli, statestore

    class Remote:
        @contextlib.contextmanager
        def cycle_lock(self):
            yield True

    runs = []

    def boom(root, quiet, profiles=None):
        runs.append(1)
        raise RuntimeError("cycle bug")
    import jobsearch.llm
    # cmd_cycle loads the real .env - keys and all - into this process.
    monkeypatch.setattr(jobsearch.llm, "load_dotenv", lambda path: None)
    monkeypatch.setattr(statestore, "remote_from_env", lambda: Remote())
    monkeypatch.setattr(statestore, "pull", lambda var, r: None)
    monkeypatch.setattr(cli, "run_cycle", boom)
    with pytest.raises(RuntimeError):
        cli.cmd_cycle(__import__("argparse").Namespace(quiet=True))
    assert runs == [1]
