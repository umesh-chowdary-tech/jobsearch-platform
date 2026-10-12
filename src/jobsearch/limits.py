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
# The purpose `setup_usd_per_day` is for: drafting a profile from someone's resumes.
SETUP = "setup"


# The admin page's changes over data/llm-limits.yaml, in var/_shared (so the
# hosted service's database keeps them with the rest of var/):
#   {"paused": true,                                  no AI request at all
#    "providers": {"claude": {"usd_per_month": 8, "per_profile": {...}}},
#    "people": {"u-...": {"claude": {"usd_per_day": 0.5}}}}   one person's own share
OVERRIDES = "llm-limits.json"


def load_overrides(root: Path) -> dict[str, Any]:
    try:
        doc = json.loads((shared_var(Path(root)) / OVERRIDES).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def save_overrides(root: Path, doc: dict[str, Any]) -> None:
    var = shared_var(Path(root))
    var.mkdir(parents=True, exist_ok=True)
    tmp = var / (OVERRIDES + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, var / OVERRIDES)


def load_limits(root: Path) -> dict[str, dict[str, Any]]:
    """Each provider's caps: data/llm-limits.yaml, with the admin page's
    changes over it. A provider missing from the file stays unused - a change
    can move a cap, never add a provider."""
    p = Path(root) / "data" / "llm-limits.yaml"
    if not p.exists():
        return {}
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = {str(k): dict(v or {}) for k, v in doc.items()}
    for name, caps in (load_overrides(root).get("providers") or {}).items():
        if name not in out or not isinstance(caps, dict):
            continue
        merged = dict(out[name])
        for k, v in caps.items():
            if k == "per_profile" and isinstance(v, dict):
                merged["per_profile"] = {**(merged.get("per_profile") or {}), **v}
            else:
                merged[k] = v
        out[name] = merged
    return out


def price_for(caps: dict[str, Any], model: str = "") -> dict[str, Any] | None:
    """Dollars per million tokens for `model`. `price_per_mtok` is either one
    price for the provider or one per model name; None when it has none."""
    price = caps.get("price_per_mtok") or {}
    if not price:
        return None
    if "input" in price:
        return price
    got = price.get(model)
    return dict(got) if isinstance(got, dict) else None


def unpriced(caps: dict[str, Any], model: str) -> str:
    """Why `model` can't be sent a request under dollar caps it has no price
    for - a cap in dollars can't hold for a model nobody priced. "" if it can."""
    in_dollars = any(k in caps for k in ("usd_per_day", "usd_per_month")) or \
        "usd_per_day" in (caps.get("per_profile") or {})
    if in_dollars and price_for(caps, model) is None:
        return f"no price for {model} in data/llm-limits.yaml - not used until one is"
    return ""


def cost_usd(caps: dict[str, Any], usage: dict[str, int], model: str = "") -> float:
    """Dollars for one request, from the model's price (`price_for`) and the
    token counts it reported (input, output, cache_write, cache_read). A reply
    from a model with no price - one the API switched to - is counted at the
    dearest listed price, so it can only overstate what was spent."""
    price = price_for(caps, model)
    if price is None:
        listed = [p for p in (caps.get("price_per_mtok") or {}).values() if isinstance(p, dict)]
        price = max(listed, key=lambda p: float(p.get("output", 0)), default={})
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

    def days(self) -> dict[str, dict[str, Any]]:
        """Every day kept (KEEP_DAYS), by provider - for the admin page."""
        return self._load().get("days") or {}

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
        over = load_overrides(self.root)
        if over.get("paused"):
            return "AI is paused on the admin page"
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
        return self._check_share(provider, caps, tokens, usd, over, purpose)

    def _check_share(self, provider: str, caps: dict[str, Any], tokens: int, usd: float,
                     over: dict[str, Any], purpose: str = "") -> str:
        """The profile's own caps: `per_profile`, or the ones the admin page
        set for this person. Setup reads several resumes with the strongest
        model once, so on the day it runs it may spend `setup_usd_per_day`
        instead of the everyday `usd_per_day`; the month's line holds either way."""
        own = ((over.get("people") or {}).get(self.who) or {}).get(provider) or {}
        pp = {**(caps.get("per_profile") or {}), **own}
        if not (self.who and pp):
            return ""
        u = self.used_by(provider, self.who)
        where = "per_profile in data/llm-limits.yaml"
        if "requests_per_day" in pp and u["requests"] + 1 > int(pp["requests_per_day"]):
            return f"{self.who}'s daily request limit reached ({u['requests']} of {pp['requests_per_day']}, {where})"
        if "tokens_per_day" in pp and u["tokens"] + tokens > int(pp["tokens_per_day"]):
            return (f"{self.who}'s daily token limit reached ({u['tokens']:,} of {int(pp['tokens_per_day']):,},"
                    f" {where})")
        day_key = "setup_usd_per_day" if purpose == SETUP and "setup_usd_per_day" in pp else "usd_per_day"
        if day_key in pp and u["usd"] + usd > float(pp[day_key]):
            return (f"{self.who}'s daily spend limit reached (${u['usd']:.2f} of ${float(pp[day_key]):.2f},"
                    f" {where})")
        if "usd_per_month" in pp:
            month = self.month_by_profile().get(self.who, {}).get("usd", 0.0)
            if month + usd > float(pp["usd_per_month"]):
                return (f"{self.who}'s monthly spend limit reached (${month:.2f} of ${float(pp['usd_per_month']):.2f},"
                        f" {where})")
        return ""

    def record(self, provider: str, tokens: int, purpose: str = "", usd: float = 0.0, model: str = "") -> None:
        """One request sent, and the tokens (and dollars) it cost - 0 when it
        was refused. The job and the model are kept too, for the admin page's
        cost by job and by model."""
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
                if usd:
                    up = row.setdefault("usd_by_purpose", {})
                    up[purpose] = round(float(up.get(purpose, 0)) + float(usd), 6)
            if model:
                bm = row.setdefault("by_model", {}).setdefault(model, {"requests": 0, "tokens": 0})
                bm["requests"] += 1
                bm["tokens"] += max(0, int(tokens))
                if usd:
                    bm["usd"] = round(float(bm.get("usd", 0)) + float(usd), 6)
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
