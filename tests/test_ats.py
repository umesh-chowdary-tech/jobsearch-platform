"""The ATS match: what the assembler optimizes, so it has to be right first."""
from __future__ import annotations

from pathlib import Path

import pytest

import jobsearch.ats as ats
from jobsearch.assemble import load_store, select_entries
from jobsearch.ats import (
    PostingKeywords,
    Scorer,
    best_descriptor,
    keywords_for,
    posting_keywords,
    role_words,
    score_terms,
)
from jobsearch.skills import load_index
from samples import sample

JAVA = {
    "title": "Software Engineer II - Java Full Stack Developer + React + AWS",
    "description": "Work in an agile team. Some Python scripting.",
    "requirements": ("Required qualifications: Experience in React, Spring Framework and "
                     "SpringBoot. Experience with Kafka. Preferred qualifications: Docker."),
}


@pytest.fixture(scope="module")
def index():
    return load_index(sample())


@pytest.fixture(scope="module")
def store():
    return load_store(sample())


# ── the posting side ─────────────────────────────────────────────────────────

def test_where_the_posting_names_a_skill_sets_its_weight(index):
    kw = posting_keywords(JAVA, index)
    assert kw.weights["Java"] == ats.IN_TITLE           # in the title
    assert kw.weights["Spring Boot"] == ats.IN_REQUIRED  # under required
    assert kw.weights["Docker"] == ats.IN_PREFERRED      # under preferred
    assert kw.weights["Python"] == ats.IN_BODY           # only in passing


def test_the_title_keeps_its_role_words_not_its_skills_or_level(index):
    assert role_words(JAVA["title"], index) == {"software", "engineer", "full", "stack", "developer"}


def test_an_unbacked_ask_counts_against_every_page_but_earns_nothing(index):
    kw = posting_keywords(JAVA, index)
    assert "Kafka" in kw.weights and "Kafka" not in kw.creditable
    # Even if something did print it, it would not score.
    m = score_terms(kw, [("experience", {"Kafka"}), ("skills", {"Kafka"})], "")
    assert m.match == 0 and "Kafka" in m.unclaimable


def test_an_incidental_skill_a_fact_names_earns_nothing(index):
    """The portal-migration bullet names Azure Blob Storage - allowed, and true.
    Rewarding the optimizer for it would be claiming Azure by the back door."""
    kw = keywords_for({"Azure", "React"}, index)
    m = score_terms(kw, [("experience", {"Azure", "React"})], "")
    assert "Azure" not in m.found and "React" in m.found


# ── the resume side ──────────────────────────────────────────────────────────

def test_experience_outscores_a_bare_skills_line(index):
    kw = keywords_for({"Java"}, index)
    body = score_terms(kw, [("experience", {"Java"})], "")
    listed = score_terms(kw, [("skills", {"Java"})], "")
    assert body.match > listed.match > 0
    assert listed.to_dict()["skills_only"] == ["Java"]


def test_repeat_mentions_saturate(index):
    kw = keywords_for({"React"}, index)
    three = score_terms(kw, [("experience", {"React"})] * 3, "")
    five = score_terms(kw, [("experience", {"React"})] * 5, "")
    assert three.depth == five.depth > 0
    assert three.match == five.match


def test_the_descriptor_is_the_approved_one_the_title_matches(index):
    approved = ["Backend AI Engineer", "Backend Software Engineer", "AI Platform Engineer"]
    assert best_descriptor(approved, posting_keywords(JAVA, index)) == "Backend Software Engineer"
    ai = posting_keywords({"title": "Senior AI Engineer - Python, RAG"}, index)
    assert best_descriptor(approved, ai) in ("Backend AI Engineer", "AI Platform Engineer")


def test_nothing_approved_means_no_descriptor(index):
    """Never a coined one: with none approved, the official title stands alone."""
    assert best_descriptor([], posting_keywords(JAVA, index)) == ""


def test_a_stack_line_counts_as_experience(store, index):
    """The portal-migration bullets say Spring Boot but never Java; the entry's
    `tech` backs Java, so the stack line under it puts Java in the body."""
    leave = next(e for e in store["entries"] if e["id"] == "acme-portal-migration")
    kw = keywords_for({"Java"}, index)
    sc = Scorer(kw, index)
    assert sc.score([leave], ["Java"], "").found["Java"] == "experience"


# ── the choices are the mechanism's, not the constants' ─────────────────────

@pytest.mark.parametrize("change", [
    {"skills": 0.3}, {"skills": 0.7}, {"projects": 0.6}, {"projects": 0.95},
    {"summary": 0.5, "other": 0.5},
])
def test_the_java_choices_survive_moving_the_credits(store, index, change, monkeypatch):
    """CREDIT is a judgement. If the choices held only at one exact setting,
    the constants would be doing the work instead of the match."""
    approved = store["identity"]["title"]["approved_descriptors"]
    kw = posting_keywords(JAVA, index)
    monkeypatch.setattr(ats, "CREDIT", {**ats.CREDIT, **change})
    ids = [e["id"] for e in select_entries(store, index, kw)]
    assert "acme-portal-migration" in ids
    assert best_descriptor(approved, kw) == "Backend Software Engineer"
