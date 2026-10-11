"""Assembly tests. The highest-stakes module: this is what goes to an employer."""
from __future__ import annotations

import pytest

from jobsearch.assemble import (
    Variant,
    build,
    emittable,
    library_path,
    load_library,
    load_store,
    select_entries,
    summary_is_grounded,
    surfaced_skills,
    unique_id,
)
from jobsearch.render import (
    bullets,
    chronological,
    parse_check,
    posting_spelling,
    render_html,
    render_text,
    strip_html,
)
from jobsearch.skills import BANNED, INCIDENTAL, load_index
from jobsearch.profile import Profile
from samples import SAMPLE, make_root, sample


@pytest.fixture(scope="module")
def store():
    return load_store(sample())


@pytest.fixture(scope="module")
def index():
    return load_index(sample())


@pytest.fixture(scope="module")
def identity(store):
    return store["identity"]


@pytest.fixture(scope="module")
def built(store, index, identity):
    wanted = {"Python", "PostgreSQL", "AWS", "Docker"}
    entries = select_entries(store, index, wanted)
    skills = surfaced_skills(entries, index, wanted)
    v = Variant(id="backend", angle="backend", emits=[e["id"] for e in entries],
                surfaces=skills)
    return v, entries, skills


# ── what may be emitted at all ───────────────────────────────────────────────

def test_low_confidence_and_parked_entries_never_emit(store):
    for e in store["entries"]:
        if e.get("confidence") == "low" or e.get("status") == "parked":
            assert not emittable(e)


def test_selection_excludes_them(store, index):
    chosen = select_entries(store, index, {"Python"})
    assert all(emittable(e) for e in chosen)


def test_selection_respects_the_caps(store, index):
    chosen = select_entries(store, index, {"Python"},
                            max_work=3, max_projects=2)
    assert len([e for e in chosen if e["type"] == "work"]) <= 3
    assert len([e for e in chosen if e["type"] == "project"]) <= 2


# ── the skills line ──────────────────────────────────────────────────────────

def test_only_backed_skills_are_surfaced(store, index, built):
    _, _, skills = built
    for s in skills:
        assert index.tier(s) in ("core", "working", "scoped")


def test_an_incidental_skill_can_never_reach_the_page(store, index, built):
    """Azure is `incidental`. The assembler has no path to name it - not a
    prompt telling it not to, no path."""
    _, _, skills = built
    assert "Azure" not in skills
    assert not any(index.tier(s) == INCIDENTAL for s in skills)


def test_a_banned_skill_can_never_reach_the_page(store, index, built):
    _, _, skills = built
    for dead in ("LangChain", "LlamaIndex", "Kafka", "Angular", "Tomcat"):
        assert dead not in skills
    assert not any(index.tier(s) == BANNED for s in skills)


def test_posting_relevant_skills_come_first(store, index):
    entries = select_entries(store, index, {"Docker"})
    skills = surfaced_skills(entries, index, {"Docker"})
    assert "Docker" in skills[:6]


def test_the_skills_list_is_capped(store, index):
    """19 technologies. Ways of working sit outside the cap - counted in, they
    pushed TypeScript and Docker off a full-stack resume."""
    from jobsearch.resume_layout import _is_method
    entries = select_entries(store, index, {"Python"})
    skills = surfaced_skills(entries, index, {"Python"})
    assert len([s for s in skills if not _is_method(s, index)]) <= 19
    assert {"Claude Code", "GitHub Copilot", "TypeScript"} <= set(skills)


# ── the library: reuse only what nothing fresh beats ─────────────────────────

JAVA = {
    "id": "t-java", "company": "Bank", "title": "Software Engineer II - Java Full Stack Developer + React + AWS",
    "description": "Design, code and test applications in an agile team.",
    "requirements": ("Required qualifications: full stack development with a focus on Java. "
                     "Experience in React, Spring Framework and SpringBoot. Experience on Cloud - AWS. "
                     "APIs and messaging like Kafka. Experience building agentic AI applications (LLM-based "
                     "agents) including prompt engineering. Preferred qualifications: Angular."),
}
AI = {
    "id": "t-ai", "company": "Lab", "title": "Senior AI Engineer - Python, RAG, LLM",
    "description": "Build retrieval systems.",
    "requirements": ("Required: Python, RAG, LLM, prompt engineering, agentic workflows, MCP. "
                     "Nice to have: PyTorch fine-tuning."),
}


def _prof(tmp_path):
    """The sample profile in a throwaway root, made on first use."""
    root = tmp_path / "root"
    return Profile(root, SAMPLE) if root.exists() else make_root(root)


def _build(tmp_path, store, index, posting):
    return build(_prof(tmp_path), store, index, posting, use_model=False)


def test_the_same_posting_twice_reuses_its_variant(tmp_path, store, index):
    first, _, _ = _build(tmp_path, store, index, JAVA)
    again, _, why = _build(tmp_path, store, index, JAVA)
    assert again.id == first.id and why.startswith("reuse")
    assert len(load_library(_prof(tmp_path))) == 1


def test_an_equally_good_existing_variant_is_reused(tmp_path, store, index):
    _build(tmp_path, store, index, JAVA)
    twin = dict(JAVA, id="t-java-2", company="Other bank")
    v, _, why = _build(tmp_path, store, index, twin)
    assert why.startswith("reuse") and len(load_library(_prof(tmp_path))) == 1
    assert [s["posting_id"] for s in v.sent_to] == ["t-java", "t-java-2"]


def test_a_posting_a_fresh_page_serves_better_gets_a_new_variant(tmp_path, store, index):
    """If a new variant outsmarts the existing ones, it is always written. An
    AI resume is not reused for a Java posting."""
    ai, _, _ = _build(tmp_path, store, index, AI)
    java, _, why = _build(tmp_path, store, index, JAVA)
    assert why.startswith("new variant") and ai.id in why
    assert java.id != ai.id and len(load_library(_prof(tmp_path))) == 2


def test_a_sent_variant_is_never_edited(tmp_path, store, index):
    """Reuse used to rewrite a variant's entries in place, so the record of
    what went to one employer stopped being what went to them."""
    ai, _, _ = _build(tmp_path, store, index, AI)
    before = (list(ai.emits), list(ai.surfaces), ai.title_descriptor)
    _build(tmp_path, store, index, JAVA)
    _build(tmp_path, store, index, dict(AI, id="t-ai-2"))
    kept = next(v for v in load_library(_prof(tmp_path)) if v.id == ai.id)
    assert (kept.emits, kept.surfaces, kept.title_descriptor) == before


def test_every_send_records_its_match(tmp_path, store, index):
    v, _, _ = _build(tmp_path, store, index, JAVA)
    ats = v.sent_to[-1]["ats"]
    assert 0 < ats["match"] <= ats["ceiling"] <= 1
    assert "Kafka" in ats["unclaimable"] and "Angular" in ats["unclaimable"]


def test_duplicate_ids_are_split_on_load(tmp_path):
    """Two variants were both called "ai-platform", so one overwrote the other's PDF."""
    lib = [Variant(id="ai-platform", angle="ai-platform").to_dict(),
           Variant(id="ai-platform", angle="ai-platform").to_dict()]
    prof = _prof(tmp_path)
    library_path(prof).write_text(__import__("json").dumps(lib), encoding="utf-8")
    assert [v.id for v in load_library(prof)] == ["ai-platform", "ai-platform-2"]


def test_unique_id():
    assert unique_id("java-react-aws", []) == "java-react-aws"
    assert unique_id("java-react-aws", ["java-react-aws", "java-react-aws-2"]) == "java-react-aws-3"


# ── what the match changes on the page ───────────────────────────────────────

def _page(tmp_path, store, index, identity, posting):
    v, entries, _ = _build(tmp_path, store, index, posting)
    return v, entries, render_text(identity, v, entries, v.surfaces, index)


def test_a_java_posting_gets_java_in_its_experience(tmp_path, store, index, identity):
    """The build that started this: the title asked for Java and Spring Boot,
    and the resume named neither."""
    from jobsearch.render import experience_section
    v, entries, text = _page(tmp_path, store, index, identity, JAVA)
    exp = experience_section(text)
    assert "Java" in exp.replace("JavaScript", "") and "Spring Boot" in exp
    assert v.title_descriptor == "Backend Software Engineer"


def test_practices_print_once_per_job_not_under_every_workstream(tmp_path, store, index, identity):
    """Under each stack line, "Agile, Code Review" repeated six times and
    pushed TypeScript and PostgreSQL off a workstream's line."""
    _, _, text = _page(tmp_path, store, index, identity, JAVA)
    practices = [l for l in text.splitlines() if l.strip().startswith("Practices:")]
    stacks = [l for l in text.splitlines() if l.strip().startswith("Stack:")]
    assert len(practices) == 1 and "Agile" in practices[0]
    assert not any(w in l for l in stacks for w in ("Agile", "Code Review", "Debugging"))


def test_ai_coding_tools_are_on_every_resume(tmp_path, store, index, identity):
    """Pinned in skills.yaml `always`, so even a posting that never asks gets
    them - once, on the Practices line, never crowding a workstream's stack."""
    plain = {"id": "t-plain", "title": "Backend Engineer", "description": "Python and PostgreSQL."}
    _, _, text = _page(tmp_path, store, index, identity, plain)
    lines = text.splitlines()
    practices = next(l for l in lines if l.strip().startswith("Practices:"))
    for tool in ("AI-assisted Development", "Claude Code", "GitHub Copilot"):
        assert tool in practices
    assert not any("Copilot" in l or "Claude Code" in l for l in lines if l.strip().startswith("Stack:"))


def test_a_stored_variant_without_the_pins_is_not_reused(tmp_path, store, index):
    """On a posting that never mentions them, a resume without the coding
    tools scores the same as one with them - so the tie would reuse it."""
    plain = {"id": "t-plain", "title": "Backend Engineer", "description": "Python and PostgreSQL."}
    old, _, _ = _build(tmp_path, store, index, plain)
    lib = load_library(_prof(tmp_path))
    lib[0].surfaces = [s for s in lib[0].surfaces if s not in index.always]
    from jobsearch.assemble import save_library
    save_library(_prof(tmp_path), lib)
    v, _, why = _build(tmp_path, store, index, dict(plain, id="t-plain-2"))
    assert why.startswith("new variant") and v.id != old.id
    assert set(index.always) <= set(v.surfaces)


def test_copilot_is_claimed_only_where_the_store_says(store, index):
    backers = {e["id"] for e in store["entries"]
               if "GitHub Copilot" in {c.skill for c in index.emittable_for(e.get("tech") or [])}}
    assert backers == {"acme-agent-platform", "acme-track"}


def test_the_postings_phrase_for_coding_assistants_is_understood(index):
    """One posting: "enterprise-authorized AI-assisted software development tools"."""
    assert "AI-assisted Development" in index.detect(
        "Hands-on experience using AI-assisted software development tools")


def test_an_ai_posting_still_gets_an_ai_resume(tmp_path, store, index, identity):
    v, entries, text = _page(tmp_path, store, index, identity, AI)
    assert v.title_descriptor != "Backend Software Engineer"
    assert "acme-portal-migration" not in [e["id"] for e in entries]


def test_a_low_priority_entry_appears_only_when_it_carries_something(store, index):
    """"Cut this first" loses every tie. The portal migration is the only
    Spring Boot on record, so it is no longer a tie on a Spring posting."""
    ids = lambda wanted: [e["id"] for e in select_entries(store, index, wanted)]
    assert "acme-portal-migration" in ids({"Spring Boot", "React"})
    assert "acme-portal-migration" not in ids({"Python", "LLM", "RAG"})


def test_nothing_unclaimable_reaches_the_page(tmp_path, store, index, identity):
    """The match counts Kafka and Angular against every candidate equally.
    No selection can add them, so none does."""
    v, _, text = _page(tmp_path, store, index, identity, JAVA)
    for dead in ("Kafka", "Angular", "Azure"):
        assert dead not in v.surfaces
    assert "Kafka" not in text and "Angular" not in text


# ── the summary guardrail ────────────────────────────────────────────────────

FACTS = ["adopted by 30+ operations analysts",
         "took it from prototype to production in under 4 months"]


def test_an_invented_number_is_rejected():
    """A model asked for a resume summary reaches for "improved performance by
    40%" because that is what resume summaries sound like. That is the exact
    invented-percentage failure the store was built to end."""
    ok, why = summary_is_grounded("Improved performance by 40% across the org.", FACTS)
    assert not ok and "40" in why


def test_a_number_with_the_wrong_unit_is_rejected():
    """Digits alone passed this: "4" is in the facts, as "under 4 months".
    Same digit, different unit - an invented claim."""
    ok, why = summary_is_grounded("Engineer with 4 years of experience shipping AI platforms.", FACTS)
    assert not ok and "4 years" in why


def test_a_count_does_not_ground_a_percentage():
    ok, _ = summary_is_grounded("Automated 40% of the release checks.",
                                ["automated 40 of the release checks"])
    assert not ok


def test_a_spelled_out_number_is_matched_as_a_word():
    """"ten" is inside "content". Matched as a substring, "ten years" passed
    against the real store."""
    ok, _ = summary_is_grounded("Ten years of backend experience.",
                                ["kept the content pipeline current"])
    assert not ok


def test_a_grounded_number_is_accepted():
    ok, _ = summary_is_grounded("Took a platform from prototype to production in 4 months.", FACTS)
    assert ok


def test_a_summary_with_no_numbers_is_fine():
    ok, _ = summary_is_grounded("Architected an internal AI platform.", FACTS)
    assert ok


def test_an_empty_summary_is_rejected():
    assert not summary_is_grounded("", FACTS)[0]


def test_an_overlong_summary_is_rejected():
    assert not summary_is_grounded("word " * 80, FACTS)[0]


# ── bullets ──────────────────────────────────────────────────────────────────

def test_a_metric_that_restates_a_fact_is_not_printed_twice():
    """The store holds both, worded differently. Printing both put the same
    claim on the page twice, which reads as padding - the opposite of what a
    measured number is for."""
    e = {
        "facts": ["used by ~3,000 business customers; handles ~2M shipment events a day"],
        "metrics": [{"measured": True,
                     "claim": "~3,000 business customers use it; it handles ~2M shipment events a day"}],
    }
    assert len(bullets(e)) == 1


def test_a_metric_that_adds_something_is_printed():
    e = {"facts": ["adopted by 30+ analysts"],
         "metrics": [{"measured": True, "claim": "report preparation ~2 days to ~2 hours"}]}
    assert len(bullets(e)) == 2


def test_an_unmeasured_metric_never_prints():
    e = {"facts": ["did a thing"], "metrics": [{"measured": False, "claim": "40% faster"}]}
    assert bullets(e) == ["did a thing"]


# ── ordering ─────────────────────────────────────────────────────────────────

def test_entries_render_newest_first():
    """Relevance decides WHICH entries appear; it must not decide the order, or
    the reader sees 2023 above 2026 and stops trusting the dates."""
    es = [{"period": "2023-present"}, {"period": "2026-present"}, {"period": "2019-2023"}]
    assert [e["period"] for e in chronological(es)][0] == "2026-present"


def test_an_entry_with_no_period_sinks_rather_than_crashing():
    es = [{"period": None}, {"period": "2026"}]
    assert chronological(es)[0]["period"] == "2026"


# ── vocabulary ───────────────────────────────────────────────────────────────

def test_the_postings_spelling_wins_when_it_means_the_same(index):
    assert posting_spelling("Node.js", index, "We use NodeJS here") == "NodeJS"


def test_the_canonical_spelling_is_kept_otherwise(index):
    assert posting_spelling("Node.js", index, "nothing relevant") == "Node.js"


def test_lowercase_prose_is_not_a_spelling(index):
    """One posting's "member of an agile team" put "agile" on the Skills line."""
    assert posting_spelling("Agile", index, "a member of an agile team") == "Agile"


def test_confirmed_practices_are_claimable_only_where_the_store_says(store, index):
    """System Design, Code Review, Agile, Debugging, NLP, Git, and unit testing
    with Jest. Claimable, and backed only by the entries they belong to - unit
    tests are the Jest ones, so only Acme Track."""
    for s in ("System Design", "Code Review", "Agile", "Debugging", "NLP", "Git", "Unit Testing"):
        assert index.tier(s) in ("core", "working", "scoped"), s
    backers = lambda s: {e["id"] for e in store["entries"]
                         if s in {c.skill for c in index.emittable_for(e.get("tech") or [])}}
    assert backers("Unit Testing") == backers("Jest") == {"acme-track"}
    assert backers("NLP") == {"proj-llm-finetune", "proj-rag-assistant", "proj-extraction-models"}


# ── parse safety ─────────────────────────────────────────────────────────────

def test_a_rendered_resume_survives_extraction(identity, built):
    v, entries, skills = built
    doc = render_html(identity, v, entries, skills)
    res = parse_check(doc, identity, entries, skills)
    assert res["ok"], [c for c in res["checks"] if not c["ok"]]


def test_the_name_and_contact_survive(identity, built):
    v, entries, skills = built
    text = strip_html(render_html(identity, v, entries, skills))
    assert identity["name"] in text
    assert identity["email"] in text
    assert identity["phone"].replace(" ", "") in text.replace(" ", "")


def test_every_employment_date_survives(identity, built):
    v, entries, skills = built
    text = " ".join(strip_html(render_html(identity, v, entries, skills)).split())
    import re
    for e in entries:
        if not e.get("period"):
            continue
        years = re.findall(r"(?:19|20)\d{2}", str(e["period"]))
        # The year, not the store's raw string - the renderer reformats dates
        # for a human reader and an ATS parses years.
        if years:
            assert any(y in text for y in years), e["id"]


def test_a_missing_skill_fails_the_check(identity, built):
    """The check has to be capable of failing, or it is decoration."""
    v, entries, skills = built
    doc = render_html(identity, v, entries, skills)
    res = parse_check(doc, identity, entries, skills + ["Kubernetes"])
    assert not res["ok"]
    assert any(c["check"] == "skills" and not c["ok"] for c in res["checks"])


def test_a_table_fails_the_check(identity, built):
    """A parser meeting a table is where fields start disappearing."""
    v, entries, skills = built
    doc = render_html(identity, v, entries, skills) + "<table><tr><td>x</td></tr></table>"
    res = parse_check(doc, identity, entries, skills)
    assert not res["ok"]


def test_the_text_and_html_agree(identity, built):
    v, entries, skills = built
    text = render_text(identity, v, entries, skills)
    from_html = strip_html(render_html(identity, v, entries, skills))
    for s in skills:
        assert s in text and s in from_html


def test_no_invented_percentage_reaches_the_page(identity, built):
    """The kind of figures an old resume invents. None was ever measured, and
    none is in the store - so none can be rendered."""
    v, entries, skills = built
    text = render_text(identity, v, entries, skills)
    for ghost in ("20% increase", "15% reduction", "95% of", "25% reduction",
                  "40% improvement", "30% faster", "10M users"):
        assert ghost not in text
