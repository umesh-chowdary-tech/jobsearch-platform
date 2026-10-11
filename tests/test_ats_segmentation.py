"""How an ATS reads the Experience section - not how a human does.

A real Workday application parsed a resume that looked right into: Job Title
"Checker", start date January of the start year. Every earlier check passed.
These pin the three causes, and run the checks on the old layout to prove they
would have caught it. The person here is the fictional sample profile.
"""
from __future__ import annotations


import pytest

from jobsearch.assemble import Variant, load_store, select_entries, surfaced_skills
from jobsearch.latex import latex_to_text, render_latex
from jobsearch.render import (_RANGE, check_text, experience_section, render_html,
                              render_text, strip_html)
from jobsearch.skills import load_index
from samples import sample

# The old layout's Experience section, as a parser reads it: one dated line
# per workstream, and no job title on any of them.
WORKDAY_READ = """Kavya Example
Software Engineer II (Backend AI Engineer)
SKILLS
Languages: Python
EXPERIENCE
Acme Freight 2022 - Present
Agent Platform - Operations AI 2025 - Present
Built Acme's internal AI agent platform for the operations team
Acme Track - Shipment Tracking Platform 2022 - Present
Backend engineer on Acme Track, the shipment tracking platform
Billing Reconciler 2022 - Present
Built a nightly check that compares carrier invoices with the rates Acme booked
PROJECTS
Tariff Helper - Document Assistant (RAG) 2025
"""


@pytest.fixture(scope="module")
def store():
    return load_store(sample())


@pytest.fixture(scope="module")
def index():
    return load_index(sample())


@pytest.fixture(scope="module")
def ai_resume(store, index):
    """The AI-platform variant. Its entries leave out the early-tenure entry
    that starts in April 2022, which is what exposed the date bug."""
    wanted = {"Python", "LLM", "RAG", "Agentic", "MCP", "Prompt Engineering"}
    entries = select_entries(store, index, wanted, max_work=4)
    skills = surfaced_skills(entries, index, wanted)
    v = Variant(id="ai-platform", angle="ai-platform", emits=[e["id"] for e in entries],
                surfaces=skills, title_descriptor="Backend AI Engineer")
    return v, entries, skills


def _renders(store, index, v, entries, skills):
    idn = store["identity"]
    return {
        "text": render_text(idn, v, entries, skills, index),
        "html": strip_html(render_html(idn, v, entries, skills, index)),
        "latex": latex_to_text(render_latex(idn, v, entries, skills, index)),
    }


def test_the_old_layout_fails_all_three_new_checks(store):
    """The checks, run on what Workday read, must fail for each thing it got wrong."""
    work = [{"id": "x", "type": "work", "where": "Acme Freight", "period": "2022-present"}]
    bad = {c["check"]: c for c in check_text(WORKDAY_READ, store["identity"], work, [])["checks"]}
    assert not bad["job title in experience"]["ok"]
    assert not bad["one dated line per job"]["ok"]            # 4 ranges for 1 employer
    assert not bad["start month (Acme Freight)"]["ok"]


def test_the_early_tenure_entry_really_is_left_out(ai_resume):
    """Otherwise the date test below proves nothing."""
    _, entries, _ = ai_resume
    assert "acme-early-tenure" not in {e["id"] for e in entries}


@pytest.mark.parametrize("fmt", ["text", "html", "latex"])
def test_every_format_passes_the_ats_checks(store, index, ai_resume, fmt):
    v, entries, skills = ai_resume
    text = _renders(store, index, v, entries, skills)[fmt]
    res = check_text(text, store["identity"], entries, skills)
    assert res["ok"], [c for c in res["checks"] if not c["ok"]]


@pytest.mark.parametrize("fmt", ["text", "html", "latex"])
def test_one_dated_line_and_it_has_the_month(store, index, ai_resume, fmt):
    v, entries, skills = ai_resume
    exp = experience_section(_renders(store, index, v, entries, skills)[fmt])
    ranges = _RANGE.findall(exp)
    assert len(ranges) == 1, ranges
    assert ranges[0].startswith("Apr 2022"), ranges[0]


def test_the_title_sits_on_the_dated_line(store, index, ai_resume):
    v, entries, skills = ai_resume
    exp = experience_section(_renders(store, index, v, entries, skills)["text"])
    dated = next(line for line in exp.splitlines() if _RANGE.search(line))
    assert "Software Engineer II (Backend AI Engineer)" in dated
    assert "Checker" not in dated


def test_ai_work_says_since_2025_without_a_range(store, index, ai_resume):
    """The AI work began in 2025. Shown to a reader as "since 2025" - a single
    year, which no parser takes for a job."""
    v, entries, skills = ai_resume
    text = _renders(store, index, v, entries, skills)["text"]
    assert "· since 2025" in text
    assert "2026 - Present" not in text


def test_no_resume_says_extractive_qa(store, index):
    """"QA" alone reads as Quality Assurance - Workday suggested it as a skill."""
    wanted = {"Python", "RAG", "Fine-tuning"}
    entries = select_entries(store, index, wanted)
    skills = surfaced_skills(entries, index, wanted)
    v = Variant(id="ml", angle="ml", emits=[e["id"] for e in entries], surfaces=skills)
    text = _renders(store, index, v, entries, skills)["text"]
    assert "extractive QA" not in text and "QA extractor" not in text


# ── the title and languages the person confirmed ─────────────────────────────

def test_one_title_since_day_one(store):
    """The position line's title is the one on record, never a different one
    an older resume used."""
    job = store["identity"]["employment"][0]
    assert job["title"] == store["identity"]["title"]["official"] == "Software Engineer II"


@pytest.mark.parametrize("fmt", ["text", "html", "latex"])
def test_no_title_off_the_record_reaches_a_resume(store, index, ai_resume, fmt):
    """The position line's title is the one on record - never one an older
    resume used, or one a descriptor list doesn't hold."""
    v, entries, skills = ai_resume
    text = _renders(store, index, v, entries, skills)[fmt]
    assert "Senior Software Engineer" not in text and "Lead Engineer" not in text


@pytest.mark.parametrize("fmt", ["text", "html", "latex"])
def test_languages_are_listed_as_given_and_nothing_more(store, index, ai_resume, fmt):
    """The person's order, and no proficiency - none was given, so none is invented."""
    v, entries, skills = ai_resume
    text = _renders(store, index, v, entries, skills)[fmt]
    assert "Marathi, Hindi, English" in " ".join(text.split())
    for guess in ("Native", "Fluent", "Proficient", "Professional", "Basic"):
        assert guess not in text


def test_the_languages_section_does_not_swallow_experience(store, index, ai_resume):
    v, entries, skills = ai_resume
    exp = experience_section(_renders(store, index, v, entries, skills)["text"])
    assert "Marathi" not in exp and "Software Engineer II" in exp
