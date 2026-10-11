"""robots.txt fetching, caching and evaluation.

One cache per host, on disk so it survives runs, with a TTL. The point is that a
fifteen-minute poll across two dozen hosts must not re-fetch robots.txt two
dozen times an hour.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import requests


@dataclass
class RobotsDecision:
    allowed: bool
    reason: str


class RobotsCache:
    """Fetches and caches robots.txt, and answers "may I fetch this URL".

    Fetching robots.txt is itself a request, so it is deliberately NOT routed
    through the rate limiter — that would deadlock the first request to every
    host. It is one small request per host per TTL.
    """

    def __init__(
        self,
        cache_dir: Path,
        user_agent: str,
        ttl_seconds: int = 24 * 3600,
        timeout: float = 10.0,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent
        self.ttl = ttl_seconds
        self.timeout = timeout
        self._memo: dict[str, tuple[float, str | None, int | None]] = {}

    def _path(self, host: str) -> Path:
        safe = urllib.parse.quote(host, safe="")
        return self.cache_dir / f"{safe}.json"

    def _load(self, host: str) -> tuple[str | None, int | None] | None:
        if host in self._memo:
            fetched, body, status = self._memo[host]
            if time.time() - fetched < self.ttl:
                return body, status
        p = self._path(host)
        if not p.exists():
            return None
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if time.time() - blob.get("fetched_at", 0) >= self.ttl:
            return None
        self._memo[host] = (blob["fetched_at"], blob.get("body"), blob.get("status"))
        return blob.get("body"), blob.get("status")

    def _store(self, host: str, body: str | None, status: int | None) -> None:
        now = time.time()
        self._memo[host] = (now, body, status)
        try:
            self._path(host).write_text(
                json.dumps({"fetched_at": now, "body": body, "status": status}),
                encoding="utf-8",
            )
        except OSError:
            pass  # a cache that cannot write is slow, not broken

    def _fetch(self, scheme: str, host: str) -> tuple[str | None, int | None]:
        url = f"{scheme}://{host}/robots.txt"
        try:
            r = requests.get(
                url, timeout=self.timeout, headers={"User-Agent": self.user_agent}
            )
        except requests.RequestException:
            # Unreachable is not the same as forbidden. Treated as "no rules",
            # which matches how every mainstream crawler behaves.
            return None, None
        body = r.text if r.status_code == 200 else None
        return body, r.status_code

    def check(self, url: str) -> RobotsDecision:
        parts = urllib.parse.urlsplit(url)
        host, scheme = parts.netloc, parts.scheme or "https"
        if not host:
            return RobotsDecision(False, "no host in url")

        cached = self._load(host)
        if cached is None:
            body, status = self._fetch(scheme, host)
            self._store(host, body, status)
        else:
            body, status = cached

        # RFC 9309: 4xx means no restrictions; 5xx means assume disallowed.
        if status is not None and 500 <= status < 600:
            return RobotsDecision(False, f"robots.txt returned {status}; assuming disallow")
        if body is None:
            return RobotsDecision(True, "no robots.txt")

        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        if rfc9309_allowed(body, self.user_agent, path):
            return RobotsDecision(True, "allowed by robots.txt")
        return RobotsDecision(False, f"robots.txt disallows {path}")


# ── RFC 9309, not urllib.robotparser ─────────────────────────────────────────
# Python's parser got two live files wrong. It ends a group at a blank line,
# so Zoho's (an Allow list, a blank line, "Disallow: /") read as allow-all.
# And it takes the first rule that matches, so Microsoft's ("Disallow: /" and
# then "Allow: /api/pcsx") blocked the very API it allows. The standard, and
# Google: a group is its user-agent lines and the rules under them; the most
# specific (longest) matching rule wins; Allow wins a tie.

def _groups(body: str) -> list[tuple[list[str], list[tuple[bool, str]]]]:
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if rules:                          # a new group starts
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
        elif key in ("allow", "disallow") and agents:
            if value:                          # "Disallow:" with no path restricts nothing
                rules.append((key == "allow", value))
            elif not rules:
                rules.append((True, ""))       # keep the group, with no restriction
    if agents:
        groups.append((agents, rules))
    return groups


def _pattern(path: str) -> re.Pattern:
    end = path.endswith("$")
    body = re.escape(path[:-1] if end else path).replace(r"\*", ".*")
    return re.compile(body + ("$" if end else ""))


def rfc9309_allowed(body: str, user_agent: str, path: str) -> bool:
    token = user_agent.split("/", 1)[0].strip().lower()
    groups = _groups(body)
    # The groups naming us, else the "*" ones - never both: a "*" Allow must
    # not override a rule written specifically for this agent.
    mine = [r for agents, r in groups if any(a != "*" and a.split("/", 1)[0] == token for a in agents)]
    rules = [x for rs in (mine or [r for agents, r in groups if "*" in agents]) for x in rs]
    best_len, verdict = -1, True
    for allow, pat in rules:
        if pat and _pattern(pat).match(path):
            n = len(pat)
            if n > best_len or (n == best_len and allow):
                best_len, verdict = n, allow
    return verdict
