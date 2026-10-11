"""The Apply kit. It tells the person what to type into a real application,
so the rule is the store's rule: nothing in it they cannot defend."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from jobsearch.apply_kit import build_kit
from jobsearch.assemble import Variant, emittable, load_store
from jobsearch.skills import BANNED, CORE, INCIDENTAL, SCOPED, WORKING, load_index
from samples import make_root, sample

ROOT = Path(__file__).resolve().parents[1]
OK = (CORE, WORKING, SCOPED)


@pytest.fixture(scope="module")
def index():
    return load_index(sample())


@pytest.fixture
def root(tmp_path):
    prof = make_root(tmp_path)
    var = prof.var
    # A posting that asks for backed skills AND for things the person must not claim.
    posting = {"id": "wf-1", "title": "Lead Software Engineer - Python/Gen AI/RAG ",
               "company": "gbx", "board": "workday:gbx", "verdict": "keep",
               "matched": {"Python": "core", "RAG": "working", "LLM": "core",
                           "Kafka": "banned", "Azure": "incidental"},
               "required_gaps": ["Kubernetes", "Flask", "Python"]}
    (var / "triage-results.json").write_text(json.dumps({"postings": {"wf-1": posting}}))
    store = load_store(prof)
    lib = [Variant(id="ai-platform", angle="ai-platform",
                   emits=[e["id"] for e in store["entries"] if emittable(e)],
                   surfaces=["TypeScript", "LangChain", "Node.js"],
                   sent_to=[{"posting_id": "wf-1", "date": "2026-09-25"}]).to_dict()]
    (var / "resume-variants.json").write_text(json.dumps(lib))
    return prof


def test_nothing_to_add_that_the_store_does_not_back(root, index):
    """Kafka (banned) and Azure (incidental) are in the posting's matched set,
    LangChain (banned) is in the variant's surfaces - none may reach the kit."""
    k = build_kit(root, "wf-1")
    added = {s["skill"] for s in k["skills_add"]}
    assert all(index.tier(s) in OK for s in added), added
    assert not added & {"Kafka", "Azure", "LangChain"}


def test_what_the_posting_asks_for_comes_first(root):
    k = build_kit(root, "wf-1")
    whys = [s["why"] for s in k["skills_add"]]
    asked = [i for i, w in enumerate(whys) if w == "this posting asks for it"]
    assert asked == list(range(len(asked))) and len(asked) == 3


def test_seen_names_are_the_forms_own_words(root):
    k = build_kit(root, "wf-1")
    py = next(s for s in k["skills_add"] if s["skill"] == "Python")
    assert py["workday"] == "Python (Programming Language)" and py["seen"]
    assert next(s for s in k["skills_add"] if s["skill"] == "RAG")["seen"] is None   # a guess, and says so


def test_bad_suggestions_and_everything_banned_come_off(root, index):
    k = build_kit(root, "wf-1")
    remove = {s["workday"] for s in k["skills_remove"]}
    assert {"Database Administration", "Quality Assurance (QA)", "DevOps"} <= remove   # the profile's own list
    banned = {c.skill for c in index.claims.values() if c.tier in (BANNED, INCIDENTAL)}
    assert banned <= remove


def test_dont_add_lists_only_what_is_not_backed(root):
    assert build_kit(root, "wf-1")["not_backed"] == ["Kubernetes", "Flask"]


def test_experience_fields_fix_what_workday_parsed_wrong(root):
    """Workday read Job Title "Checker" and January of the start year."""
    (job,) = build_kit(root, "wf-1")["experience"]
    assert job == {"title": "Software Engineer II", "company": "Acme Freight", "location": "Pune, India",
                   "from": "04/2022", "from_words": "Apr 2022", "current": True}


def test_certifications_are_one_row_each(root):
    certs = build_kit(root, "wf-1")["certifications"]
    assert {"name": "AWS Certified Developer - Associate", "issuer": "AWS", "year": "2024"} in certs
    courses = [c for c in certs if c["issuer"] == "Example Academy"]
    assert [(c["name"], c["year"]) for c in courses] == [
        ("Example Academy - Data Structures", "2021"), ("Example Academy - Databases", "2022"),
        ("Example Academy - Cloud Computing", "2022")]


def test_education_and_languages_are_exactly_the_store(root):
    k = build_kit(root, "wf-1")
    assert k["education"] == [{"school": "Example Institute of Technology", "degree": "B.E.",
                               "field": "Information Technology", "from": "08/2018",
                               "to": "05/2022", "grade": "7.8"}]
    assert k["languages"] == ["Marathi", "Hindi", "English"]


# ── the names files ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def names():
    from jobsearch.apply_kit import _names
    return _names(sample())


def test_the_shared_names_claim_nothing_for_anyone():
    """data/ats-names.yaml is every person's: Workday's words only. What to
    remove or keep depends on the person, so it lives in their profile."""
    shared = yaml.safe_load((ROOT / "data" / "ats-names.yaml").read_text(encoding="utf-8"))
    assert set(shared) == {"workday"} and all(r.get("name") for r in shared["workday"].values())


def test_a_name_the_store_does_not_back_never_reaches_the_kit(root, names, index):
    unbacked = [k for k in names["workday"] if index.tier(index.canonical(k) or k) not in OK]
    added = {s["skill"] for s in build_kit(root, "wf-1")["skills_add"]}
    assert not set(unbacked) & added


def test_nothing_is_both_kept_and_removed(names):
    keep = {r["name"] for r in names["workday_keep"]}
    remove = {r["name"] for r in names["workday_remove"]}
    add = {r["name"] for r in names["workday"].values()}
    assert not keep & remove and not add & remove


def test_every_removal_says_why(names):
    assert all(r.get("why") for r in names["workday_remove"] + names["workday_keep"])
