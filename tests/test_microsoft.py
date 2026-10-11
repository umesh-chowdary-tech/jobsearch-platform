"""Microsoft's careers site (Eightfold). The API is scripted; shapes, the page
size and the rate limit are from the live site, 2026-09-29."""
from __future__ import annotations

import urllib.parse

import pytest

from jobsearch import applications as A
from jobsearch import providers as P
from jobsearch.http import Fetcher, Result
from jobsearch.models import ERROR, OK
from jobsearch.robots import RobotsCache

SLUG = "india@software engineering+research, applied, & data sciences"


def _pos(n, name="Software Engineer 2 - C# + Azure"):
    return {"id": 1970393557007700 + n, "displayJobId": str(200057800 + n), "atsJobId": str(200057800 + n),
            "name": name, "locations": ["India, Telangana, Hyderabad", "India, Karnataka, Bangalore"],
            "standardizedLocations": ["Hyderabad, TS, IN", "Bengaluru, KA, IN"], "postedTs": 1790620200,
            "department": "Software Engineering", "positionUrl": f"/careers/job/{1970393557007700 + n}"}


class API:
    """Ten a page, whatever is asked for - as the live API does."""

    def __init__(self, positions, count=None):
        self.positions, self.count, self.urls = positions, len(positions) if count is None else count, []

    def get(self, url):
        self.urls.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if "position_details" in url:
            return Result(url=url, status=200, text="", json={"data": {
                "jobDescription": "<p>Build <b>Azure</b> services &amp; APIs.</p><ul><li>C#, .NET</li></ul>"}})
        start = int(q["start"][0])
        return Result(url=url, status=200, text="", json={"status": 200, "data": {
            "count": self.count, "positions": self.positions[start:start + 10]}})


def test_a_slug_is_a_location_and_professions():
    assert P.parse_ms_slug(SLUG) == ("india", ("software engineering", "research, applied, & data sciences"))


@pytest.mark.parametrize("bad", ["india", "@software engineering", "India@software engineering",
                                 "india@Software/Engineering", ""])
def test_anything_else_is_refused(bad):
    with pytest.raises(ValueError):
        P.parse_ms_slug(bad)


def test_the_search_carries_the_location_and_every_profession():
    api = API([_pos(1)])
    P.probe(api, "microsoft", SLUG)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(api.urls[0]).query)
    assert q["location"] == ["india"] and q["domain"] == ["microsoft.com"]
    assert q["filter_profession"] == ["software engineering", "research, applied, & data sciences"]


def test_postings_are_keyed_by_microsofts_job_number():
    p = P.probe(API([_pos(83)]), "microsoft", SLUG)
    (post,) = p.postings
    assert p.status == OK and p.company == "Microsoft"
    assert post.id == "microsoft-200057883"                    # the "Job number" in Microsoft's emails
    assert post.url == "https://apply.careers.microsoft.com/careers/job/1970393557007783"
    assert post.location == "Hyderabad, TS, IN; Bengaluru, KA, IN" and post.updated_at == "2026-09-28"
    assert A.posting_req(post.id) == "200057883"


def test_every_page_of_ten_is_read():
    api = API([_pos(i) for i in range(23)])
    p = P.probe(api, "microsoft", SLUG)
    assert p.status == OK and len(p.postings) == 23 and len(api.urls) == 3


def test_no_postings_is_an_error_not_an_empty_board():
    """A misspelt profession returned count 0, live."""
    p = P.probe(API([]), "microsoft", "india@sofware engineering")
    assert p.status == ERROR and "check the names" in p.error


def test_too_many_for_this_hosts_pace_is_refused():
    p = P.probe(API([_pos(1)], count=900), "microsoft", SLUG)
    assert p.status == ERROR and "900 postings" in p.error


def test_a_board_that_moved_while_paging_is_retried():
    ps = [_pos(i) for i in range(10)] + [_pos(9)] + [_pos(11)]
    p = P.probe(API(ps), "microsoft", SLUG)
    assert p.status == ERROR and "changed while paging" in p.error


def test_the_description_comes_from_the_position_details():
    url = "https://apply.careers.microsoft.com/careers/job/1970393557007783"
    got = P.fetch_details(API([]), "microsoft", SLUG, {"url": url, "id": "microsoft-200057883"})
    assert "Build Azure services & APIs." in " ".join(got["description"].split())


def test_the_fetcher_waits_ten_seconds_between_microsoft_requests(tmp_path, monkeypatch):
    """The fourth request a second apart got "429 Please try again later"."""
    class NoRobots(RobotsCache):
        def _fetch(self, scheme, host):
            return None, 404
    f = Fetcher(NoRobots(tmp_path / "r", "t"))
    slept: list[float] = []
    monkeypatch.setattr("jobsearch.http.time.sleep", lambda s: slept.append(s))
    f._wait("apply.careers.microsoft.com")
    f._wait("apply.careers.microsoft.com")
    f._wait("boards-api.greenhouse.io")
    f._wait("boards-api.greenhouse.io")
    assert slept[0] > 9.9 and slept[1] <= 1.0
