"""Layout tests - the structure that made the difference between a generated
resume and one worth sending."""
from __future__ import annotations

import pytest

from jobsearch.resume_layout import (
    entry_period,
    group_skills,
    group_work,
    heading,
    lead_line,
)
from jobsearch.skills import load_index
from samples import sample


@pytest.fixture(scope="module")
def index():
    return load_index(sample())


def _w(where, period, **kw):
    return {"type": "work", "where": where, "period": period, **kw}


# ── employer grouping ────────────────────────────────────────────────────────

def test_one_block_per_employer():
    """Six consecutive headings for one employer read as six jobs, not one role
    with depth. This was the single biggest thing making generated output
    weaker than a hand-written resume."""
    blocks = group_work([
        _w("Acme Freight", "2026-present"), _w("Acme Freight", "2023-present"),
        _w("Acme Freight", "2023-present"), _w("Other Co", "2021-2022"),
    ])
    assert len(blocks) == 2
    acme = next(b for b in blocks if b["employer"] == "Acme Freight")
    assert len(acme["entries"]) == 3


def test_the_employer_range_spans_all_its_entries():
    """The job began 2023-09 even though the newest workstream starts in 2026.
    The block header must show the employment, not the newest workstream."""
    blocks = group_work([_w("Acme Freight", "2026-present"),
                         _w("Acme Freight", "2023-09 to ~2024")])
    assert "2023" in blocks[0]["period"]


def test_current_employer_sorts_first():
    blocks = group_work([_w("Old Co", "2019-2021"), _w("Acme Freight", "2023-present")])
    assert blocks[0]["employer"] == "Acme Freight"


def test_entries_within_a_block_are_newest_first():
    blocks = group_work([_w("Acme", "2023-present"), _w("Acme", "2026-present")])
    assert blocks[0]["entries"][0]["period"] == "2026-present"


# ── projects and their pieces ────────────────────────────────────────────────

def test_pieces_print_under_their_project():
    """The case that broke a real resume: six workstreams in a row, and a
    parser took the first piece ("Ticket Agent") for the job."""
    blocks = group_work([
        _w("Acme", "2025-present", id="platform"), _w("Acme", "2026-present", id="ticket", part_of="platform"),
        _w("Acme", "2023-present", id="track"), _w("Acme", "2024", id="checker", part_of="track"),
    ])
    groups = blocks[0]["projects"]
    assert [g["entry"]["id"] for g in groups] == ["platform", "track"]     # by its newest piece
    assert [p["id"] for p in groups[0]["pieces"]] == ["ticket"]
    assert [e["id"] for e in blocks[0]["entries"]] == ["platform", "ticket", "track", "checker"]


def test_a_piece_whose_project_is_not_on_the_page_stands_alone():
    blocks = group_work([_w("Acme", "2026-present", id="ticket", part_of="platform")])
    assert blocks[0]["projects"][0]["entry"]["id"] == "ticket"


def test_the_dated_line_names_the_job_even_without_a_title():
    """No title saved: the dated line said only "Apr 2022 - Present", and a
    parser read the next bold line as the job. The employer goes there."""
    from jobsearch.resume_layout import job_line
    assert job_line({"title": {"official": ""}}, "", "Acme", "Pune") == ("Acme", "Pune")
    assert job_line({"title": {"official": "SE II"}}, "", "Acme", "") == ("SE II", "Acme")


# ── dates ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expect", [
    ("2026-present", "2026 - Present"),
    ("2023-09 to ~2024", "Sep 2023 - 2024"),
    ("2019-03 to 2023-06", "Mar 2019 - Jun 2023"),
    ("2025", "2025"),
    (None, ""),
])
def test_periods_render_for_a_human(raw, expect):
    assert entry_period({"period": raw}) == expect


# ── headings ─────────────────────────────────────────────────────────────────

def test_a_short_title_becomes_the_heading():
    e = {"title": "Billing Reconciler", "headline": "Built a statistical dashboard"}
    assert heading(e) == "Billing Reconciler"
    assert lead_line(e) == "Built a statistical dashboard"


def test_without_a_title_the_headline_is_the_heading_and_is_not_repeated():
    e = {"headline": "Built a statistical dashboard"}
    assert heading(e) == "Built a statistical dashboard"
    assert lead_line(e) == ""


# ── skills grouping ──────────────────────────────────────────────────────────

def test_skills_are_grouped_by_category(index):
    groups = dict(group_skills(["Python", "React", "Docker", "PostgreSQL"], index))
    assert "Languages" in groups and "Python" in groups["Languages"]
    assert "Cloud & DevOps" in groups and "Docker" in groups["Cloud & DevOps"]


def test_every_skill_lands_somewhere(index):
    skills = ["Python", "React", "Docker", "PostgreSQL", "Claude APIs", "Jest"]
    placed = [s for _, items in group_skills(skills, index) for s in items]
    assert sorted(placed) == sorted(skills)


def test_empty_groups_are_dropped(index):
    labels = [l for l, _ in group_skills(["Python"], index)]
    assert labels == ["Languages"]
