"""Models as an ordered chain: open-weight ones behind OpenAI-compatible
endpoints (Groq, OpenRouter), and Claude through Anthropic's own SDK.

Try the first healthy provider, fall back on failure, and when nothing is configured say so loudly rather than silently
producing worse output. LLM_PROVIDERS sets the chain; "claude" alone means a
refused or capped request waits rather than going to a weaker model.

**The pipeline must work without this.** Discovery, watching and triage passes
1 and 2 never call it. A missing key degrades the system; it does not break it,
and the dashboard says which happened.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from .limits import Meter, cost_usd, estimate_tokens, load_limits
from .profile import Profile


def load_dotenv(path: Path) -> None:
    """Minimal .env reader. Values already in the environment win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    base_url: str
    key_env: str
    model_env: str
    default_model: str

    @property
    def key(self) -> str | None:
        return os.environ.get(self.key_env) or None

    @property
    def model(self) -> str:
        return os.environ.get(self.model_env) or self.default_model

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "model": self.model,
            "configured": self.configured,
        }


KNOWN: dict[str, Provider] = {
    "groq": Provider(
        name="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        key_env="GROQ_API_KEY",
        model_env="GROQ_MODEL",
        default_model="openai/gpt-oss-120b",
    ),
    "openrouter": Provider(
        name="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        key_env="OPENROUTER_API_KEY",
        model_env="OPENROUTER_MODEL",
        default_model="meta-llama/llama-3.3-70b-instruct",
    ),
    # Through the official SDK, not an OpenAI-compatible shim (_ask_claude).
    "claude": Provider(
        name="claude",
        label="Claude",
        base_url="https://api.anthropic.com",
        key_env="ANTHROPIC_API_KEY",
        model_env="CLAUDE_MODEL",
        default_model="claude-opus-5-5",
    ),
}

# Claude Opus 5.5 always thinks; effort sets how much. Ranking is judgement
# over a short list, and every thinking token is billed as output - "low"
# unless CLAUDE_EFFORT says otherwise.
CLAUDE_EFFORT = "low"
# Thinking shares max_tokens with the answer. Room on top of what the caller
# asked for the answer, so a long think doesn't cut the JSON off.
CLAUDE_THINKING_ROOM = 6000
CLAUDE_MAX_TOKENS = 16000          # non-streaming requests stay under this
# A request declined by a safety classifier is re-run on Anthropic's
# recommended model for that category, server side. Job postings shouldn't
# trip one; if one does, the answer still comes back.
CLAUDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class Answer:
    ok: bool
    data: dict[str, Any] | None = None
    text: str = ""
    provider: str | None = None
    model: str | None = None
    failures: list[dict[str, str]] = field(default_factory=list)
    # Set when a rate limit ended the call ("Groq: tokens per day, try again
    # in 33m7s"). A run of many requests stops on it: the next request would
    # be refused the same way.
    limited: str = ""
    # Why a run of many requests ended before its last one - a limit, or the
    # caller's time budget. Blank when it ran to the end.
    stopped: str = ""

    @property
    def attribution(self) -> str:
        """What to show a human. Never blank - "No AI involved" is information."""
        if self.provider:
            return f"{self.model} via {self.provider}"
        return "No AI involved"


# Groq's free tier allows 8,000 tokens a minute. A bulk re-rank hit that on
# every other request and fell through to OpenRouter - a paid model, which
# spent the credits until it answered 402. A 429 that says how long to wait is
# waited out, up to this long, a few times, before the next provider.
RATE_WAIT_MAX = 60.0
RATE_WAITS = 4
# A per-day limit is never waited for, however short the wait it names. Groq
# counts the day as a rolling window, so near the cap the wait it names can be
# short - and waiting out one after another would hold the whole cycle
# (watcher, inbox) to the pace the day's budget frees up.
_DAILY = re.compile(r"per day|\((?:TPD|RPD)\)", re.I)


def _limit_note(p: "Provider", r: Any) -> str:
    """"Groq: tokens per day (TPD) - try again in 33m7s", from a 429."""
    text = r.text or ""
    which = re.search(r"on ((?:tokens|requests) per (?:minute|day)(?: \(\w+\))?)", text)
    when = re.search(r"try again in (\d[\dms.]*[ms])", text)
    return (f"{p.label}: {which.group(1) if which else 'rate limit'}"
            + (f" - try again in {when.group(1)}" if when else ""))


def _retry_after(r: Any) -> float | None:
    """Seconds a 429 asks us to wait: the Retry-After header, else the
    "try again in 7.2s" / "450ms" / "1m2s" in the message."""
    try:
        return float(r.headers.get("retry-after"))
    except (TypeError, ValueError):
        pass
    m = re.search(r"try again in ((?:\d+(?:\.\d+)?(?:ms|s|m))+)", r.text or "")
    if not m:
        return None
    total = 0.0
    for n, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|s|m)", m.group(1)):
        total += float(n) * {"ms": 0.001, "s": 1.0, "m": 60.0}[unit]
    return total


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull an object out of a reply that may be fenced or prefaced with prose."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


class LLM:
    def __init__(self, prof: Profile | None = None, timeout: float = 60.0,
                 providers: list[str] | None = None, purpose: str = "") -> None:
        """`providers` pins the chain, ignoring LLM_PROVIDERS: the inbox sends
        email text to Groq only, and must never fall back to anything else.

        With a profile, every request is held to data/llm-limits.yaml and
        counted against that person (see limits.py); `purpose` ("rank",
        "mail", ...) is what the day's tokens are counted under, and "mail"
        may use the share kept for it."""
        self.meter = Meter(prof.root, prof.id) if prof else None
        self.purpose = purpose
        if prof:
            load_dotenv(prof.env)
        order = [
            p.strip().lower()
            for p in (providers if providers is not None
                      else os.environ.get("LLM_PROVIDERS", "groq,openrouter").split(","))
            if p.strip()
        ]
        self.chain = [KNOWN[n] for n in order if n in KNOWN]
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return any(p.configured for p in self.chain)

    def status(self) -> list[dict[str, Any]]:
        if not self.meter:
            return [p.public() for p in self.chain]
        today = self.meter.today()
        return [p.public() | {"today": today.get(p.name)} for p in self.chain]

    def key_check(self) -> tuple[bool, str]:
        """Will any provider in the chain take a request? A GET /models that
        carries no content - so a dead key is found before anything that
        matters is sent. An expired Groq key turned five emails' worth of
        requests into five 401s."""
        why = []
        for p in self.chain:
            if not p.configured:
                continue
            if p.name == "claude":
                ok, reason = _claude_key_check(p, self.timeout)
                if ok:
                    return True, ""
                why.append(reason)
                continue
            try:
                r = requests.get(f"{p.base_url}/models", headers={"Authorization": f"Bearer {p.key}"},
                                 timeout=self.timeout)
            except requests.RequestException as exc:
                why.append(f"{p.label} unreachable ({type(exc).__name__})")
                continue
            if r.status_code in (401, 403):
                why.append(f"{p.label} rejected the API key (HTTP {r.status_code}) - it may have expired")
                continue
            return True, ""
        return False, "; ".join(why) or "no API key configured"

    STRICT = (
        "Reply with a single valid JSON object and nothing else. "
        "No prose, no explanation outside the JSON, no markdown fence."
    )

    def _post(self, p: Provider, system: str, user: str, max_tokens: int,
              temperature: float, strict_format: bool) -> requests.Response:
        body: dict[str, Any] = {
            "model": p.model,
            "messages": [
                {"role": "system", "content": f"{system}\n{self.STRICT}"},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if strict_format:
            body["response_format"] = {"type": "json_object"}
        try:
            r = requests.post(
                f"{p.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {p.key}",
                         "Content-Type": "application/json"},
                json=body,
                timeout=self.timeout,
            )
        except requests.RequestException:
            if getattr(self, "meter", None):
                self.meter.record(p.name, 0, self.purpose)
            raise
        if getattr(self, "meter", None):
            tokens = 0
            if r.status_code == 200:
                try:
                    tokens = int(r.json().get("usage", {}).get("total_tokens") or 0)
                except (ValueError, AttributeError, TypeError):
                    tokens = 0
                tokens = tokens or estimate_tokens(system, user) + max_tokens
            self.meter.record(p.name, tokens, self.purpose)
        return r

    def _post_patient(self, p: Provider, system: str, user: str, max_tokens: int,
                      temperature: float, strict_format: bool) -> requests.Response:
        """_post, waiting out a rate limit when the provider says how long."""
        r = self._post(p, system, user, max_tokens, temperature, strict_format)
        for _ in range(RATE_WAITS):
            if r.status_code != 429:
                return r
            if _DAILY.search(r.text or ""):
                return r           # the day's budget: comes back slowly, if at all today
            delay = _retry_after(r)
            if delay is None or delay > RATE_WAIT_MAX:
                return r           # not worth waiting for
            time.sleep(delay + 0.25)
            r = self._post(p, system, user, max_tokens, temperature, strict_format)
        return r

    def ask_json(
        self, system: str, user: str, max_tokens: int = 900, temperature: float = 0.2,
        cached_prefix: str = "",
    ) -> Answer:
        """One JSON answer from the first provider that manages it.

        `cached_prefix` is the stable start of the user message - ranking's
        profile, the same in every batch. Claude caches it (a cached read costs
        a twentieth of a fresh one); other providers just get it prepended."""
        ans = Answer(ok=False)
        if not self.available:
            ans.failures.append({"provider": "-", "error": "no API key configured"})
            return ans

        for p in self.chain:
            if not p.configured:
                ans.failures.append({"provider": p.name, "error": f"{p.key_env} not set"})
                continue
            meter = getattr(self, "meter", None)
            if meter:
                est_in = estimate_tokens(system, cached_prefix, user)
                usd = 0.0
                if p.name == "claude":
                    caps = load_limits(meter.root).get(p.name) or {}
                    usd = cost_usd(caps, {"input": est_in, "output": min(max_tokens, 2000)})
                why = meter.check(p.name, est_in + max_tokens, self.purpose, usd=usd)
                if why:
                    ans.failures.append({"provider": p.name, "error": why})
                    ans.limited = ans.limited or f"{p.label}: {why}"
                    continue
            if p.name == "claude":
                if self._ask_claude(p, system, user, max_tokens, cached_prefix, ans):
                    return ans
                if ans.limited:
                    break
                continue
            user_full = cached_prefix + user

            # `response_format: json_object` is a guardrail that can itself be
            # the failure: a reasoning model emits thinking tokens and the
            # server rejects the whole completion. Retry the SAME provider
            # without it before falling through - otherwise a strictness
            # setting quietly costs us the faster provider on every call.
            for strict in (True, False):
                try:
                    r = self._post_patient(p, system, user_full, max_tokens, temperature, strict)
                except requests.RequestException as exc:
                    ans.failures.append(
                        {"provider": p.name, "error": f"{type(exc).__name__}"}
                    )
                    break

                if r.status_code != 200:
                    detail = r.text[:160].replace("\n", " ")
                    ans.failures.append(
                        {"provider": p.name,
                         "error": f"HTTP {r.status_code}{' (strict json)' if strict else ''}: {detail}"}
                    )
                    if strict and r.status_code == 400 and "json" in r.text.lower():
                        continue          # same provider, relaxed
                    if r.status_code == 429:
                        ans.limited = _limit_note(p, r)
                    break

                try:
                    text = r.json()["choices"][0]["message"]["content"]
                except (ValueError, KeyError, IndexError) as exc:
                    ans.failures.append(
                        {"provider": p.name, "error": f"bad envelope: {exc}"}
                    )
                    break

                data = _extract_json(text)
                if data is None:
                    ans.failures.append(
                        {"provider": p.name, "error": "reply was not JSON"}
                    )
                    if strict:
                        continue
                    break

                ans.ok, ans.data, ans.text = True, data, text
                ans.provider, ans.model = p.name, p.model
                return ans

        return ans

    def _ask_claude(self, p: Provider, system: str, user: str, max_tokens: int,
                    cached_prefix: str, ans: Answer) -> bool:
        """One request through the Anthropic SDK. Fills `ans`; True on a JSON answer.

        Claude Opus 5.5 takes no temperature and always thinks - `effort`
        decides how much. The stable prefix (system prompt, then the profile)
        is cached: in a run of batches only the first pays for it in full."""
        import anthropic
        client = anthropic.Anthropic(api_key=p.key, timeout=self.timeout, max_retries=2)
        first = [{"type": "text", "text": cached_prefix, "cache_control": {"type": "ephemeral"}}] \
            if cached_prefix else []
        system_block = {"type": "text", "text": f"{system}\n{self.STRICT}"}
        if not cached_prefix:
            system_block["cache_control"] = {"type": "ephemeral"}
        try:
            resp = client.beta.messages.create(
                model=p.model,
                max_tokens=min(max_tokens + CLAUDE_THINKING_ROOM, CLAUDE_MAX_TOKENS),
                system=[system_block],
                messages=[{"role": "user", "content": [*first, {"type": "text", "text": user}]}],
                output_config={"effort": os.environ.get("CLAUDE_EFFORT") or CLAUDE_EFFORT},
                betas=[CLAUDE_FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            self._claude_spent(p, None)
            ans.failures.append({"provider": p.name, "error": "HTTP 401: Claude rejected the API key"})
            return False
        except anthropic.RateLimitError as exc:
            self._claude_spent(p, None)
            ans.failures.append({"provider": p.name, "error": f"HTTP 429: {str(exc)[:120]}"})
            ans.limited = "Claude: rate limited (HTTP 429)"
            return False
        except anthropic.APIStatusError as exc:
            self._claude_spent(p, None)
            # 402-style "credit balance is too low" arrives as a 400.
            text = str(getattr(exc, "message", "") or exc)
            ans.failures.append({"provider": p.name, "error": f"HTTP {exc.status_code}: {text[:140]}"})
            if "credit balance" in text.lower():
                ans.limited = "Claude: the account's credit has run out"
            return False
        except anthropic.APIConnectionError as exc:
            self._claude_spent(p, None)
            ans.failures.append({"provider": p.name, "error": type(exc).__name__})
            return False
        self._claude_spent(p, resp.usage)
        if resp.stop_reason == "refusal":
            cat = getattr(resp.stop_details, "category", None) if resp.stop_details else None
            ans.failures.append({"provider": p.name, "error": f"declined by a safety classifier ({cat})"})
            return False
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        if resp.stop_reason == "max_tokens":
            ans.failures.append({"provider": p.name, "error": "reply cut off at max_tokens"})
            return False
        data = _extract_json(text)
        if data is None:
            ans.failures.append({"provider": p.name, "error": "reply was not JSON"})
            return False
        ans.ok, ans.data, ans.text = True, data, text
        ans.provider, ans.model = p.name, resp.model or p.model
        return True

    def _claude_spent(self, p: Provider, usage: Any) -> None:
        meter = getattr(self, "meter", None)
        if not meter:
            return
        if usage is None:
            meter.record(p.name, 0, self.purpose)
            return
        counts = {"input": usage.input_tokens or 0, "output": usage.output_tokens or 0,
                  "cache_write": usage.cache_creation_input_tokens or 0,
                  "cache_read": usage.cache_read_input_tokens or 0}
        caps = load_limits(meter.root).get(p.name) or {}
        meter.record(p.name, sum(counts.values()), self.purpose, usd=cost_usd(caps, counts))


def _claude_key_check(p: Provider, timeout: float) -> tuple[bool, str]:
    """Lists one model - free, and carries nothing of ours."""
    import anthropic
    try:
        anthropic.Anthropic(api_key=p.key, timeout=timeout, max_retries=1).models.list(limit=1)
    except anthropic.AuthenticationError:
        return False, "Claude rejected the API key (HTTP 401)"
    except anthropic.APIStatusError as exc:
        return False, f"Claude answered HTTP {exc.status_code}"
    except anthropic.APIConnectionError as exc:
        return False, f"Claude unreachable ({type(exc).__name__})"
    return True, ""
