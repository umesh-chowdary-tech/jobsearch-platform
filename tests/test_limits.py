"""Every provider has a daily cap, checked before a request is sent."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobsearch.limits import Meter, load_limits
from jobsearch.llm import LLM
from jobsearch.profile import Profile

ROOT = Path(__file__).resolve().parents[1]


def P(root: Path, pid: str = "someone") -> Profile:
    return Profile(root, pid)


def usage_file(root: Path) -> Path:
    return root / "var" / "_shared" / "llm-usage.json"


@pytest.fixture
def root(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "llm-limits.yaml").write_text(
        "groq: {requests_per_day: 3, tokens_per_day: 10000, keep_for_mail: 4000}\n"
        "openrouter: {requests_per_day: 0, tokens_per_day: 0}\n"
        "jev: {requests_per_day: 100, tokens_per_day: 100000, tokens_per_month: 150000}\n")
    return tmp_path


def test_every_provider_the_app_can_call_has_a_limit():
    from jobsearch.llm import KNOWN
    caps = load_limits(ROOT)
    assert set(KNOWN) | {"jev"} <= set(caps)
    assert caps["openrouter"]["requests_per_day"] == 0                 # paid; off


def test_a_provider_with_no_limit_is_not_used(root):
    assert "no limit set for mystery" in Meter(root).check("mystery", 10)


def test_zero_turns_a_provider_off(root):
    assert Meter(root).check("openrouter", 10) == "openrouter is off in data/llm-limits.yaml"


def test_the_request_cap(root):
    m = Meter(root)
    for _ in range(3):
        assert m.check("groq", 10) == ""
        m.record("groq", 10)
    assert "daily request limit reached (3 of 3" in m.check("groq", 10)


def test_ranking_leaves_the_mails_share(root):
    """The day's tokens can't all go on re-rating postings."""
    m = Meter(root)
    m.record("groq", 5500, "rank")
    assert "4,000 kept for the inbox" in m.check("groq", 1000, "rank")    # 5500+1000 > 10000-4000
    assert m.check("groq", 1000, "mail") == ""


def test_the_month_cap(root, monkeypatch):
    import datetime as dt

    class Day(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 7)
    monkeypatch.setattr("jobsearch.limits.date", Day)
    usage_file(root).parent.mkdir(parents=True, exist_ok=True)
    usage_file(root).write_text(json.dumps({"days": {
        "2026-09-30": {"jev": {"requests": 1, "tokens": 900000}},          # last month: not counted
        "2026-10-02": {"jev": {"requests": 1, "tokens": 90000}},
        "2026-10-07": {"jev": {"requests": 1, "tokens": 50000}}}}))
    m = Meter(root)
    assert m.month_tokens("jev") == 140000
    assert m.check("jev", 5000) == ""
    assert "monthly token limit reached (140,000 of 150,000" in m.check("jev", 20000)


# ── the client: refused before sending, counted after ───────────────────────

class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text, self.headers = status, body, text, {}

    def json(self):
        return self._body


def test_over_the_cap_nothing_is_sent(root, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr("jobsearch.llm.requests.post", lambda *a, **k: pytest.fail("sent past the cap"))
    Meter(root).record("groq", 9000, "rank")
    ans = LLM(P(root), providers=["groq"], purpose="rank").ask_json("s", "u", max_tokens=500)
    assert not ans.ok and ans.limited.startswith("Groq: daily token limit reached")


def test_the_providers_own_count_is_recorded(root, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    ok = _Resp(200, {"choices": [{"message": {"content": '{"a": 1}'}}], "usage": {"total_tokens": 1234}})
    monkeypatch.setattr("jobsearch.llm.requests.post", lambda *a, **k: ok)
    assert LLM(P(root), providers=["groq"], purpose="mail").ask_json("s", "u", max_tokens=500).ok
    assert Meter(root).used("groq") == {"requests": 1, "tokens": 1234}
    day = json.loads(usage_file(root).read_text())["days"]
    assert next(iter(day.values()))["groq"]["by_purpose"] == {"mail": 1234}
    assert next(iter(day.values()))["groq"]["by_profile"] == {"someone": {"requests": 1, "tokens": 1234}}


def test_a_refused_request_still_counts_as_a_request(root, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr("jobsearch.llm.requests.post",
                        lambda *a, **k: _Resp(429, text="on tokens per day (TPD): try again in 9m"))
    LLM(P(root), providers=["groq"]).ask_json("s", "u", max_tokens=100)
    assert Meter(root).used("groq") == {"requests": 1, "tokens": 0}


# ── each person's share ─────────────────────────────────────────────────────

def _per_profile(root):
    (root / "data" / "llm-limits.yaml").write_text(
        "groq: {requests_per_day: 100, tokens_per_day: 100000,\n"
        "       per_profile: {requests_per_day: 2, tokens_per_day: 5000}}\n"
        "claude: {requests_per_day: 100, tokens_per_day: 1000000, usd_per_day: 1.0,\n"
        "         per_profile: {usd_per_day: 0.10}}\n")
    return root


def test_one_profile_cannot_spend_everyones_day(root):
    """Each person is held to their own share; the next person still has theirs."""
    _per_profile(root)
    a, b = Meter(root, "asha"), Meter(root, "ravi")
    a.record("groq", 10)
    a.record("groq", 10)
    assert "asha's daily request limit reached (2 of 2" in a.check("groq", 10)
    assert b.check("groq", 10) == ""
    assert Meter(root).check("groq", 10) == ""                     # no profile: the provider's cap only
    b.record("groq", 4900)
    assert "ravi's daily token limit reached" in b.check("groq", 200)


def test_a_profiles_dollars_are_capped_too(root):
    _per_profile(root)
    m = Meter(root, "asha")
    m.record("claude", 1000, "rank", usd=0.09)
    assert m.check("claude", 1000, "outreach", usd=0.005) == ""
    assert "asha's daily spend limit reached ($0.09 of $0.10" in m.check("claude", 1000, "outreach", usd=0.02)
    assert Meter(root, "ravi").check("claude", 1000, "outreach", usd=0.02) == ""


def test_todays_usage_shows_the_profiles_share(root):
    _per_profile(root)
    Meter(root, "asha").record("groq", 300, "rank")
    Meter(root, "ravi").record("groq", 200, "rank")
    row = Meter(root, "asha").today()["groq"]
    assert row["tokens"] == 500 and row["profile"] == {"requests": 1, "tokens": 300, "usd": 0.0}


def test_the_shipped_limits_cap_every_paid_provider_per_profile():
    caps = load_limits(ROOT)
    for name, c in caps.items():
        if c.get("requests_per_day"):
            assert c.get("per_profile"), f"{name} has no per_profile cap"


# ── dollars: Claude is prepaid ──────────────────────────────────────────────

def _usd_root(root):
    (root / "data" / "llm-limits.yaml").write_text(
        "claude: {requests_per_day: 100, tokens_per_day: 1000000, usd_per_day: 0.15, usd_per_month: 4.0,\n"
        "         keep_usd_for_clicks: 0.03,\n"
        "         price_per_mtok: {input: 4.0, output: 20.0, cache_write: 5.0, cache_read: 0.2}}\n")
    return root


def test_the_cost_of_a_request_comes_from_its_reported_tokens():
    from jobsearch.limits import cost_usd
    caps = {"price_per_mtok": {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.2}}
    assert round(cost_usd(caps, {"input": 2600, "output": 2300, "cache_write": 2400, "cache_read": 0}), 4) == 0.0684


def test_ranking_stops_short_of_the_days_dollars_and_a_click_does_not(root):
    m = Meter(_usd_root(root))
    m.record("claude", 5000, "rank", usd=0.10)
    assert "$0.03 kept for drafts" in m.check("claude", 5000, "rank", usd=0.03)
    assert m.check("claude", 5000, "outreach", usd=0.03) == ""
    assert "daily spend limit reached" in m.check("claude", 5000, "outreach", usd=0.06)


def test_the_month_of_dollars(root, monkeypatch):
    import datetime as dt

    class Day(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 20)
    monkeypatch.setattr("jobsearch.limits.date", Day)
    usage_file(root).parent.mkdir(parents=True, exist_ok=True)
    usage_file(root).write_text(json.dumps({"days": {
        f"2026-10-{d:02d}": {"claude": {"requests": 2, "tokens": 9000, "usd": 0.21}} for d in range(1, 20)}}))
    assert "monthly spend limit reached ($3.99 of $4.00" in Meter(_usd_root(root)).check(
        "claude", 100, "outreach", usd=0.02)


# ── the Claude client, with the SDK faked ───────────────────────────────────

class _Usage:
    def __init__(self, i, o, cw=0, cr=0):
        self.input_tokens, self.output_tokens = i, o
        self.cache_creation_input_tokens, self.cache_read_input_tokens = cw, cr


class _Block:
    def __init__(self, kind, text=""):
        self.type, self.text, self.thinking = kind, text, ""


class _Msg:
    def __init__(self, text, stop="end_turn", usage=None):
        self.content = [_Block("thinking"), _Block("text", text)]
        self.stop_reason, self.stop_details, self.model = stop, None, "claude-opus-5-5"
        self.usage = usage or _Usage(2600, 2300, 2400, 0)


class _FakeAnthropic:
    sent: list = []
    reply = None

    def __init__(self, **kw):
        self.beta = self
        self.messages = self

    def create(self, **kw):
        _FakeAnthropic.sent.append(kw)
        r = _FakeAnthropic.reply
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def claude(root, monkeypatch):
    import anthropic
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("CLAUDE_EFFORT", raising=False)
    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)
    _FakeAnthropic.sent = []
    return _usd_root(root)


def test_claude_answers_through_the_sdk_and_its_cost_is_counted(claude):
    _FakeAnthropic.reply = _Msg('{"rankings": []}')
    ans = LLM(P(claude), providers=["claude"], purpose="rank").ask_json(
        "sys", "postings", max_tokens=4000, cached_prefix="PROFILE")
    assert ans.ok and ans.data == {"rankings": []} and ans.attribution == "claude-opus-5-5 via claude"
    kw = _FakeAnthropic.sent[0]
    assert kw["model"] == "claude-opus-5-5" and "temperature" not in kw         # Opus 5.5 takes none
    assert kw["messages"][0]["content"][0] == {"type": "text", "text": "PROFILE",
                                               "cache_control": {"type": "ephemeral"}}
    assert kw["fallbacks"] == "default" and kw["output_config"] == {"effort": "low"}
    assert Meter(claude).used("claude") == {"requests": 1, "tokens": 7300, "usd": 0.0684}


def test_claude_is_not_called_past_the_days_dollars(claude):
    Meter(claude).record("claude", 1, "rank", usd=0.12)
    _FakeAnthropic.reply = AssertionError("sent past the cap")
    ans = LLM(P(claude), providers=["claude"], purpose="rank").ask_json("sys", "x" * 4000, max_tokens=4000)
    assert not ans.ok and ans.limited.startswith("Claude: daily spend limit reached")
    assert _FakeAnthropic.sent == []


def test_a_declined_or_cut_off_reply_is_not_an_answer(claude):
    _FakeAnthropic.reply = _Msg("", stop="refusal")
    assert "declined" in LLM(P(claude), providers=["claude"]).ask_json("s", "u").failures[-1]["error"]
    _FakeAnthropic.reply = _Msg('{"rankings": [', stop="max_tokens")
    assert "cut off" in LLM(P(claude), providers=["claude"]).ask_json("s", "u").failures[-1]["error"]


def test_no_credit_left_stops_the_run(claude):
    import anthropic
    import httpx2
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx2.Response(400, request=req, json={"error": {"message": "Your credit balance is too low"}})
    _FakeAnthropic.reply = anthropic.BadRequestError("Your credit balance is too low", response=resp, body=None)
    ans = LLM(P(claude), providers=["claude"]).ask_json("s", "u")
    assert ans.limited == "Claude: the account's credit has run out"
