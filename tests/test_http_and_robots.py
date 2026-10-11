"""Politeness tests: robots.txt handling and rate limiting, with no real network."""
from __future__ import annotations

import time

import pytest

from jobsearch.http import Blocked, Fetcher
from jobsearch.robots import RobotsCache


class FakeRobots(RobotsCache):
    """Real parsing logic, canned robots.txt bodies."""

    def __init__(self, tmp_path, bodies: dict[str, tuple[str | None, int | None]]):
        super().__init__(tmp_path / "robots", "test-agent")
        self._bodies = bodies
        self.fetches: list[str] = []

    def _fetch(self, scheme, host):
        self.fetches.append(host)
        return self._bodies.get(host, (None, 404))


DISALLOW_ALL = ("User-agent: *\nDisallow: /\n", 200)
ALLOW_ALL = ("User-agent: *\nDisallow:\n", 200)
DISALLOW_API = ("User-agent: *\nDisallow: /postings/\n", 200)


def test_missing_robots_means_allowed(tmp_path):
    r = FakeRobots(tmp_path, {})
    assert r.check("https://example.invalid/jobs").allowed


def test_disallow_all_blocks(tmp_path):
    r = FakeRobots(tmp_path, {"blocked.invalid": DISALLOW_ALL})
    d = r.check("https://blocked.invalid/anything")
    assert not d.allowed and "disallow" in d.reason.lower()


def test_path_specific_disallow(tmp_path):
    """The SmartRecruiters case: the site is fine, the postings API is not."""
    r = FakeRobots(tmp_path, {"ats.invalid": DISALLOW_API})
    assert not r.check("https://ats.invalid/postings/acme").allowed
    assert r.check("https://ats.invalid/careers").allowed


def test_server_error_is_treated_as_disallow(tmp_path):
    """RFC 9309: 5xx means assume disallowed. Failing open on a broken server
    is how a polite crawler becomes an impolite one."""
    r = FakeRobots(tmp_path, {"flaky.invalid": (None, 503)})
    d = r.check("https://flaky.invalid/jobs")
    assert not d.allowed and "503" in d.reason


def test_4xx_means_no_rules(tmp_path):
    r = FakeRobots(tmp_path, {"nope.invalid": (None, 404)})
    assert r.check("https://nope.invalid/jobs").allowed


def test_robots_is_fetched_once_per_host(tmp_path):
    r = FakeRobots(tmp_path, {"a.invalid": ALLOW_ALL})
    for _ in range(5):
        r.check("https://a.invalid/jobs")
    assert r.fetches.count("a.invalid") == 1


def test_cache_survives_a_new_instance(tmp_path):
    r1 = FakeRobots(tmp_path, {"a.invalid": ALLOW_ALL})
    r1.check("https://a.invalid/jobs")
    r2 = FakeRobots(tmp_path, {"a.invalid": ALLOW_ALL})
    r2.check("https://a.invalid/jobs")
    assert r2.fetches == []          # read from disk, not re-fetched


# ── rate limiting ────────────────────────────────────────────────────────────

class CountingFetcher(Fetcher):
    def __init__(self, robots, **kw):
        super().__init__(robots, **kw)
        self.calls: list[str] = []

    def _real_get(self, url):  # pragma: no cover - replaced in tests
        raise NotImplementedError


def test_blocked_url_raises_before_any_request(tmp_path):
    r = FakeRobots(tmp_path, {"blocked.invalid": DISALLOW_ALL})
    f = Fetcher(r, min_interval=0)
    with pytest.raises(Blocked):
        f.get("https://blocked.invalid/jobs")


def test_post_honours_robots_too(tmp_path, monkeypatch):
    """Workday's search is a POST. It must not be a way around robots.txt."""
    r = FakeRobots(tmp_path, {"blocked.invalid": DISALLOW_ALL})
    f = Fetcher(r, min_interval=0)
    monkeypatch.setattr(f._session, "post", lambda *a, **k: pytest.fail("request sent"))
    with pytest.raises(Blocked):
        f.post("https://blocked.invalid/wday/cxs/t/s/jobs", {"limit": 20})


def test_rate_limit_is_per_host(tmp_path, monkeypatch):
    r = FakeRobots(tmp_path, {})
    f = Fetcher(r, min_interval=0.25)
    slept: list[float] = []
    monkeypatch.setattr("jobsearch.http.time.sleep", lambda s: slept.append(s))

    f._wait("a.invalid")
    f._wait("b.invalid")     # different host — must not wait behind the first
    assert slept == []

    f._wait("a.invalid")     # same host again — must wait
    assert len(slept) == 1 and 0 < slept[0] <= 0.25


def test_rate_limit_actually_elapses(tmp_path):
    interval = 0.1
    f = Fetcher(FakeRobots(tmp_path, {}), min_interval=interval)
    start = time.monotonic()
    f._wait("h.invalid")
    f._wait("h.invalid")
    # Tolerance, because time.sleep may return a hair early and the clock has
    # finite resolution. Asserting on the exact figure makes the suite flaky,
    # which is worse than useless - it teaches you to ignore red.
    assert time.monotonic() - start >= interval * 0.9


def test_a_blank_line_inside_a_group_does_not_end_it(tmp_path):
    """Zoho's robots.txt: an Allow list, a blank line, then "Disallow: /".
    Python's parser dropped the Disallow and allowed everything."""
    body = ("User-agent: *\nAllow: /jobs\n\nDisallow: /\n", 200)
    r = FakeRobots(tmp_path, {"org.zohorecruit.com": body})
    assert r.check("https://org.zohorecruit.com/jobs/Careers").allowed
    assert not r.check("https://org.zohorecruit.com/recruit/Login.do").allowed


def test_separate_groups_stay_separate(tmp_path):
    body = ("User-agent: someone-else\nDisallow: /\n\nUser-agent: *\nDisallow: /private\n", 200)
    r = FakeRobots(tmp_path, {"x.invalid": body})
    assert r.check("https://x.invalid/jobs").allowed
    assert not r.check("https://x.invalid/private/a").allowed


# ── RFC 9309 precedence: the most specific rule wins ─────────────────────────

MICROSOFT = ("User-agent: *\nDisallow: /\nAllow: /$\nAllow: /careers\nAllow: /api/apply\nAllow: /api/pcsx\n"
             "Allow: /candidate/login\n", 200)


def test_a_longer_allow_beats_an_earlier_disallow(tmp_path):
    """Microsoft's file. Python's parser took the first match, "Disallow: /",
    and blocked the API the file allows."""
    r = FakeRobots(tmp_path, {"apply.careers.microsoft.com": MICROSOFT})
    assert r.check("https://apply.careers.microsoft.com/api/pcsx/search?domain=microsoft.com").allowed
    assert r.check("https://apply.careers.microsoft.com/").allowed                       # "Allow: /$"
    assert not r.check("https://apply.careers.microsoft.com/admin").allowed
    assert not r.check("https://apply.careers.microsoft.com/x/").allowed                 # "$" anchors


@pytest.mark.parametrize("rules, path, allowed", [
    ("Allow: /p\nDisallow: /p", "/p", True),                  # a tie goes to Allow
    ("Disallow: /*.pdf$\nAllow: /", "/a/b.pdf", False),       # wildcard and end anchor
    ("Disallow: /*.pdf$\nAllow: /", "/a/b.pdf?x=1", True),
    ("Disallow: /private\nAllow: /private/open", "/private/open/1", True),
    ("Disallow:", "/anything", True),                         # an empty Disallow restricts nothing
])
def test_precedence(tmp_path, rules, path, allowed):
    r = FakeRobots(tmp_path, {"x.invalid": (f"User-agent: *\n{rules}\n", 200)})
    assert r.check(f"https://x.invalid{path}").allowed is allowed


def test_a_group_naming_us_replaces_the_star_group(tmp_path):
    body = ("User-agent: *\nAllow: /\n\nUser-agent: test-agent\nDisallow: /jobs\n", 200)
    r = FakeRobots(tmp_path, {"x.invalid": body})
    assert not r.check("https://x.invalid/jobs").allowed          # ours, not "*"'s Allow
    other = ("User-agent: test\nDisallow: /\n", 200)                # "test" is not "test-agent"
    assert FakeRobots(tmp_path / "o", {"y.invalid": other}).check("https://y.invalid/jobs").allowed
