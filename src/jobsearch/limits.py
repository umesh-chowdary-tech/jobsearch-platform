"""A daily cap on every model provider, checked before a request is sent.

The providers enforce their own limits, but only by refusing - after the
request. Groq's free tier answered "429" once per cycle after its daily budget
ran out; OpenRouter, a paid fallback, answered "402" only after the credits were
spent. Jev bills from the first token. So each request is counted here, and one
that would pass the day's cap in data/llm-limits.yaml is never made.

A provider missing from that file is not used at all: a new provider gets a
limit before it gets a request.

Each request is also counted against the profile it was made for, and a
provider's `per_profile` caps hold each person to their own share of the day -
so one busy profile can't spend everyone's budget, and the cost per person is
known from the first request.

The counts live in var/_shared/llm-usage.json, by local day and provider. Two
processes (the scheduled cycle and the app) both send requests, so the file is
read and written under its own short lock.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterator

import yaml

from .busy import _try_lock, _unlock
from .profile import shared_var

KEEP_DAYS = 40
# The purpose that `keep_for_mail` is kept for.
MAIL = "mail"


def load_limits(root: Path) -> dict[str, dict[str, Any]]:
    p = Path(root) / "data" / "llm-limits.yaml"
    if not p.exists():
        return {}
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {str(k): dict(v or {}) for k, v in doc.items()}


def cost_usd(caps: dict[str, Any], usage: dict[str, int]) -> float:
    """Dollars for one request, from the provider's `price_per_mtok` and the
    token counts it reported (input, output, cache_write, cache_read)."""
    price = caps.get("price_per_mtok") or {}
    return sum(float(price.get(k, 0)) * int(usage.get(k, 0) or 0) for k in price) / 1e6


@contextmanager
def _locked(var: Path, timeout: float = 10.0) -> Iterator[None]:
    var.mkdir(parents=True, exist_ok=True)
    fd = os.open(var / ".usage.lock", os.O_RDWR | os.O_CREAT)
    deadline = time.monotonic() + timeout
    try:
        while not _try_lock(fd):
            if time.monotonic() > deadline:
                raise TimeoutError(f"{var / '.usage.lock'} held too long")
            time.sleep(0.02)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


class Meter:
    """`check` before a request, `record` after it. `who` is the profile the
    requests are for: its share is counted, and held to `per_profile`."""

    def __init__(self, root: Path, who: str = "") -> None:
        self.root = Path(root)
        self.who = who
        self.var = shared_var(self.root)
        self.path = self.var / "llm-usage.json"

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"days": {}}

    def used(self, provider: str, day: str | None = None) -> dict[str, Any]:
        day = day or date.today().isoformat()
        got = self._load()["days"].get(day, {}).get(provider) or {}
        out: dict[str, Any] = {"requests": int(got.get("requests", 0)), "tokens": int(got.get("tokens", 0))}
        if "usd" in got:
            out["usd"] = round(float(got["usd"]), 6)
        return out

    def used_by(self, provider: str, who: str, day: str | None = None) -> dict[str, Any]:
        """One profile's share of a provider's day."""
        day = day or date.today().isoformat()
        got = ((self._load()["days"].get(day, {}).get(provider) or {}).get("by_profile") or {}).get(who) or {}
        return {"requests": int(got.get("requests", 0)), "tokens": int(got.get("tokens", 0)),
                "usd": round(float(got.get("usd", 0)), 6)}

    def month_tokens(self, provider: str) -> int:
        month = date.today().isoformat()[:7]
        return sum(int((v.get(provider) or {}).get("tokens", 0))
                   for d, v in self._load()["days"].items() if d.startswith(month))

    def month_usd(self, provider: str) -> float:
        month = date.today().isoformat()[:7]
        return sum(float((v.get(provider) or {}).get("usd", 0))
                   for d, v in self._load()["days"].items() if d.startswith(month))

    def month_by_profile(self) -> dict[str, dict[str, Any]]:
        """Each profile's requests, tokens and dollars this month, every
        provider together - the hosted service's cost-per-person page."""
        month = date.today().isoformat()[:7]
        out: dict[str, dict[str, Any]] = {}
        for d, providers in self._load()["days"].items():
            if not d.startswith(month):
                continue
            for got in providers.values():
                for who, u in (got.get("by_profile") or {}).items():
                    row = out.setdefault(who, {"requests": 0, "tokens": 0, "usd": 0.0})
                    row["requests"] += int(u.get("requests", 0))
                    row["tokens"] += int(u.get("tokens", 0))
                    row["usd"] = round(row["usd"] + float(u.get("usd", 0)), 6)
        return out

    def check(self, provider: str, tokens: int, purpose: str = "", usd: float = 0.0) -> str:
        """"" when a request of about `tokens` (and `usd`, for a provider
        priced in dollars) may be sent; else why not."""
        caps = load_limits(self.root).get(provider)
        if caps is None:
            return f"no limit set for {provider} in data/llm-limits.yaml - not used until one is"
        u = self.used(provider)
        rpd = caps.get("requests_per_day", 0)
        if u["requests"] + 1 > rpd:
            return (f"{provider} is off in data/llm-limits.yaml" if rpd == 0 else
                    f"daily request limit reached ({u['requests']} of {rpd}, data/llm-limits.yaml)")
        tpd = caps.get("tokens_per_day", 0)
        keep = caps.get("keep_for_mail", 0) if purpose != MAIL else 0
        if u["tokens"] + tokens > tpd - keep:
            if tpd == 0:
                return f"{provider} is off in data/llm-limits.yaml"
            held = f", {keep:,} kept for the inbox" if keep else ""
            return f"daily token limit reached ({u['tokens']:,} of {tpd:,}{held}, data/llm-limits.yaml)"
        tpm = caps.get("tokens_per_month")
        if tpm is not None and self.month_tokens(provider) + tokens > tpm:
            return f"monthly token limit reached ({self.month_tokens(provider):,} of {tpm:,}, data/llm-limits.yaml)"
        upd = caps.get("usd_per_day")
        # Ranking runs every cycle; drafting a resume or an outreach note is a
        # click. Ranking stops short by `keep_usd_for_clicks`, so a click on a
        # busy day still gets an answer.
        held_usd = float(caps.get("keep_usd_for_clicks", 0)) if purpose == "rank" else 0.0
        if upd is not None and float(u.get("usd", 0)) + usd > float(upd) - held_usd:
            held = f", ${held_usd:.2f} kept for drafts" if held_usd else ""
            return (f"daily spend limit reached (${float(u.get('usd', 0)):.2f} of ${float(upd):.2f}{held},"
                    f" data/llm-limits.yaml)")
        upm = caps.get("usd_per_month")
        if upm is not None and self.month_usd(provider) + usd > float(upm):
            return (f"monthly spend limit reached (${self.month_usd(provider):.2f} of ${float(upm):.2f},"
                    f" data/llm-limits.yaml)")
        return self._check_share(provider, caps, tokens, usd)

    def _check_share(self, provider: str, caps: dict[str, Any], tokens: int, usd: float) -> str:
        """The profile's own cap for the day, from `per_profile`."""
        pp = caps.get("per_profile") or {}
        if not (self.who and pp):
            return ""
        u = self.used_by(provider, self.who)
        where = "per_profile in data/llm-limits.yaml"
        if "requests_per_day" in pp and u["requests"] + 1 > int(pp["requests_per_day"]):
            return f"{self.who}'s daily request limit reached ({u['requests']} of {pp['requests_per_day']}, {where})"
        if "tokens_per_day" in pp and u["tokens"] + tokens > int(pp["tokens_per_day"]):
            return (f"{self.who}'s daily token limit reached ({u['tokens']:,} of {int(pp['tokens_per_day']):,},"
                    f" {where})")
        if "usd_per_day" in pp and u["usd"] + usd > float(pp["usd_per_day"]):
            return (f"{self.who}'s daily spend limit reached (${u['usd']:.2f} of ${float(pp['usd_per_day']):.2f},"
                    f" {where})")
        return ""

    def record(self, provider: str, tokens: int, purpose: str = "", usd: float = 0.0) -> None:
        """One request sent, and the tokens (and dollars) it cost - 0 when it
        was refused."""
        today = date.today().isoformat()
        with _locked(self.var):
            blob = self._load()
            days = blob.setdefault("days", {})
            row = days.setdefault(today, {}).setdefault(provider, {"requests": 0, "tokens": 0})
            row["requests"] += 1
            row["tokens"] += max(0, int(tokens))
            if usd:
                row["usd"] = round(float(row.get("usd", 0)) + float(usd), 6)
            if purpose:
                by = row.setdefault("by_purpose", {})
                by[purpose] = by.get(purpose, 0) + max(0, int(tokens))
            if self.who:
                mine = row.setdefault("by_profile", {}).setdefault(self.who, {"requests": 0, "tokens": 0})
                mine["requests"] += 1
                mine["tokens"] += max(0, int(tokens))
                if usd:
                    mine["usd"] = round(float(mine.get("usd", 0)) + float(usd), 6)
            for d in sorted(days)[:-KEEP_DAYS]:
                days.pop(d)
            text = json.dumps(blob, indent=1, sort_keys=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            for _ in range(5):          # Windows won't replace a file a reader has open
                try:
                    os.replace(tmp, self.path)
                    break
                except PermissionError:
                    time.sleep(0.05)
            else:
                self.path.write_text(text, encoding="utf-8")
                tmp.unlink(missing_ok=True)

    def today(self) -> dict[str, dict[str, Any]]:
        """Each provider's caps and today's use - for the app and the CLI."""
        out = {}
        for name, caps in load_limits(self.root).items():
            out[name] = {**self.used(name), "caps": caps}
            if "tokens_per_month" in caps:
                out[name]["month_tokens"] = self.month_tokens(name)
            if "usd_per_month" in caps:
                out[name]["month_usd"] = round(self.month_usd(name), 4)
            if self.who:
                out[name]["profile"] = self.used_by(name, self.who)
        return out


def estimate_tokens(*texts: str) -> int:
    """About four characters a token - for the check before a request; the
    count recorded after it is the provider's own."""
    return sum(len(t or "") for t in texts) // 4 + 1
