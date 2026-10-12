"""Which model does each job: data/models.yaml."""
from __future__ import annotations

from jobsearch.limits import load_limits, price_for
from jobsearch.llm import KNOWN, LLM, load_routes
from jobsearch.rank import ranking_lead
from samples import ROOT


def _chain(llm):
    return [(p.name, p.model) for p in llm.chain]


def test_every_job_names_a_provider_with_limits_and_a_priced_model():
    """A provider gets limits before it gets a request; a Claude model gets a
    price - or the job would be refused at its first request."""
    limits = load_limits(ROOT)
    routes = load_routes(ROOT)
    assert {"rank", "resume", "setup", "outreach"} <= set(routes)
    for job, route in routes.items():
        for provider, model in route.items():
            assert provider in KNOWN, (job, provider)
            assert limits.get(provider, {}).get("requests_per_day"), (job, provider)
            if provider == "claude":
                assert price_for(limits["claude"], model or KNOWN["claude"].default_model), (job, model)


def test_each_job_gets_its_own_model(prof, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "openrouter")
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    assert _chain(LLM(prof, purpose="rank")) == [("claude", "claude-haiku-5-5")]
    assert _chain(LLM(prof, purpose="resume")) == [("claude", "claude-sonnet-5-5")]
    assert _chain(LLM(prof, purpose="setup")) == [("claude", "claude-opus-5-5")]
    assert _chain(LLM(prof, purpose="outreach")) == [("groq", "openai/gpt-oss-120b")]
    # A job with no route, and a request with no job, follow LLM_PROVIDERS.
    assert [p.name for p in LLM(prof, purpose="other").chain] == ["openrouter"]
    assert [p.name for p in LLM(prof).chain] == ["openrouter"]


def test_a_pinned_chain_wins_over_the_route(prof):
    """The inbox's emails go to Groq only, whatever a route says."""
    assert [p.name for p in LLM(prof, providers=["groq"], purpose="rank").chain] == ["groq"]
    # The first pass of ranking: Groq, at its own default model.
    assert _chain(LLM(prof, providers=["groq"], purpose="rank")) == [("groq", KNOWN["groq"].model)]


def test_the_route_leads_ranking(prof, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "groq")
    assert ranking_lead(prof) == "claude"
    (prof.root / "data" / "models.yaml").write_text("resume: {claude: claude-sonnet-5-5}\n", encoding="utf-8")
    assert ranking_lead(prof) == "groq"                      # no "rank" route: LLM_PROVIDERS


def test_no_models_file_means_llm_providers_as_before(prof, monkeypatch):
    (prof.root / "data" / "models.yaml").unlink()
    monkeypatch.setenv("LLM_PROVIDERS", "groq,claude")
    assert [p.name for p in LLM(prof, purpose="resume").chain] == ["groq", "claude"]
