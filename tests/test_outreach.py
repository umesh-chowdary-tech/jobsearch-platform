"""Outreach tests. Everything here is a message that goes to a real person
under the job seeker's name, so the checks are the product - these test the
checks. The person is the fictional sample profile."""
from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jobsearch import outreach as o
from jobsearch.assemble import Variant, emittable, load_store
from jobsearch.llm import Answer
from jobsearch.skills import load_index
from samples import make_root, sample

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "jobsearch"
URL = "https://job-boards.greenhouse.io/examplecorp/jobs/1000001"
BOARD = "greenhouse:razorpaysoftwareprivatelimited"   # data/company-names.yaml: Razorpay


@pytest.fixture(scope="module")
def store():
    return load_store(sample())


@pytest.fixture(scope="module")
def index():
    return load_index(sample())


@pytest.fixture(scope="module")
def variant(store):
    return Variant(id="backend", angle="backend",
                   emits=[e["id"] for e in store["entries"] if emittable(e)],
                   title_descriptor="Backend Software Engineer")


def _posting(title="Senior Backend Engineer", pid="1000001"):
    return {"id": pid, "title": title, "company": "razorpaysoftwareprivatelimited", "url": URL,
            "board": BOARD, "location": "Hybrid in Bangalore, India",
            "matched": {"Python": "core", "FastAPI": "core", "PostgreSQL": "core"}}


def _ctx(store, index, variant, how="cold", role="Senior Backend Engineer",
         company="Razorpay", context=""):
    entries = o.pick_entries(store, index, variant, _posting(role))
    idn = store["identity"]
    ctx = o.Context(
        me_full=idn["name"], me_first=idn["name"].split()[0], to_first="Priya",
        how=how, company=company, role=role, url=URL,
        descriptor=variant.title_descriptor, band=idn["title"]["official"],
        college=o._college(store), context=context, facts=o.facts_for(entries),
        employer="Acme Freight", city="Pune", shared="Acme Freight", shared_current=True)
    return ctx, entries


def _fails(parts, ctx, index):
    return {c["check"]: c["detail"] for c in o.check(parts, ctx, index)
            if not c["ok"] and c["level"] == "fail"}


# ── the boundary: this module cannot send ────────────────────────────────────

SENDING = {"requests", "httpx", "aiohttp", "urllib.request", "http.client",
           "smtplib", "socket", "selenium", "playwright", "webbrowser"}


@pytest.mark.parametrize("module", ["outreach.py", "outreach_cli.py"])
def test_outreach_cannot_send_anything(module):
    """The agent drafts and the person sends. Held here by what the file is
    able to import, not by a promise in a docstring."""
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            imported.add(node.module)
    # urllib.parse (building search links) is fine; urllib.request is not.
    assert not {m for m in imported if m in SENDING or m.split(".")[0] in SENDING}


# ── the template: always there, always clean ─────────────────────────────────

@pytest.mark.parametrize("how", o.RELATIONSHIPS)
@pytest.mark.parametrize("role, company", [
    ("Senior Backend Engineer", "Razorpay"),
    ("Senior Security Engineer - Corporate Security", "Atlan"),
    ("Software Development Engineer III DevOps", "Meesho"),
    ("SDE 2, Platform", "MongoDB"),          # a number in the title; a company that is a skill
])
def test_template_passes_every_blocking_check(store, index, variant, how, role, company):
    ctx, entries = _ctx(store, index, variant, how, role, company)
    parts = o.template(ctx, entries)
    assert not _fails(parts, ctx, index)
    if how == "warm":
        assert parts["note"] is None
    else:
        assert len(parts["note"]) <= o.NOTE_LIMIT


def test_the_profiles_own_employer_and_city_are_used(store, index, variant):
    """Where the person works comes from identity.employment and
    identity.location - the template names no employer of its own."""
    ctx, entries = _ctx(store, index, variant)
    parts = o.template(ctx, entries)
    assert "at Acme Freight in Pune" in parts["message"]
    assert parts["blurb"].startswith("Kavya Example is a Software Engineer II (Backend Software Engineer)"
                                     " at Acme Freight in Pune.")


def test_newest_role_leads_and_is_not_demoted_to_also(store, index, variant):
    """Relevance put an older workstream first and the draft said "I also
    built..." about the most senior work."""
    ctx, entries = _ctx(store, index, variant)
    assert entries[0]["id"] == "acme-agent-platform"
    msg = o.template(ctx, entries)["message"]
    assert "I built Acme's internal AI agent platform for the operations team" in msg
    assert "also built Acme's internal" not in msg


def test_low_priority_entries_never_get_a_sentence(store, index, variant):
    _, entries = _ctx(store, index, variant)
    assert all(e.get("priority") != "low" for e in entries)


def test_role_is_not_said_twice(store, index, variant):
    """"I'm a Backend Software Engineer ... I'm a backend engineer on Acme
    Track" - the noun-phrase headline repeated the introduction."""
    assert o.first_person("Backend engineer on Acme Track, the shipment tracking platform") \
        == "I work on Acme Track, the shipment tracking platform"
    assert o.third_person("Kavya", "Backend engineer on Acme Track, X", also=True) \
        == "Kavya also works on Acme Track, X"


def test_acronyms_keep_their_case():
    assert o._lower_first("IBM's") == "IBM's"
    assert o._lower_first("Built") == "built"


def test_blurb_names_the_person_and_guesses_no_pronoun(store, index, variant):
    ctx, entries = _ctx(store, index, variant)
    blurb = o.template(ctx, entries)["blurb"]
    assert not o._PRONOUNS.search(blurb)
    assert blurb.startswith(store["identity"]["name"])
    assert store["identity"]["title"]["official"] in blurb   # the title leads, per identity rule


# ── the checks, each on the failure that motivated it ────────────────────────

def test_a_number_is_checked_with_its_unit(store, index, variant):
    """Digits alone: "4 years" passes, because "under 4 months" is a fact."""
    ctx, _ = _ctx(store, index, variant)
    ok, _ = o.numbers_grounded("took it from prototype to production in under 4 months", ctx.facts)
    assert ok
    ok, why = o.numbers_grounded("I have 4 years of backend experience", ctx.facts)
    assert not ok and "4 years" in why


def test_spelled_out_numbers_are_checked_too(store, index, variant):
    ctx, _ = _ctx(store, index, variant)
    ok, _ = o.numbers_grounded("five years building platforms", ctx.facts)
    assert not ok


def test_digits_in_the_link_are_not_claims(store, index, variant):
    ctx, _ = _ctx(store, index, variant)
    ok, _ = o.numbers_grounded(f"the role is here:\n{URL}", ctx.facts)
    assert ok


def test_led_the_development_is_caught(store, index, variant):
    """The first live model rewrite: "architected" became "led the development
    of", and every check then in place passed it."""
    ctx, entries = _ctx(store, index, variant)
    parts = o.template(ctx, entries)
    parts["message"] = parts["message"].replace("I built", "I led the development of")
    assert "claims_grounded" in _fails(parts, ctx, index)


@pytest.mark.parametrize("word", ["spearheaded", "owned", "expert", "large-scale"])
def test_inflating_words_are_caught(store, index, variant, word):
    ctx, entries = _ctx(store, index, variant)
    parts = o.template(ctx, entries)
    parts["blurb"] += f" Kavya is {word} in platforms."
    assert "claims_grounded" in _fails(parts, ctx, index)


def test_a_message_about_the_person_instead_of_by_them_is_caught(store, index, variant):
    """"<name> is applying ... would you be open to referring <name>?" """
    ctx, entries = _ctx(store, index, variant, how="warm")
    parts = o.template(ctx, entries)
    parts["message"] = (f"Hi Priya,\n\nKavya is applying for the Senior Backend "
                        f"Engineer role at Razorpay:\n{URL}\n\nWould you be open "
                        f"to referring Kavya? No worries if not.")
    assert _fails(parts, ctx, index)["first_person"].startswith("talks about Kavya")


def test_signing_off_with_their_name_is_first_person(store, index, variant):
    ctx, entries = _ctx(store, index, variant)
    ok, _ = o.first_person_voice(o.template(ctx, entries)["message"], ctx)
    assert ok


@pytest.mark.parametrize("sentence, expect", [
    ("I have hands-on Kafka experience.", "banned"),
    ("I have run Kubernetes clusters.", "does not back"),
])
def test_unbacked_skills_cannot_be_named(store, index, variant, sentence, expect):
    ctx, entries = _ctx(store, index, variant)
    parts = o.template(ctx, entries)
    parts["message"] = parts["message"].replace("I also work", sentence + " I also work")
    assert expect in _fails(parts, ctx, index).get("skills_backed", "")


def test_the_employer_is_not_a_skill_claim(store, index, variant):
    ctx, entries = _ctx(store, index, variant, company="MongoDB", role="Golang Engineer")
    assert o.skills_backed(o.template(ctx, entries)["message"], ctx, index)[0]


def test_invented_research_about_the_recipient_is_caught(store, index, variant):
    ctx, entries = _ctx(store, index, variant)
    parts = o.template(ctx, entries)
    parts["message"] = parts["message"].replace(
        "Hi Priya,", "Hi Priya,\n\nI loved your recent post on platform teams.")
    assert "no_invented_familiarity" in _fails(parts, ctx, index)


def test_a_note_without_the_company_fails(store, index, variant):
    """The model's note was "Hi Priya, referral for Senior Backend Engineer"."""
    ctx, entries = _ctx(store, index, variant)
    parts = o.template(ctx, entries)
    parts["note"] = "Hi Priya, referral for Senior Backend Engineer"
    assert "names_company" in _fails(parts, ctx, index)


def test_placeholders_are_caught(store, index, variant):
    ctx, entries = _ctx(store, index, variant)
    parts = o.template(ctx, entries)
    parts["message"] = parts["message"].replace("Hi Priya", "Hi [Name]")
    assert "no_placeholders" in _fails(parts, ctx, index)


# ── the model's one sentence ─────────────────────────────────────────────────

def test_a_duplicate_greeting_is_cut_not_rejected():
    assert o.strip_greeting("Hey Sam, it was great working together.") \
        == "It was great working together."
    assert o.strip_greeting("Hiking was fun.") == "Hiking was fun."


@pytest.mark.parametrize("opener, ok", [
    ("It was great working together on the Acme Track team.", True),
    ("Before you moved to Razorpay we sat together on the Acme Track team.", True),  # context says so
    ("I'm keen on the Senior Backend Engineer role.", False),                         # the job
    ("We shipped 12 releases together on the Acme Track team.", False),               # invented number
    ("Kavya really enjoyed the Acme Track team.", False),                             # third person
])
def test_opener_is_grounded_in_context_alone(store, index, variant, opener, ok):
    ctx, _ = _ctx(store, index, variant, how="warm",
                  context="we were on the Acme Track team at Acme until you moved to Razorpay")
    assert o.opener_ok(opener, ctx)[0] is ok


class FakeLLM:
    available = True

    def __init__(self, opener):
        self.opener = opener
        self.seen: list[str] = []

    def ask_json(self, system, user, **kw):
        self.seen.append(user)
        return Answer(ok=True, data={"opener": self.opener}, provider="fake", model="m")


# ── end to end, in a throwaway root ──────────────────────────────────────────

def _seed(prof, store):
    var = prof.var
    (prof.shared / "seen").mkdir(parents=True, exist_ok=True)
    (var / "triage-results.json").write_text(json.dumps(
        {"postings": {"1000001": _posting(), "555": _posting("Frontend Engineer", "555")}}))
    lib = [Variant(id="backend", angle="backend",
                   emits=[e["id"] for e in store["entries"] if emittable(e)],
                   sent_to=[{"posting_id": "1000001", "date": "2026-09-23"}]).to_dict()]
    (var / "resume-variants.json").write_text(json.dumps(lib))
    (prof.shared / "seen" / "greenhouse__razorpaysoftwareprivatelimited.json").write_text(
        json.dumps({"ids": ["1000001"]}))
    return prof


@pytest.fixture
def root(tmp_path, store):
    return _seed(make_root(tmp_path), store)


def test_no_resume_no_referral_request(root):
    with pytest.raises(o.OutreachError, match="assemble --posting 555"):
        o.draft(root, "555", "Priya Sharma", "cold", use_model=False)


def test_draft_writes_the_ledger_and_a_copy_paste_file(root):
    d = o.draft(root, "1000001", "Priya Sharma", "cold", use_model=False)
    assert o.passed(d.checks) and d.by == "No AI involved"
    assert d.company == "Razorpay"                        # never the slug
    assert o.load_ledger(root)[d.id]["status"] == "drafted"
    md = (root.var / "outreach" / f"{d.id}.md").read_text(encoding="utf-8")
    assert d.message in md and d.blurb in md


def test_no_context_means_no_model_call(root):
    llm = FakeLLM("anything")
    o.draft(root, "1000001", "Priya Sharma", "cold", llm=llm)
    assert llm.seen == []


def test_the_recipients_name_never_reaches_the_model(root):
    llm = FakeLLM("It was great working together on the Acme Track team.")
    d = o.draft(root, "1000001", "Ravi Kumar", "warm",
                context="Ravi and I were on the Acme Track team together", llm=llm)
    assert llm.seen and all("Ravi" not in u for u in llm.seen)
    assert "It was great working together on the Acme Track team." in d.message
    assert d.by.startswith("store facts; opener by")


def test_a_bad_opener_falls_back_to_the_template(root):
    llm = FakeLLM("We shipped 40 features together, you led it all.")
    d = o.draft(root, "1000001", "Ravi Kumar", "warm",
                context="we were on the Acme Track team together", llm=llm)
    assert d.by == "No AI involved" and d.rejected
    assert "Hope you're doing well!" in d.message and o.passed(d.checks)


def test_a_sent_ask_is_not_overwritten(root):
    d = o.draft(root, "1000001", "Priya Sharma", "cold", use_model=False)
    o.mark(root, d.id, "sent")
    with pytest.raises(o.OutreachError, match="already 'sent'"):
        o.draft(root, "1000001", "Priya Sharma", "cold", use_model=False)
    assert o.draft(root, "1000001", "Priya Sharma", "cold", use_model=False, force=True)


def test_a_second_ask_at_the_same_company_is_flagged(root):
    d = o.draft(root, "1000001", "Priya Sharma", "cold", use_model=False)
    o.mark(root, d.id, "sent")
    d2 = o.draft(root, "1000001", "Anil Rao", "cold", use_model=False)
    warn = {c["check"]: c for c in d2.checks}["one_ask_per_company"]
    assert not warn["ok"] and warn["level"] == "warn" and "Priya" in warn["detail"]


def test_find_orders_warm_connections_first_and_fetches_nothing(root):
    r = o.find_leads(root, _posting())
    assert [x["how"] for x in r["leads"]][:2] == ["ex-colleague", "alumni"]
    assert r["leads"][0]["employer"] == "Acme Freight"
    assert all(x["url"].startswith("https://www.linkedin.com/search/results/people/")
               for x in r["leads"])
    assert "Bangalore" in r["leads"][2]["url"]


def test_an_ex_colleague_from_a_past_employer(tmp_path):
    """Two employers: a shared past one says "I worked there too", not "I'm
    there now" - and an employer not on record is refused."""
    prof = make_root(tmp_path, "sample-android")
    _seed(prof, load_store(prof))
    d = o.draft(prof, "1000001", "Asha Rao", "ex-colleague", use_model=False, employer="Umbrella Apps")
    assert "I saw you were at Umbrella Apps before Razorpay. I worked there too." in d.message
    assert o.passed(d.checks)
    now = o.draft(prof, "1000001", "Vikram Shah", "ex-colleague", use_model=False)
    assert "I'm there now" in now.message and "Globex Mobile" in now.message
    with pytest.raises(o.OutreachError, match="not in identity.employment"):
        o.draft(prof, "1000001", "Asha Rao", "ex-colleague", use_model=False, employer="Initech")
    assert [x["employer"] for x in o.find_leads(prof, _posting())["leads"][:2]] \
        == ["Globex Mobile", "Umbrella Apps"]


# ── tracking ─────────────────────────────────────────────────────────────────

def _at(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


@pytest.mark.parametrize("status, days, expect", [
    ("drafted", 0, "review and send"),
    ("sent", 2, None),
    ("sent", 6, "follow up"),
    ("followed-up", 3, None),
    ("followed-up", 8, "close it"),
    ("referred", 30, None),
])
def test_what_is_due(status, days, expect):
    got = o.due({"status": status, "history": [{"status": status, "at": _at(days)}]})
    assert (got is None) if expect is None else got.startswith(expect)


def test_a_nudge_only_says_still_open_when_the_watcher_says_so():
    d = {"to": "Priya Sharma", "me": "Kavya Example", "url": URL}
    assert "still open" in o.follow_up(d, True)
    assert "still open" not in o.follow_up(d, None)
    assert "closed" in o.follow_up(d, False) and URL not in o.follow_up(d, False)


def test_company_names_prefer_the_curated_file(root):
    assert o.company_name(root, "greenhouse:razorpaysoftwareprivatelimited", {}) == ("Razorpay", True)
    # Pure Storage renamed itself Everpure (confirmed 2026-09-25): a message
    # should use the name the company uses now.
    assert o.company_name(root, "greenhouse:purestorage", {}) == ("Everpure", True)


def test_an_unlisted_board_name_is_cleaned_and_flagged(root):
    (root.shared / "boards.json").write_text(json.dumps(
        {"greenhouse:newco": {"slug": "newco", "company": "newco",
                              "board_name": "NewCo Job Board"}}))
    assert o.company_name(root, "greenhouse:newco", {}) == ("NewCo", False)
    assert o.company_name(root, "lever:acme", {"company": "acme"}) == ("Acme", False)
