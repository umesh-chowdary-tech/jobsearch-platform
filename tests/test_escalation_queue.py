"""The escalation queue is the human-in-the-loop. If it fills with noise, the
human stops reading it, and the loop quietly stops having a human in it."""
from __future__ import annotations

import json

from jobsearch.models import ERROR, NOT_FOUND, OK, BoardProbe, Escalation, Posting
from jobsearch.registry import (
    _append_escalations,
    open_escalations,
    read_escalations,
    resolve_escalation,
    write_run,
)


def _esc(slug="purestorage", kind="identity_mismatch") -> Escalation:
    return Escalation(
        kind=kind,
        company=slug,
        provider="greenhouse",
        slug=slug,
        reason="board is named Everpure",
        evidence={"board_name": "Everpure"},
    )


def test_first_escalation_is_written(tmp_path):
    p = tmp_path / "e.jsonl"
    assert _append_escalations(p, [_esc()]) == 1
    assert len(open_escalations(p)) == 1


def test_the_same_escalation_is_not_appended_twice(tmp_path):
    """Discovery reruns on a schedule. An unresolved doubt must not append a new
    row every run until the file is unreadable."""
    p = tmp_path / "e.jsonl"
    _append_escalations(p, [_esc()])
    for _ in range(10):
        assert _append_escalations(p, [_esc()]) == 0
    assert len(read_escalations(p)) == 1


def test_different_slugs_are_separate_escalations(tmp_path):
    p = tmp_path / "e.jsonl"
    _append_escalations(p, [_esc("purestorage"), _esc("everpure")])
    assert len(open_escalations(p)) == 2


def test_resolving_closes_it(tmp_path):
    p = tmp_path / "e.jsonl"
    _append_escalations(p, [_esc()])
    resolve_escalation(p, "greenhouse", "purestorage", "Everpure is Pure Storage's brand")
    assert open_escalations(p) == []


def test_a_resolved_escalation_does_not_come_back(tmp_path):
    """The next scheduled run will raise the same doubt again. It must stay shut."""
    p = tmp_path / "e.jsonl"
    _append_escalations(p, [_esc()])
    resolve_escalation(p, "greenhouse", "purestorage", "checked, it is them")
    assert _append_escalations(p, [_esc()]) == 0
    assert open_escalations(p) == []


def test_resolution_is_appended_not_edited(tmp_path):
    """The history is the audit trail - nothing is ever rewritten."""
    p = tmp_path / "e.jsonl"
    _append_escalations(p, [_esc()])
    resolve_escalation(p, "greenhouse", "purestorage", "why")
    rows = read_escalations(p)
    assert len(rows) == 2
    assert rows[0]["kind"] == "identity_mismatch"     # original still there
    assert rows[1]["kind"] == "resolved"
    assert rows[1]["note"] == "why"


def test_missing_file_is_an_empty_queue(tmp_path):
    assert open_escalations(tmp_path / "nope.jsonl") == []


def test_corrupt_lines_are_skipped_not_fatal(tmp_path):
    p = tmp_path / "e.jsonl"
    _append_escalations(p, [_esc()])
    with p.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert len(open_escalations(p)) == 1


# ── boards.json must survive a partial run ───────────────────────────────────

def _probe(provider, slug, status=OK, n=1):
    p = BoardProbe(company=slug, provider=provider, slug=slug, status=status,
                   url=f"https://{provider}.invalid/{slug}")
    p.postings = [Posting(str(i), "t", "l", "u") for i in range(n)]
    return p


def _boards(tmp_path):
    return json.loads((tmp_path / "boards.json").read_text(encoding="utf-8"))


def test_a_partial_run_does_not_wipe_other_boards(tmp_path):
    """`verify --provider lever` probes 5 of 24 boards. Replacing rather than
    merging would delete the other 19 from the watcher's world silently."""
    write_run(tmp_path, [_probe("greenhouse", "druva"), _probe("lever", "cred")], [], {})
    assert set(_boards(tmp_path)) == {"greenhouse:druva", "lever:cred"}

    write_run(tmp_path, [_probe("lever", "cred", n=9)], [], {})
    b = _boards(tmp_path)
    assert set(b) == {"greenhouse:druva", "lever:cred"}
    assert b["lever:cred"]["posting_count"] == 9        # refreshed
    assert b["greenhouse:druva"]["posting_count"] == 1  # untouched


def test_a_404_removes_the_board(tmp_path):
    write_run(tmp_path, [_probe("lever", "gone")], [], {})
    assert "lever:gone" in _boards(tmp_path)
    write_run(tmp_path, [_probe("lever", "gone", status=NOT_FOUND, n=0)], [], {})
    assert "lever:gone" not in _boards(tmp_path)


def test_a_transient_error_leaves_the_board_alone(tmp_path):
    """Absence of evidence is not evidence the board is gone. A timeout must not
    quietly shrink the watcher's registry."""
    write_run(tmp_path, [_probe("ashby", "atlan", n=8)], [], {})
    write_run(tmp_path, [_probe("ashby", "atlan", status=ERROR, n=0)], [], {})
    b = _boards(tmp_path)
    assert b["ashby:atlan"]["posting_count"] == 8
