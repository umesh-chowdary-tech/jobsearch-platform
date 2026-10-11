"""atlassian.com's careers listing: every job worldwide in one answer, the
page's team and country filters applied here. Shapes from the live endpoint,
2026-09-28."""
from __future__ import annotations

import pytest

from jobsearch import applications as A
from jobsearch import providers as P
from jobsearch.http import Result
from jobsearch.models import EMPTY, ERROR, OK


def _job(n, title, team="Engineering", locs=("Bengaluru - India -   Bengaluru,  560071 India", "Remote - India - Remote")):
    return {"id": n, "title": title, "category": team, "locations": list(locs), "portalId": "17",
            "overview": "<p><strong>Working at Atlassian</strong></p><p>Work from anywhere.</p>",
            "responsibilities": "<ul><li><p>Build the search platform &amp; its APIs</p></li></ul>",
            "qualifications": "<ul><li><p>8+ years with Java or Kotlin</p></li></ul>",
            "applyUrl": f"https://globalcareers-atlassian.icims.com/jobs/{n}/x/job?mode=apply"}


JOBS = [_job(27090, "Principal Engineer, Search Platform"),
        _job(25696, "Principal Fullstack Engineer", locs=("Remote - India - Remote", "Remote - Remote")),
        _job(25583, "Account Executive - Japanese Speaking", team="Sales", locs=("Remote - Japan - Remote",)),
        _job(26000, "Senior Engineer", locs=("San Francisco - United States -   San Francisco",)),
        _job(26100, "Data Scientist", team="Analytics & Data Science")]


class Endpoint:
    def __init__(self, body, status=200):
        self.body, self.status, self.urls = body, status, []

    def get(self, url):
        self.urls.append(url)
        return Result(url=url, status=self.status, json=self.body, text="")


def test_a_slug_is_a_country_and_teams():
    assert P.parse_atlassian_slug("India@Engineering+Analytics & Data Science") == (
        "India", ("Engineering", "Analytics & Data Science"))


@pytest.mark.parametrize("bad", ["India", "@Engineering", "India@", "India@Eng/../x", ""])
def test_anything_else_is_refused(bad):
    with pytest.raises(ValueError):
        P.parse_atlassian_slug(bad)


def test_only_the_teams_in_the_country_are_postings():
    p = P.probe(Endpoint(JOBS), "atlassian", "India@Engineering")
    assert p.status == OK and p.company == "Atlassian"
    assert [x.id for x in p.postings] == ["atlassian-27090", "atlassian-25696"]
    search = p.postings[0]
    assert search.location == "Bengaluru - India; Remote - India"
    assert search.url == "https://www.atlassian.com/company/careers/details/27090"
    body = " ".join(search.description.split())
    assert "Build the search platform & its APIs" in body and "8+ years with Java or Kotlin" in body
    assert "atlassian" not in P.DETAIL_PROVIDERS


def test_a_misspelt_team_is_an_error_not_an_empty_board():
    p = P.probe(Endpoint(JOBS), "atlassian", "India@Engineerng")
    assert p.status == ERROR and "Engineerng" in p.error and "Engineering" in p.error


def test_a_country_with_no_posting_anywhere_is_an_error():
    assert P.probe(Endpoint(JOBS), "atlassian", "Indai@Engineering").status == ERROR


def test_a_real_team_with_nothing_in_the_country_is_empty():
    assert P.probe(Endpoint(JOBS), "atlassian", "India@Sales").status == EMPTY


@pytest.mark.parametrize("body, status", [([], 200), ({"error": "x"}, 200), (None, 503)])
def test_no_list_is_an_error(body, status):
    assert P.probe(Endpoint(body, status), "atlassian", "India@Engineering").status == ERROR


def test_an_atlassian_job_id_is_a_requisition_for_linking():
    assert A.posting_req("atlassian-27090") == "27090"
