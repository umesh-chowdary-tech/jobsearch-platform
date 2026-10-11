"""A polite HTTP client: robots-aware, rate-limited, retrying only where it should."""
from __future__ import annotations

import os
import random
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .robots import RobotsCache

def user_agent() -> str:
    """Honest identification. A job board that wants to block this can, and
    should be able to — which is the point of saying who we are.
    JOBSEARCH_CONTACT (in .env) adds a URL the board can reach whoever runs
    this copy at."""
    contact = os.environ.get("JOBSEARCH_CONTACT", "").strip()
    return ("jobsearch-platform/0.1 (job-search agent; reads public job feeds"
            + (f"; +{contact}" if contact else "") + ")")


class Blocked(Exception):
    """robots.txt says no. Not retried, not escalated — recorded and dropped."""


@dataclass
class Result:
    url: str
    status: int
    json: Any | None
    text: str


# Hosts that ask for more room than one second. Microsoft's careers API
# answers "429 Please try again later" to the fourth request a second apart,
# and to requests six seconds apart after a short rest; ten seconds apart ran
# clean (measured 2026-09-29).
HOST_INTERVAL = {"apply.careers.microsoft.com": 10.0}


class Fetcher:
    """One request per second per host, at most - or HOST_INTERVAL's pace.

    The limit is per host, not global: Greenhouse and Lever are different
    infrastructure and there is no reason to make them queue behind each other.
    """

    def __init__(
        self,
        robots: RobotsCache,
        min_interval: float = 1.0,
        timeout: float = 15.0,
        retries: int = 2,
    ) -> None:
        self.robots = robots
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self._last: dict[str, float] = {}
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": user_agent(), "Accept": "application/json, text/html;q=0.8"}
        )

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        interval = max(self.min_interval, HOST_INTERVAL.get(host, 0.0))
        if last is not None:
            gap = time.monotonic() - last
            if gap < interval:
                time.sleep(interval - gap)
        self._last[host] = time.monotonic()

    def get(self, url: str) -> Result:
        return self._request("GET", url)

    def post(self, url: str, body: Any) -> Result:
        """A JSON POST, for search APIs that only answer POST (Workday).

        Same robots check, rate limit and retry rule as `get`. Retrying is safe
        only because the callers are read-only searches; nothing that changes
        state on the other end should come through here.
        """
        return self._request("POST", url, body)

    def _request(self, method: str, url: str, body: Any = None) -> Result:
        decision = self.robots.check(url)
        if not decision.allowed:
            raise Blocked(f"{url}: {decision.reason}")

        host = urllib.parse.urlsplit(url).netloc
        last_exc: Exception | None = None

        for attempt in range(self.retries + 1):
            self._wait(host)
            try:
                if method == "POST":
                    r = self._session.post(url, json=body, timeout=self.timeout)
                else:
                    r = self._session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_exc = exc
            else:
                # 4xx is an answer — the slug is wrong, or we are not welcome.
                # Retrying it just makes us rude. Only 5xx and 429 are transient.
                if r.status_code < 500 and r.status_code != 429:
                    try:
                        body = r.json()
                    except ValueError:
                        body = None
                    return Result(url=url, status=r.status_code, json=body, text=r.text)
                last_exc = requests.HTTPError(f"{r.status_code} for {url}")

            if attempt < self.retries:
                # Jittered backoff so a flapping host does not get a synchronised
                # retry from every slug we are probing on it.
                time.sleep((2**attempt) + random.uniform(0, 0.5))

        raise last_exc if last_exc else RuntimeError(f"unreachable: {url}")


def default_fetcher(var_dir: Path) -> Fetcher:
    return Fetcher(RobotsCache(Path(var_dir) / "robots", user_agent()))
