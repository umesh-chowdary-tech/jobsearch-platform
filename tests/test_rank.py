"""Pass 3 tests. The LLM is stubbed; the prompt building and the parsing are real."""
from __future__ import annotations

import json

import pytest
import yaml

from jobsearch.llm import LLM, Answer, _extract_json
from jobsearch.rank import (
    BATCH,
    MAX_TRIES,
    PROMPT_VERSION,
    apply_to_results,
    profile_digest,
    rank_shortlist,
    sort_key,
    unread,
)
from samples import make_root, sample

PROF = sample()


@pytest.fixture(scope="module")
def store():
    return yaml.safe_load(PROF.file("master-store.yaml").read_text(encoding="utf-8"))


class StubLLM(LLM):
    """Records every prompt and returns scripted answers."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.chain = []

    @property
    def available(self):
        return True

    def ask_json(self, system, user, max_tokens=900, temperature=0.2, cached_prefix=""):
        self.prompts.append(cached_prefix + user)
        if not self.replies:
            return Answer(ok=False, failures=[{"provider": "stub", "error": "exhausted"}])
        data = self.replies.pop(0)
        return Answer(ok=True, data=data, provider="stub", model="stub-1")


def _posting(pid, **kw):
    base = {"id": pid, "title": "Backend Engineer", "company": "acme",
            "location": "Pune", "score": 10, "coverage": "3/5",
            "matched": {"Python": "core"}, "required_gaps": ["Kubernetes"],
            "description": "We need Python and AWS."}
    base.update(kw)
    return base


# ── the profile the model is given ───────────────────────────────────────────

def test_digest_includes_every_work_and_project_entry(store):
    n = len([e for e in store["entries"]
             if e["type"] in ("work", "project")
             and e.get("confidence") != "low" and e.get("status") != "parked"])
    assert len([l for l in profile_digest(store).splitlines() if l.startswith("- ")]) == n


def test_digest_includes_the_ml_work(store):
    """Regression. The digest was capped at 9 entries sorted work-first; with
    8 work entries, exactly one project survived and a whole ML portfolio
    never reached the model. It then judged an ML posting as "lacks
    PyTorch/transformer fine-tuning experience" - a correct inference from a
    silently truncated profile.

    A partial profile does not give a slightly worse ranking. It gives a
    confident wrong one."""
    d = profile_digest(store)
    assert "DistilBERT" in d
    assert "QLoRA" in d
    assert "token F1" in d          # the measured extraction number


def test_digest_excludes_unconfirmed_entries(store):
    """Anything the store will not emit must not reach the model either."""
    d = profile_digest(store)
    for e in store["entries"]:
        if e.get("confidence") == "low" or e.get("status") == "parked":
            assert e["headline"] not in d


def test_digest_carries_measured_metrics_only(store):
    d = profile_digest(store)
    assert "measured:" in d
    assert "20%" not in d           # the invented percentages never existed here


# ── parsing the reply ────────────────────────────────────────────────────────

def test_judgements_are_attached_by_id():
    ps = [_posting("a"), _posting("b")]
    llm = StubLLM([{"rankings": [
        {"id": "a", "fit": "strong", "why": "does the thing"},
        {"id": "b", "fit": "weak", "why": "does not"},
    ]}])
    out, ans = rank_shortlist(PROF, ps, llm=llm)
    assert ans.ok
    assert out[0]["ai"]["fit"] == "strong"
    assert out[1]["ai"]["fit"] == "weak"


def test_an_unknown_id_is_ignored():
    ps = [_posting("a")]
    llm = StubLLM([{"rankings": [{"id": "ghost", "fit": "strong", "why": "x"}]}])
    out, _ = rank_shortlist(PROF, ps, llm=llm)
    assert "ai" not in out[0]


def test_an_invalid_fit_is_ignored():
    """Better no judgement than a made-up category silently sorting first."""
    ps = [_posting("a")]
    llm = StubLLM([{"rankings": [{"id": "a", "fit": "excellent", "why": "x"}]}])
    out, _ = rank_shortlist(PROF, ps, llm=llm)
    assert "ai" not in out[0]


def test_a_renamed_list_is_still_found():
    ps = [_posting("a")]
    llm = StubLLM([{"results": [{"id": "a", "fit": "possible", "why": "x"}]}])
    out, _ = rank_shortlist(PROF, ps, llm=llm)
    assert out[0]["ai"]["fit"] == "possible"


def test_postings_are_batched():
    """One call for everything produced long replies that models truncate and
    pad with generic reasons."""
    ps = [_posting(str(i)) for i in range(BATCH * 2 + 1)]
    llm = StubLLM([{"rankings": []}] * 3)
    rank_shortlist(PROF, ps, llm=llm)
    assert len(llm.prompts) == 3


def test_one_failed_batch_does_not_lose_the_others():
    ps = [_posting(str(i)) for i in range(BATCH + 1)]
    llm = StubLLM([{"rankings": [{"id": "0", "fit": "strong", "why": "x"}]}])
    out, ans = rank_shortlist(PROF, ps, llm=llm)   # second batch gets "exhausted"
    assert ans.ok                                   # partial success is success
    assert out[0]["ai"]["fit"] == "strong"
    assert any("exhausted" in f["error"] for f in ans.failures)


def test_the_profile_goes_into_every_batch():
    ps = [_posting(str(i)) for i in range(BATCH + 1)]
    llm = StubLLM([{"rankings": []}, {"rankings": []}])
    rank_shortlist(PROF, ps, llm=llm)
    assert all("DistilBERT" in p for p in llm.prompts)


# ── ordering ─────────────────────────────────────────────────────────────────

def test_model_judgement_outranks_keyword_score():
    strong_low = {"score": 1, "ai": {"fit": "strong"}}
    weak_high = {"score": 99, "ai": {"fit": "weak"}}
    assert sorted([weak_high, strong_low], key=sort_key)[0] is strong_low


def test_score_breaks_ties_within_a_fit():
    a = {"score": 5, "ai": {"fit": "possible"}}
    b = {"score": 9, "ai": {"fit": "possible"}}
    assert sorted([a, b], key=sort_key)[0] is b


def test_an_unranked_posting_keeps_its_keyword_place():
    """An un-ranked posting is not a bad posting - the model simply never saw
    it. It must not sink below things judged weak."""
    unranked = {"score": 50}
    weak = {"score": 1, "ai": {"fit": "weak"}}
    order = sorted([unranked, weak], key=sort_key)
    assert order[0] is weak and order[1] is unranked


# ── the no-key path ──────────────────────────────────────────────────────────

def test_without_a_key_nothing_is_claimed(monkeypatch):
    for k in ("GROQ_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    llm = LLM()
    llm.chain = [p for p in llm.chain]
    assert not llm.available
    ans = llm.ask_json("s", "u")
    assert not ans.ok
    assert ans.attribution == "No AI involved"


@pytest.mark.parametrize("text, expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Sure! Here you go:\n{"a": 1}\nHope that helps.', {"a": 1}),
    ("no json here", None),
    ("", None),
])
def test_json_is_extracted_from_whatever_the_model_wraps_it_in(text, expected):
    assert _extract_json(text) == expected


# ── which postings are read ──────────────────────────────────────────────────

class JudgeAll(StubLLM):
    """Judges every id it is shown - except the ones in `skip`."""

    def __init__(self, skip=()):
        super().__init__([])
        self.skip, self.seen = set(skip), []

    def ask_json(self, system, user, max_tokens=900, temperature=0.2, cached_prefix=""):
        import re
        self.prompts.append(cached_prefix + user)
        ids = re.findall(r"^### id: (\S+)", user, re.M)
        self.seen += ids
        return Answer(ok=True, provider="stub", model="stub-1", data={"rankings": [
            {"id": i, "fit": "possible", "why": "x"} for i in ids if i not in self.skip]})


@pytest.fixture
def results(tmp_path):
    prof = make_root(tmp_path)
    postings = {str(i): _posting(str(i), verdict="keep", score=i) for i in range(9)}
    postings["read"] = _posting("read", verdict="keep", score=99,
                                ai={"fit": "strong", "why": "y", "by": "m", "prompt": PROMPT_VERSION})
    postings["closed"] = _posting("closed", verdict="keep", score=98, closed_at="2026-09-20T00:00:00+00:00")
    postings["dropped"] = _posting("dropped", verdict="discard", score=97)
    path = prof.var / "triage-results.json"
    path.write_text(json.dumps({"postings": postings}))
    return prof, path


def test_every_unread_kept_posting_is_read_four_to_a_request(results):
    """It used to take the top ten by score, read or not - so the same ten
    were re-judged and 37 kept postings were never read."""
    root, path = results
    llm = JudgeAll()
    res = apply_to_results(root, llm=llm)
    assert sorted(llm.seen) == sorted(str(i) for i in range(9))      # not read, closed or discarded
    assert len(llm.prompts) == -(-9 // BATCH) and res["ranked"] == 9
    blob = json.loads(path.read_text())
    assert unread(blob) == [] and blob["postings"]["read"]["ai"]["fit"] == "strong"
    assert apply_to_results(root, llm=JudgeAll())["considered"] == 0   # nothing left: no call


def test_reread_judges_every_kept_posting_again(results):
    root, _ = results
    llm = JudgeAll()
    apply_to_results(root, llm=llm, reread=True)
    assert "read" in llm.seen and "closed" not in llm.seen and len(llm.seen) == 10


def test_a_posting_the_model_keeps_skipping_stops_being_offered(results):
    root, path = results
    for _ in range(MAX_TRIES + 1):
        llm = JudgeAll(skip={"3"})
        apply_to_results(root, llm=llm)
    assert "3" not in llm.seen                                       # given up on, not re-sent forever
    assert json.loads(path.read_text())["postings"]["3"]["ai_tries"] == MAX_TRIES


def test_a_failed_request_counts_against_no_posting(results):
    root, path = results
    apply_to_results(root, llm=StubLLM([]))                          # every request fails
    assert all("ai_tries" not in p for p in json.loads(path.read_text())["postings"].values())


def test_the_key_check_passes_if_any_provider_takes_requests(monkeypatch):
    class R:
        def __init__(self, code):
            self.status_code = code
    monkeypatch.setenv("GROQ_API_KEY", "expired")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fine")
    monkeypatch.setattr("requests.get", lambda url, **k: R(401 if "groq" in url else 200))
    assert LLM(providers=["groq", "openrouter"]).key_check() == (True, "")
    ok, why = LLM(providers=["groq"]).key_check()
    assert not ok and "Groq rejected the API key" in why


# ── level: the model is told it, and cannot rank past it ────────────────────

def test_the_model_is_told_the_level_and_each_postings_ask():
    llm = StubLLM([{"rankings": []}])
    rank_shortlist(PROF, [_posting("1", description="Experience: 10 + Years. Build agentic AI.")], llm=llm)
    prompt = llm.prompts[0]
    assert "LEVEL: 4 years of professional experience" in prompt and "individual contributor" in prompt
    assert "B.E." in prompt and "no Master's, no PhD" in prompt
    assert "Titles out of reach: staff, principal" in prompt
    assert "stated minimum experience: 10 years" in prompt


def test_a_posting_asking_too_many_years_is_never_better_than_weak():
    """A Lead AIML Engineer posting (10+ years) came back "strong"."""
    llm = StubLLM([{"rankings": [{"id": "1", "fit": "strong", "why": "Agentic AI platform, like Acme's"},
                                 {"id": "2", "fit": "strong", "why": "Spring services, like Acme's"}]}])
    out, _ = rank_shortlist(PROF, [_posting("1", description="Experience: 10 + Years."),
                                   _posting("2", description="3+ years of experience.")], llm=llm)
    assert out[0]["ai"]["fit"] == "weak" and out[0]["ai"]["why"].startswith("Asks for 10+ years against 4.")
    assert out[1]["ai"]["fit"] == "strong"


def test_a_judgement_under_older_rules_is_read_again(results):
    """A fit judged before the level rules is stale, not settled."""
    root, path = results
    blob = json.loads(path.read_text())
    blob["postings"]["read"]["ai"] = {"fit": "strong", "why": "old", "by": "m"}         # no prompt stamp
    path.write_text(json.dumps(blob))
    llm = JudgeAll()
    apply_to_results(root, llm=llm)
    assert "read" in llm.seen
    after = json.loads(path.read_text())["postings"]
    assert all(p["ai"]["prompt"] == PROMPT_VERSION for p in after.values() if p.get("ai"))
    assert apply_to_results(root, llm=JudgeAll())["considered"] == 0                   # settled now


# ── rate limits: wait on the free provider, don't spill onto a paid one ─────

class _Resp:
    def __init__(self, status, text="", headers=None, body=None):
        self.status_code, self.text, self.headers, self._body = status, text, headers or {}, body

    def json(self):
        return self._body


def test_a_rate_limit_is_waited_out_on_the_same_provider(monkeypatch):
    """Groq's 8,000 tokens a minute sent every other bulk request to paid
    OpenRouter, until its credits ran out (402)."""
    monkeypatch.setenv("GROQ_API_KEY", "k")
    replies = [_Resp(429, "Rate limit reached ... Please try again in 7.5s."),
               _Resp(200, body={"choices": [{"message": {"content": '{"rankings": []}'}}]})]
    posted, slept = [], []
    monkeypatch.setattr("jobsearch.llm.requests.post", lambda url, **k: posted.append(url) or replies.pop(0))
    monkeypatch.setattr("jobsearch.llm.time.sleep", lambda s: slept.append(s))
    ans = LLM(providers=["groq", "openrouter"]).ask_json("s", "u")
    assert ans.ok and ans.provider == "groq" and slept == [7.75]
    assert all("groq" in u for u in posted)


def test_a_long_wait_is_not_waited_for(monkeypatch):
    """A daily cap ("try again in 33m7s") falls through at once."""
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("jobsearch.llm.requests.post", lambda url, **k: _Resp(429, "try again in 33m7.2s"))
    monkeypatch.setattr("jobsearch.llm.time.sleep", lambda s: pytest.fail("waited for a daily cap"))
    assert not LLM(providers=["groq"]).ask_json("s", "u").ok


_TPD = ("Rate limit reached for model `openai/gpt-oss-120b` on tokens per day (TPD): "
        "Limit 200000, Used 197400, Requested 5100. Please try again in 40.3s.")


def test_a_daily_limit_is_never_waited_for_even_a_short_wait(monkeypatch):
    """Groq's day is a rolling window: near the cap it names a short wait.
    Waiting those out one after another held the whole cycle."""
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr("jobsearch.llm.requests.post", lambda url, **k: _Resp(429, _TPD))
    monkeypatch.setattr("jobsearch.llm.time.sleep", lambda s: pytest.fail("waited on a daily limit"))
    ans = LLM(providers=["groq"]).ask_json("s", "u")
    assert not ans.ok and ans.limited == "Groq: tokens per day (TPD) - try again in 40.3s"


class Limited(JudgeAll):
    """Answers `good` requests, then is refused for the day."""

    def __init__(self, good=0):
        super().__init__()
        self.good = good

    def ask_json(self, system, user, max_tokens=900, temperature=0.2, cached_prefix=""):
        if self.good:
            self.good -= 1
            return super().ask_json(system, user, max_tokens, temperature, cached_prefix)
        self.prompts.append(user)
        return Answer(ok=False, limited="Groq: tokens per day (TPD) - try again in 33m",
                      failures=[{"provider": "groq", "error": "HTTP 429"}])


def test_a_daily_limit_stops_the_run_and_keeps_what_it_got(results):
    root, path = results
    llm = Limited(good=1)
    res = apply_to_results(root, llm=llm)
    assert len(llm.prompts) == 2                          # one answered, one refused, no more asked
    assert res["ranked"] == BATCH and res["stopped"].startswith("Groq: tokens per day")
    assert res["left"] == 9 - BATCH and len(unread(json.loads(path.read_text()))) == 9 - BATCH
    assert all("ai_tries" not in p for p in json.loads(path.read_text())["postings"].values())


def test_the_time_budget_ends_the_run_between_requests(results, monkeypatch):
    """After a week asleep, 167 postings were waiting; reading them all in one
    cycle held the watcher and the inbox. The rest wait for the next cycle."""
    root, path = results
    clock = [0.0]
    monkeypatch.setattr("jobsearch.rank.time.monotonic", lambda: clock[0])

    class Slow(JudgeAll):
        def ask_json(self, *a, **k):
            clock[0] += 50                                 # each request takes 50 s
            return super().ask_json(*a, **k)

    llm = Slow()
    res = apply_to_results(root, llm=llm, budget=60)       # starts at 0 and 50; not at 100
    assert len(llm.prompts) == 2 and res["stopped"] == "time budget"
    assert res["ranked"] == 2 * BATCH and res["left"] == 9 - 2 * BATCH
    assert res["unranked"] == []                           # not reached is not "dropped by the model"
    nxt = JudgeAll()
    apply_to_results(root, llm=nxt)
    assert len(nxt.seen) == 9 - 2 * BATCH                  # the next run picks up the rest


def test_each_answer_is_on_disk_before_the_next_request(results):
    """The PC slept mid-run and nothing had been written: a run that is cut
    short must keep every judgement it already has."""
    root, path = results

    class Peek(JudgeAll):
        def __init__(self):
            super().__init__()
            self.on_disk = []

        def ask_json(self, *a, **k):
            blob = json.loads(path.read_text())
            self.on_disk.append(sum(1 for p in blob["postings"].values()
                                    if (p.get("ai") or {}).get("by") == "stub-1 via stub"))
            return super().ask_json(*a, **k)

    llm = Peek()
    apply_to_results(root, llm=llm)
    assert llm.on_disk == [0, BATCH, 2 * BATCH]


# ── Claude checks what Groq read; Groq reads what no one has ────────────────

from jobsearch.llm import KNOWN  # noqa: E402


class JudgeAs(JudgeAll):
    """JudgeAll that answers as a named provider, with a fixed fit."""

    def __init__(self, name, fit="possible"):
        super().__init__()
        self.chain, self.name, self.fit = [KNOWN[name]], name, fit

    def ask_json(self, system, user, max_tokens=900, temperature=0.2, cached_prefix=""):
        import re
        self.prompts.append(cached_prefix + user)
        ids = re.findall(r"^### id: (\S+)", user, re.M)
        self.seen += ids
        return Answer(ok=True, provider=self.name, model=f"{self.name}-model", data={"rankings": [
            {"id": i, "fit": self.fit, "why": "x"} for i in ids]})


def _by(provider, fit):
    return {"fit": fit, "why": "w", "by": f"m via {provider}", "prompt": PROMPT_VERSION}


def test_a_weaker_models_strong_is_read_again_first_and_its_weak_stands():
    blob = {"postings": {
        "gs": _posting("gs", verdict="keep", score=1, ai=_by("groq", "strong")),
        "gp": _posting("gp", verdict="keep", score=9, ai=_by("groq", "possible")),
        "gw": _posting("gw", verdict="keep", score=9, ai=_by("groq", "weak")),
        "new": _posting("new", verdict="keep", score=5),
        "cs": _posting("cs", verdict="keep", score=9, ai=_by("claude", "strong"))}}
    assert [p["id"] for p in unread(blob, lead="claude")] == ["gs", "new", "gp"]
    assert [p["id"] for p in unread(blob, lead="groq")] == ["new"]          # nothing to supersede
    assert [p["id"] for p in unread(blob)] == ["new"]                       # no lead given: as before


def test_two_passes_groq_reads_the_new_and_claude_checks_its_strong(results):
    """Claude's budget is about eight readings a day against ~40 new postings:
    alone it would leave new postings piling up unread."""
    root, path = results
    groq, claude = JudgeAs("groq", fit="strong"), JudgeAs("claude", fit="possible")
    res = apply_to_results(root, llm=claude, first_llm=groq)
    assert sorted(groq.seen) == sorted(str(i) for i in range(9))           # the never-read
    assert sorted(claude.seen) == sorted(str(i) for i in range(9))         # then its strong, checked
    after = json.loads(path.read_text())["postings"]
    assert all(after[str(i)]["ai"]["by"] == "claude-model via claude" for i in range(9))
    assert after["read"]["ai"]["fit"] == "strong"            # judged by an unlisted model: left alone
    assert res["ranked"] == 18 and res["attribution"] == "groq-model via groq + claude-model via claude"


def test_the_first_pass_gets_half_the_time(results, monkeypatch):
    root, _ = results
    clock = [0.0]
    monkeypatch.setattr("jobsearch.rank.time.monotonic", lambda: clock[0])

    class Slow(JudgeAs):
        def ask_json(self, *a, **k):
            clock[0] += 50                                                # each request takes 50 s
            return super().ask_json(*a, **k)

    groq, claude = Slow("groq", fit="strong"), Slow("claude")
    apply_to_results(root, llm=claude, first_llm=groq, budget=120)
    # Groq starts at 0 and 50, not at 100 (its half is 60); Claude at 100, not at 150.
    assert len(groq.prompts) == 2 and len(claude.prompts) == 1
