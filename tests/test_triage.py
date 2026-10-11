"""Triage tests. The sample profile's skills.yaml, the shared vocabulary, synthetic postings."""
from __future__ import annotations

import argparse
import copy
import json
import socket

import pytest

from jobsearch import triage_cli
from jobsearch.dashboard import render
from jobsearch.skills import CORE, SCOPED, load_index
from jobsearch.triage import (
    EXCERPT_CHARS,
    FLAG,
    KEEP,
    NO_MATCH,
    REJECT,
    Coverage,
    Triaged,
    function_verdict,
    load_profile,
    location_verdict,
    recheck_record,
    recheck_results,
    required_years,
    score_posting,
    seniority_verdict,
    split_requirements,
    sync_closed,
    triage_posting,
    update_gap_ledger,
    write_results,
)
from jobsearch.watch import BoardWatch, WatchRun, posting_live, save_seen, seen_path
from samples import make_root, sample



@pytest.fixture(scope="module")
def index():
    return load_index(sample())


@pytest.fixture(scope="module")
def profile():
    return load_profile(sample().dir)


def posting(**kw):
    base = {"title": "Software Engineer", "location": "Pune, India",
            "description": "", "company": "acme", "id": "1",
            "board": "lever:acme", "first_seen": "2026-09-23T00:00:00+00:00"}
    base.update(kw)
    return base


# ── requirement splitting ────────────────────────────────────────────────────

def test_nice_to_have_is_split_off():
    req, nice = split_requirements("Requirements: Python.\nNice to have: Kubernetes.")
    assert "Python" in req and "Kubernetes" not in req
    assert "Kubernetes" in nice


def test_no_heading_means_everything_is_required():
    """The conservative reading. Treating unlabelled text as optional would
    under-count the gaps that actually block an application."""
    req, nice = split_requirements("We want Python and Go.")
    assert "Python" in req and nice == ""


@pytest.mark.parametrize("heading", ["Bonus points", "Preferred qualifications",
                                     "Good to have", "It's a plus"])
def test_common_bonus_headings(heading):
    req, nice = split_requirements(f"Must: Java.\n{heading}: Rust.")
    assert "Rust" in nice and "Rust" not in req


# ── years of experience ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("5+ years of experience", 5),
    ("3-5 years experience required", 3),
    ("minimum 8 years of relevant experience", 8),
    ("2 yrs experience", 2),
    ("no numbers here", None),
    ("100 years of experience", None),      # nonsense, ignored
])
def test_required_years(text, expected):
    assert required_years(text) == expected


def test_the_lowest_stated_minimum_wins():
    """Postings often list several. The lowest is the one that decides whether
    applying is worth the time."""
    assert required_years("8+ years overall, 3+ years with Python experience") == 3


# ── hard filters ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("loc, ok", [
    ("Pune, Maharashtra", True),
    ("Bangalore, Karnataka", True),
    ("Remote - India", True),
    ("Santa Clara, CA", False),
    ("London, UK", False),
])
def test_location_filter(loc, ok, profile):
    assert location_verdict(loc, "", profile)[0] is ok


REMOTE_BODY = "This role is fully remote. Python, Java and Kubernetes."


@pytest.mark.parametrize("loc", [
    "Remote, Austria",                          # purestorage 8179372
    "Remote, Canada; Remote, United States",    # gitlab 8838202002
    "Remote - CA",                              # samsara 8223367
    "Remote - OR",                              # Oregon, not the connective
    "Remote in the US",
    "NYC or Remote",
    "Remote, Indiana",                          # not India
])
def test_remote_tied_to_a_place_outside_india_is_rejected(loc, profile):
    """The first three were keeps on the dashboard on 2026-09-24: any "remote"
    passed, before the place it was tied to was looked at. The body saying
    remote as well must not bring them back."""
    ok, why = location_verdict(loc, REMOTE_BODY, profile)
    assert not ok
    assert why.startswith("remote, but tied to a place")


@pytest.mark.parametrize("loc", [
    "Remote - India",
    "Remote",
    "India - Remote",
    "Remote, Bangalore",
    "Distributed",
    "Hybrid or Remote",
    # One of the places is India, so it suits.
    "Remote, India; Remote, Israel; Remote, Poland; Remote, United Kingdom",
])
def test_remote_from_india_or_from_nowhere_in_particular_passes(loc, profile):
    assert location_verdict(loc, "", profile)[0] is True
    assert location_verdict(loc, REMOTE_BODY, profile)[0] is True


def test_remote_in_the_body_does_not_rescue_a_named_foreign_place(profile):
    """This used to pass. A posting located in San Francisco that says "fully
    remote" means remote within the US - nearly every one does."""
    ok, _ = location_verdict("San Francisco", REMOTE_BODY, profile)
    assert not ok


@pytest.mark.parametrize("loc", ["", "Hybrid"])
def test_remote_in_the_body_counts_when_the_location_names_no_place(loc, profile):
    """The body is the only evidence then, and the conservative reading keeps it."""
    assert location_verdict(loc, REMOTE_BODY, profile) == (True, "remote (from the description)")
    assert location_verdict(loc, "Python and Java.", profile)[0] is False


def test_places_match_whole_words(profile):
    """"India" is a substring of "Indiana". This one passed as an acceptable
    location, no remote involved."""
    assert location_verdict("Chicago, Illinois; Indiana", "", profile)[0] is False


def test_the_regression_cases_through_the_whole_triage(index, profile):
    """End to end: with the location fixed, nothing later brings them back."""
    for loc in ("Remote, Austria", "Remote, Canada; Remote, United States", "Remote - CA"):
        t = triage_posting(index, profile, posting(location=loc, description=REMOTE_BODY))
        assert t.verdict == REJECT, loc
    for loc in ("Remote - India", "Remote"):
        t = triage_posting(index, profile, posting(location=loc, description=REMOTE_BODY))
        assert t.verdict != REJECT, loc


@pytest.mark.parametrize("title, verdict", [
    ("Staff Software Engineer", REJECT),
    ("Principal Engineer", REJECT),
    ("Engineering Manager", REJECT),
    ("Software Engineer II", KEEP),
    ("Senior Software Engineer", KEEP),
])
def test_seniority_by_title(title, verdict, profile):
    assert seniority_verdict(title, "", profile)[0] == verdict


def test_too_many_years_is_rejected(profile):
    assert seniority_verdict("Engineer", "9+ years of experience", profile)[0] == REJECT


def test_within_the_cap_is_kept(profile):
    assert seniority_verdict("Engineer", "4+ years of experience", profile)[0] == KEEP


@pytest.mark.parametrize("title", ["Account Executive, LATAM", "Sales Development Rep",
                                   "Senior Recruiter", "Data Operations Specialist"])
def test_non_engineering_titles_are_rejected(title, profile):
    assert function_verdict(title, profile)[0] is False


# ── scoring ──────────────────────────────────────────────────────────────────

def test_tiers_are_weighted_core_above_working(index, profile):
    core_only = score_posting(index, "We need Python.", profile)
    working_only = score_posting(index, "We need Node.js.", profile)
    assert core_only.score > working_only.score


def test_incidental_scores_zero(index, profile):
    """Azure is `incidental`. If naming it earned points, the whole skills layer
    would have been pointless."""
    cov = score_posting(index, "Experience with Azure required.", profile)
    assert cov.score == 0
    assert "Azure" in cov.shallow and "Azure" not in cov.matched


def test_an_unbacked_skill_is_an_absent_gap(index, profile):
    cov = score_posting(index, "Deep Kubernetes experience.", profile)
    assert "Kubernetes" in cov.absent and cov.score == 0


def test_a_scoped_skill_counts_but_stays_shallow(index, profile):
    cov = score_posting(index, "Write optimized Redshift queries.", profile)
    assert cov.matched.get("Redshift") == SCOPED
    assert "Redshift" in cov.shallow


def test_baseline_skills_are_neither_matched_nor_gaps(index, profile):
    """'Learn Git' is not a plan. These must not crowd the real gaps off the
    top of the ledger."""
    cov = score_posting(index, "Git, Agile, code review and Linux.", profile)
    assert cov.absent == set() and cov.matched == {}


def test_practice_terms_are_backed_after_the_store_correction(index, profile):
    """The first live gap ledger listed LLM and Agentic as 'learn from zero'
    for someone who architected a multi-agent LLM platform. The store named
    products and missed the word postings actually use."""
    cov = score_posting(index, "Build LLM and agentic systems.", profile)
    assert "LLM" in cov.matched and "Agentic" in cov.matched
    assert not ({"LLM", "Agentic"} & cov.absent)


# ── ambiguous words ──────────────────────────────────────────────────────────

def test_lowercase_spark_in_prose_is_not_apache_spark(index):
    """A false positive here does not just mis-score one posting - it puts a
    technology nobody asked for at the top of the gap ledger."""
    assert "Spark" not in index.detect("We want people who spark joy and spark change.")


def test_capitalised_spark_counts(index):
    assert "Spark" in index.detect("Experience with Spark and Hadoop.")


def test_an_unambiguous_alias_counts_regardless_of_case(index):
    assert "Spark" in index.detect("experience with pyspark pipelines")


def test_react_native_does_not_also_register_as_react(index):
    found = index.detect("Build mobile apps with React Native.")
    assert "React Native" in found and "React" not in found


# ── whole-posting verdicts ───────────────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "Cluster Head - Last Mile",
    "Senior Talent Partner (Non-Tech Hiring)",
    "Associate, Learning Business Partner",
])
def test_a_title_with_no_engineering_signal_is_rejected(title, index, profile):
    """An allowlist, because the blocklist was unwinnable. All three of these
    slipped through it, and the last one scored 10 on the strength of the AI
    tools its description happened to mention."""
    t = triage_posting(index, profile, posting(
        title=title, description="Work with Claude and Salesforce daily."))
    assert t.verdict == REJECT
    assert "no engineering signal" in t.reasons[-1]


@pytest.mark.parametrize("title", [
    "Business Analyst ",
    "Product Analyst II",
    "Senior Data Analyst",
    "Cluster Head  ( Security & Investigation)",
    "Senior Cloudflare One GTM Specialist",
    "EMEA Demand Generation, Platforms",
])
def test_a_function_or_domain_word_is_not_an_engineering_signal(title, index, profile):
    """Razorpay's "Business Analyst" was kept at score 11: 'analyst' was on the
    allowlist, and the description named SQL and Salesforce. 'security',
    'cloud' and 'platform' leaked the same way, the last two by substring."""
    t = triage_posting(index, profile, posting(
        title=title, description="SQL, Python and Salesforce. 3+ years experience."))
    assert t.verdict == REJECT
    assert "no engineering signal" in t.reasons[-1]


@pytest.mark.parametrize("title", [
    "Senior Security Engineer - Corporate Security",
    "Product Security Engineer II",
    "Cloud Network Engineer II",
    "Senior Platform Engineer, Growth Product Engineering",
    # "Data Scientist III" was here; the sample profile excludes data science
    # (see test_excluded_job_types_are_rejected).
])
def test_engineering_titles_in_those_domains_still_pass(title, profile):
    """The other half of the fix: dropping the domain words must not drop the
    engineering roles in those domains."""
    assert function_verdict(title, profile) == (True, "")


def test_an_engineering_title_naming_no_technology_is_still_rejected(index, profile):
    """The second net, for a title that passes the allowlist but has no
    technical content behind it."""
    t = triage_posting(index, profile, posting(
        title="Platform Engineer",
        description="Coordinate stakeholders and drive alignment across teams."))
    assert t.verdict == REJECT
    assert "not an engineering role" in t.reasons[-1]


def test_an_engineering_role_with_nothing_backed_is_no_match_not_reject(index, profile):
    """Kept apart from a hard-filter rejection because it is the single most
    informative case for the gap ledger."""
    t = triage_posting(index, profile, posting(
        title="Platform Engineer",
        description="Deep Kubernetes, Terraform and Elasticsearch experience."))
    assert t.verdict == NO_MATCH
    assert {"Kubernetes", "Terraform"} <= t.required_gaps


def test_a_good_match_is_kept_with_its_reasoning(index, profile):
    t = triage_posting(index, profile, posting(
        title="Backend Engineer",
        description="Python, PostgreSQL, AWS Lambda and Docker. 3+ years experience."))
    assert t.verdict == KEEP
    assert {"Python", "PostgreSQL"} <= set(t.coverage.matched)
    assert t.score > 0


# ── the gap ledger ───────────────────────────────────────────────────────────

def _triaged_with(gaps, verdict=NO_MATCH):
    from jobsearch.triage import Coverage
    t = Triaged(posting(), verdict, [], Coverage(named=set(gaps), absent=set(gaps)))
    t.required_gaps = set(gaps)
    return t


def test_gap_counts_accumulate_across_runs(tmp_path, index):
    update_gap_ledger(tmp_path, index, [_triaged_with(["Kubernetes"])])
    update_gap_ledger(tmp_path, index, [_triaged_with(["Kubernetes"])])
    import json
    led = json.loads((tmp_path / "skill-gaps.json").read_text(encoding="utf-8"))
    assert led["absent"]["Kubernetes"]["required_count"] == 2


def test_hard_rejections_do_not_feed_the_ledger(tmp_path, index):
    """The wrong city or the wrong level tells you nothing about which skills
    to learn."""
    update_gap_ledger(tmp_path, index, [_triaged_with(["Kubernetes"], verdict=REJECT)])
    import json
    led = json.loads((tmp_path / "skill-gaps.json").read_text(encoding="utf-8"))
    assert led["absent"] == {}


# ── recheck: stored results against today's profile ─────────────────────────

POLLED_BEFORE = "2026-09-23T00:00:00+00:00"   # triaged_at, earlier than any poll here


def stored(**kw):
    """A record as write_results stores it now."""
    base = {"id": "1", "title": "Backend Engineer", "company": "acme",
            "location": "Pune, India", "url": "", "board": "lever:acme",
            "description": "Python and PostgreSQL.", "excerpt_chars": EXCERPT_CHARS,
            "years_required": 3, "triaged_at": POLLED_BEFORE,
            "verdict": KEEP, "score": 6, "coverage": "2/2",
            "matched": {"Python": "core", "PostgreSQL": "core"},
            "required_gaps": [], "nice_gaps": [],
            "reasons": ["preferred location (Pune)", "3+ years asked"]}
    base.update(kw)
    return base


def legacy(**kw):
    """A record written before 2026-09-24: a 1600-character excerpt, and no
    years_required - the reason string is all that says what pass 1 read."""
    rec = stored(**kw)
    for key in ("excerpt_chars", "years_required", "triaged_at"):
        if key not in kw:
            rec.pop(key)
    return rec


CUT = "Filler about the company and its mission. " * 40      # > 1600 characters


def test_recheck_rejects_a_keep_the_profile_no_longer_allows(profile):
    """Razorpay's Business Analyst was stored as a keep before 'analyst' left
    the allowlist, and no later triage run could reach it."""
    verdict, reasons, unsure = recheck_record(
        legacy(title="Business Analyst ", location="Bengaluru",
               reasons=["acceptable location (Bengaluru)", "no years stated"]), profile)
    assert verdict == REJECT and unsure is None
    assert "no engineering signal" in reasons[-1]


def test_recheck_leaves_a_passing_posting_as_it_was(profile):
    rec = stored()
    assert recheck_record(rec, profile) == (KEEP, rec["reasons"], None)


@pytest.mark.parametrize("make", [stored, legacy])
def test_recheck_reads_years_pass_1_took_from_the_whole_description(make, profile):
    """The excerpt holds "8+ years overall"; the "3+ years with Python" after
    it was cut. Re-reading the excerpt would reject a posting pass 1 kept."""
    rec = make(description="8+ years of overall experience. " + CUT)
    assert required_years(rec["description"]) == 8
    assert recheck_record(rec, profile)[0] == KEEP


def test_recheck_applies_a_lowered_years_cap(profile):
    tighter = copy.deepcopy(profile)
    tighter["seniority"]["max_years_required"] = 2
    verdict, reasons, _ = recheck_record(stored(years_required=3), tighter)
    assert verdict == REJECT and reasons[-1] == "asks for 3+ years, profile caps at 2"


def test_a_cut_excerpt_does_not_reject_on_a_remote_it_may_have_lost(profile):
    """Pass 1 read 2000 characters; an old record kept 1600. A location with no
    place in it depends on the description saying "remote", so a failure on
    the excerpt proves nothing - the verdict stays, and says why."""
    rec = legacy(location="", description=CUT[:1600])
    verdict, reasons, unsure = recheck_record(rec, profile)
    assert (verdict, reasons) == (KEEP, rec["reasons"])
    assert "shorter than what pass 1 read" in unsure


def test_a_whole_excerpt_does_reject_on_location(profile):
    verdict, _, unsure = recheck_record(stored(location="", description=CUT[:1600]), profile)
    assert verdict == REJECT and unsure is None


def test_a_cut_excerpt_still_rejects_when_no_description_could_save_it(profile):
    """"Remote, Austria" fails whatever the description says, so the missing
    400 characters cannot matter. It is one of the 2026-09-24 keeps."""
    rec = legacy(location="Remote, Austria", description=CUT[:1600], reasons=["remote", "no years stated"])
    verdict, reasons, unsure = recheck_record(rec, profile)
    assert verdict == REJECT and unsure is None
    assert "Remote, Austria" in reasons[-1]


def test_recheck_keeps_no_match_and_flag(profile):
    no_match = stored(matched={}, verdict=NO_MATCH,
                      reasons=["preferred location (Pune)", "3+ years asked",
                               "no backed skill named"])
    assert recheck_record(no_match, profile)[0] == NO_MATCH
    assert recheck_record(stored(title="Software Engineer Intern"), profile)[0] == FLAG


# ── recheck against files ────────────────────────────────────────────────────

def _var(tmp_path, *records):
    return _results(tmp_path / "var", *records)


def _results(var, *records):
    """Stored triage results and a gap ledger, written into `var`."""
    var.mkdir(parents=True, exist_ok=True)
    blob = {"postings": {r["id"]: r for r in records}, "last_run": POLLED_BEFORE,
            "reject_reasons": {"'sales' role": 12}, "ai": {"ranked": 2}}
    (var / "triage-results.json").write_text(json.dumps(blob), encoding="utf-8")
    (var / "skill-gaps.json").write_text('{"absent": {"Go": {"required_count": 4}}}',
                                          encoding="utf-8")
    return var


def _live(var):
    return lambda board, pid, since: posting_live(var, board, pid, since)


def _stored_now(var):
    return json.loads((var / "triage-results.json").read_text(encoding="utf-8"))


AI = {"fit": "strong", "why": "Posting wants X; did X", "by": "model via provider"}


def test_a_recheck_is_a_dry_run_unless_told_to_write(tmp_path, profile):
    var = _var(tmp_path, stored(id="ba", title="Business Analyst"))
    before = (var / "triage-results.json").read_bytes()
    rep = recheck_results(var, profile, _live(var))
    assert [r["id"] for r, *_ in rep.changed] == ["ba"] and not rep.written
    assert (var / "triage-results.json").read_bytes() == before


def test_writing_keeps_ai_and_every_record_and_leaves_ledger_and_tallies(tmp_path, profile):
    var = _var(tmp_path, stored(id="ba", title="Business Analyst", ai=AI),
               stored(id="ok", ai=AI))
    gaps = (var / "skill-gaps.json").read_bytes()
    before = _stored_now(var)

    rep = recheck_results(var, profile, _live(var), write=True)
    after = _stored_now(var)

    assert rep.written
    assert after["postings"]["ba"]["verdict"] == REJECT
    assert after["postings"]["ba"]["recheck"]["was"] == KEEP
    # Rejected, not deleted: outreach may still refer to it by id.
    assert set(after["postings"]) == {"ba", "ok"}
    assert after["postings"]["ba"]["ai"] == AI and after["postings"]["ok"] == before["postings"]["ok"]
    assert after["reject_reasons"] == before["reject_reasons"]
    assert after["last_run"] == before["last_run"] and after["ai"] == before["ai"]
    assert (var / "skill-gaps.json").read_bytes() == gaps
    # And a second run finds nothing left to do.
    assert not recheck_results(var, profile, _live(var), write=True).changed


def test_closed_postings_are_marked_not_deleted_and_can_reopen(tmp_path, profile):
    var = _var(tmp_path, stored(id="gone"), stored(id="open"), stored(id="m", board="manual"))
    save_seen(seen_path(var, "lever", "acme"), ["open"], None)

    rep = recheck_results(var, profile, _live(var), write=True)
    assert [r["id"] for r in rep.closed] == ["gone"]
    assert [r["id"] for r in rep.unknown] == ["m"]
    postings = _stored_now(var)["postings"]
    assert postings["gone"]["closed_at"] and "closed_at" not in postings["open"]
    assert postings["gone"]["verdict"] == KEEP       # closed is not a verdict

    save_seen(seen_path(var, "lever", "acme"), ["open", "gone"], None)
    rep = recheck_results(var, profile, _live(var), write=True)
    assert [r["id"] for r in rep.reopened] == ["gone"]
    assert "closed_at" not in _stored_now(var)["postings"]["gone"]


def test_every_cycle_marks_what_left_the_board_and_changes_nothing_else(tmp_path):
    # "ba" is a keep the recheck would now reject; this must not touch that.
    var = _var(tmp_path, stored(id="gone", ai=AI), stored(id="open"),
               stored(id="ba", title="Business Analyst"), stored(id="m", board="manual"))
    save_seen(seen_path(var, "lever", "acme"), ["open", "ba"], None)
    before = _stored_now(var)

    assert sync_closed(var, _live(var)) == (["gone"], [])
    after = _stored_now(var)
    assert after["postings"]["gone"].pop("closed_at")
    assert after == before

    written = (var / "triage-results.json").read_bytes()
    assert sync_closed(var, _live(var)) == ([], [])
    assert (var / "triage-results.json").read_bytes() == written   # nothing to say, no write

    save_seen(seen_path(var, "lever", "acme"), ["open", "ba", "gone"], None)
    assert sync_closed(var, _live(var)) == ([], ["gone"])
    assert "closed_at" not in _stored_now(var)["postings"]["gone"]


def test_the_cycle_unmarks_a_returning_posting_before_triage_runs(tmp_path, monkeypatch):
    """Triage stamps a fresh `triaged_at`, and the watcher will not speak for a
    posting triaged after its last poll. Unmarked after triage, a posting that
    came back would stay hidden until the next poll."""
    from jobsearch import cycle, mailbox
    prof = make_root(tmp_path)
    var = _results(prof.var, stored(id="back", closed_at="2026-09-24T09:00:00+00:00"))

    def watch(fetcher, var_dir, boards):
        save_seen(seen_path(var_dir, "lever", "acme"), ["back"], None)
        return WatchRun([BoardWatch("lever", "acme", "acme", "polled", added=["back"])])

    at_triage: dict = {}

    def triage(p, args):
        at_triage.update(_stored_now(var)["postings"]["back"])
        return 0

    monkeypatch.setattr(cycle, "load_boards", lambda v: [{"provider": "lever", "slug": "acme"}])
    monkeypatch.setattr(cycle, "watch_all", watch)
    monkeypatch.setattr(cycle, "record_new", lambda v, run: None)
    monkeypatch.setattr(cycle, "run_triage", triage)
    monkeypatch.setattr(mailbox, "configured", lambda p: False)

    cycle.run_cycle(tmp_path, quiet=True)
    assert at_triage and "closed_at" not in at_triage


@pytest.mark.parametrize("ids, since, expected", [
    (["7"], None, True),
    ([], None, False),
    ([], "2026-09-24T05:00:00+00:00", False),   # polled after triage: gone
    ([], "2026-09-24T07:00:00+00:00", None),    # triaged after the last poll
])
def test_posting_live_waits_for_a_poll_after_triage(tmp_path, ids, since, expected):
    """A posting read from a live `--board` ten minutes ago is not closed just
    because the watcher has not polled since."""
    path = seen_path(tmp_path, "lever", "acme")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"ids": ids, "last_poll": "2026-09-24T06:00:00+00:00"}))
    assert posting_live(tmp_path, "lever:acme", "7", since) is expected
    assert posting_live(tmp_path, "lever:unwatched", "7") is None
    assert posting_live(tmp_path, "manual", "7") is None


def test_the_recheck_command_never_touches_the_network(tmp_path, monkeypatch):
    prof = make_root(tmp_path)
    var = _results(prof.var, stored(id="ba", title="Business Analyst"))

    def no_network(*a, **kw):
        raise AssertionError("a recheck tried to use the network")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(triage_cli, "default_fetcher", no_network)

    args = argparse.Namespace(recheck=True, write=True, all=False, board=None)
    assert triage_cli.run_triage(prof, args) == 0
    assert _stored_now(var)["postings"]["ba"]["verdict"] == REJECT


def test_retriage_keeps_what_later_steps_added(tmp_path):
    """write_results replaced records whole, so `triage --all` erased the
    model's judgements. It re-derives the verdict and nothing else."""
    var = _var(tmp_path, stored(id="1", ai=AI, closed_at="2026-09-24T06:00:00+00:00"))
    t = Triaged(posting(description="x" * 5000), KEEP, ["remote"],
                Coverage(named={"Python"}, matched={"Python": CORE}))
    write_results(var, [t])
    rec = _stored_now(var)["postings"]["1"]
    assert rec["ai"] == AI and rec["closed_at"] == "2026-09-24T06:00:00+00:00"
    assert len(rec["description"]) == rec["excerpt_chars"] == EXCERPT_CHARS
    assert "years_required" in rec and rec["triaged_at"]


def test_the_dashboard_hides_closed_and_rechecked_out_postings(tmp_path):
    prof = make_root(tmp_path)
    _results(prof.var, stored(id="1", title="Backend Engineer"),
             stored(id="2", title="Frontend Engineer", closed_at="2026-09-24T06:00:00+00:00"),
             stored(id="3", title="Business Analyst", verdict=REJECT))
    html = render(prof).read_text(encoding="utf-8")
    assert "Backend Engineer" in html
    assert "Frontend Engineer" not in html and "Business Analyst" not in html
    assert "Not shown: 1 closed on their board" in html


# ── seniority that got through, found by a human on 2026-09-29 ──────────────

@pytest.mark.parametrize("text, expected", [
    # OTSI, verbatim: the label comes first, and "10 + Years" has a space.
    ("Lead Agentic AI Engineer (Pune) Experience: 10 + Years MAJOR RESPONSIBILITIES", 10),
    ("Exp - 8 to 12 yrs", 8),
    ("Experience required: 3+ years", 3),
])
def test_years_stated_after_a_label(text, expected):
    assert required_years(text) == expected


@pytest.mark.parametrize("title", ["Lead AIML Engineer", "Lead Software Engineer", "Applied AI/ML Lead",
                                   "Sr Lead Software Engineer - Python, Agentic AI Solutions", "Team Lead - QA"])
def test_every_lead_title_is_above_the_current_level(title, profile):
    """"lead engineer" as a substring missed all of these; 20 were ranked strong."""
    assert seniority_verdict(title, "", profile)[0] == REJECT


@pytest.mark.parametrize("title", ["Software Engineer II", "Senior Software Engineer", "Software Engineer III"])
def test_ordinary_titles_are_still_kept(title, profile):
    assert seniority_verdict(title, "", profile)[0] == KEEP


def test_amazons_standard_phrasing_states_its_years():
    """185 Amazon postings read as "no years stated" with a 45-character window."""
    assert required_years("- 3+ years of non-internship professional software development experience") == 3
    assert required_years("- 5+ years of non-internship professional software development experience\n"
                          "- 5+ years of leading design or architecture of new and existing systems experience") == 5


# ── job types the profile never wants (reject_function) ─────────────────────

@pytest.mark.parametrize("title", ["Customer Experience Engineer, L2", "DevRel Engineer II", "Field Security Engineer",
                                   "Quality Assurance Engineer 1, QRE", "Software Development Engineer in Test",
                                   "Software Development Engineer - Test II, Alexa", "QE SDET",
                                   "Infrastructure Test Engineering Senior Analyst - Performance",
                                   "Data Engineer II", "Data Scientist Associate", "Data Scientist III",
                                   "Business Intelligence Engineer"])
def test_excluded_job_types_are_rejected(title, profile):
    assert function_verdict(title, profile)[0] is False


@pytest.mark.parametrize("title", ["Software Engineer III - AWS, Python, Snowflake", "Senior Machine Learning Engineer",
                                   "Senior Software Engineer - Data Platform", "Software Development Engineer II, FBA",
                                   "Java Full Stack (Java, ReactJS, AWS)", "Site Reliability Engineer II"])
def test_software_and_ai_engineering_is_kept(title, profile):
    assert function_verdict(title, profile)[0] is True
