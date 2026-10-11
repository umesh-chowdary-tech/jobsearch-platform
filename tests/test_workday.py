"""Workday probe tests. The API is scripted; the paging and checks are real.

Payload shapes are copied from Wells Fargo's live site, 2026-09-24.
"""
from __future__ import annotations

import pytest

from jobsearch import providers as P
from jobsearch.discovery import search_company
from jobsearch.http import Result
from jobsearch.models import EMPTY, ERROR, NOT_FOUND, OK

SLUG = "wf.wd1.WellsFargoJobs"
API = "https://wf.wd1.myworkdayjobs.com/wday/cxs/wf/WellsFargoJobs"
INDIA_ID = "c4f78be1a8f14da0ab49ce1162348a5e"

# Wells Fargo nests the country facet one level down.
NESTED_FACETS = [
    {"facetParameter": "jobFamilyGroup", "values": [{"descriptor": "Operations", "id": "x1"}]},
    {"facetParameter": "locationMainGroup", "values": [
        {"facetParameter": "locationHierarchy1", "descriptor": "Region",
         "values": [{"descriptor": "APAC", "id": "r1", "count": 260}]},
        {"facetParameter": "locationCountry", "descriptor": "Country",
         "values": [{"descriptor": "Canada", "id": "c1", "count": 1},
                    {"descriptor": "India", "id": INDIA_ID, "count": 163}]},
    ]},
]


def _job(n: int, title: str = "Software Engineer", where: str = "Hyderabad, India") -> dict:
    return {
        "title": title,
        "externalPath": f"/job/Hyderabad-India/{title.replace(' ', '-')}_R-{n}",
        "locationsText": where,
        "postedOn": "Posted Today",
        "bulletFields": [f"R-{n}", "Posting End Date: 09/28/2026"],
    }


def _page(jobs: list[dict], total: int, facets: list | None = None) -> Result:
    body = {"total": total, "jobPostings": jobs, "facets": facets or []}
    return Result(url=API + "/jobs", status=200, json=body, text="")


class ScriptedFetcher:
    """Answers POSTs from a queue and GETs from a dict; records every request."""

    def __init__(self, posts: list[Result], gets: dict[str, Result] | None = None):
        self.posts = list(posts)
        self.gets = gets or {}
        self.bodies: list[dict] = []
        self.get_urls: list[str] = []

    def post(self, url, body):
        self.bodies.append(body)
        return self.posts.pop(0)

    def get(self, url):
        self.get_urls.append(url)
        return self.gets.get(url, Result(url=url, status=404, json=None, text=""))


# ── slugs and ids ────────────────────────────────────────────────────────────

def test_slug_has_three_parts_and_an_optional_country():
    ws = P.parse_workday_slug("wf.wd1.WellsFargoJobs@India")
    assert (ws.tenant, ws.dc, ws.site, ws.country) == ("wf", "wd1", "WellsFargoJobs", "India")
    assert ws.api == API
    assert P.parse_workday_slug(SLUG).country is None


@pytest.mark.parametrize("bad", ["wellsfargo", "wf.WellsFargoJobs", "wf.eu1.WellsFargoJobs",
                                 "wf..WellsFargoJobs", ""])
def test_malformed_slug_is_refused(bad):
    with pytest.raises(ValueError):
        P.parse_workday_slug(bad)


def test_malformed_slug_is_an_error_not_a_crash():
    p = P.probe(ScriptedFetcher([]), "workday", "wellsfargo")
    assert p.status == ERROR and "tenant.wdN.site" in p.error


def test_id_survives_a_title_edit():
    """The id is diffed across polls; the title part of the path gets edited."""
    before = P.workday_id("wf", "/job/Hyderabad-India/Software-Engineer_R-500001-1")
    after = P.workday_id("wf", "/job/Hyderabad-India/Software-Engineer---Java-backend_R-500001-1")
    assert before == after == "wf-R-500001-1"


def test_id_carries_the_tenant():
    """R-numbers are per employer. Two tenants can both have R-100200, and
    triage's already-seen set and the pipeline key on the bare id."""
    assert P.workday_id("wf", "/job/X/Role_R-100200") != P.workday_id("nvidia", "/job/X/Role_R-100200")


def test_parse_keeps_the_location_count_and_builds_the_public_url():
    ws = P.parse_workday_slug(SLUG)
    [post] = P.parse_workday({"jobPostings": [_job(1, where="3 Locations")]}, ws)
    assert post.location == "3 Locations"
    assert post.url == "https://wf.wd1.myworkdayjobs.com/WellsFargoJobs/job/Hyderabad-India/Software-Engineer_R-1"
    assert post.updated_at is None     # "Posted Today" is a bucket, not a date


# ── country facet ────────────────────────────────────────────────────────────

def test_country_facet_found_when_nested():
    assert P.workday_country_facet(NESTED_FACETS, "india") == (True, ("locationCountry", INDIA_ID))


def test_country_facet_found_at_top_level():
    facets = [{"facetParameter": "Location_Country", "values": [{"descriptor": "India", "id": "i9"}]}]
    assert P.workday_country_facet(facets, "India") == (True, ("Location_Country", "i9"))


def test_country_facet_absent_versus_country_absent():
    assert P.workday_country_facet(NESTED_FACETS, "Brazil") == (True, None)
    assert P.workday_country_facet(NESTED_FACETS[:1], "India") == (False, None)


# ── paging ───────────────────────────────────────────────────────────────────

def test_pages_on_the_first_total_only():
    """Later pages report total 0 (seen live). Re-reading it would stop after
    page two and quietly lose the rest of the board."""
    f = ScriptedFetcher([
        _page([_job(i) for i in range(20)], total=45),
        _page([_job(i) for i in range(20, 40)], total=0),
        _page([_job(i) for i in range(40, 45)], total=0),
    ])
    p = P.probe(f, "workday", SLUG, with_board_name=False)
    assert p.status == OK and len(p.postings) == 45
    assert [b["offset"] for b in f.bodies] == [0, 20, 40]
    assert all(b["limit"] == 20 for b in f.bodies)


def test_a_failed_page_fails_the_whole_probe():
    """A short board reads to the watcher as postings closing."""
    f = ScriptedFetcher([
        _page([_job(i) for i in range(20)], total=30),
        Result(url=API + "/jobs", status=400, json=None, text=""),
    ])
    p = P.probe(f, "workday", SLUG, with_board_name=False)
    assert p.status == ERROR and p.postings == []


def test_board_shifting_mid_paging_is_an_error():
    """A posting added at the top pushes one onto the next page twice, and the
    last one off the end."""
    f = ScriptedFetcher([
        _page([_job(i) for i in range(20)], total=25),
        _page([_job(i) for i in range(19, 24)], total=0),
    ])
    p = P.probe(f, "workday", SLUG, with_board_name=False)
    assert p.status == ERROR and "24 of 25" in p.error
    assert p.postings == []


def test_a_hidden_posting_counts_toward_total_but_is_not_a_posting():
    """Wells Fargo lists one posting as {"bulletFields": ["R-500002"]} and
    nothing else. Without counting it every poll read as "board changed", and
    a page of nothing but those must not end the paging."""
    stub = {"bulletFields": ["R-500002"]}
    f = ScriptedFetcher([
        _page([_job(i) for i in range(19)] + [stub], total=41),
        _page([{"bulletFields": [f"R-9{i}"]} for i in range(20)], total=0),
        _page([_job(100)], total=0),
    ])
    p = P.probe(f, "workday", SLUG, with_board_name=False)
    assert p.status == OK
    assert len(p.postings) == 20 and all(post.url for post in p.postings)
    assert len(f.bodies) == 3


@pytest.mark.parametrize("status", [404, 422])
def test_unknown_site_or_tenant_is_not_found(status):
    f = ScriptedFetcher([Result(url=API, status=status, json={"errorCode": "x"}, text="")])
    assert P.probe(f, "workday", SLUG).status == NOT_FOUND


def test_a_huge_board_without_a_country_is_refused_after_one_request():
    f = ScriptedFetcher([_page([_job(i) for i in range(20)], total=1807)])
    p = P.probe(f, "workday", SLUG, with_board_name=False)
    assert p.status == ERROR and f"{SLUG}@India" in p.error
    assert len(f.bodies) == 1


# ── narrowing to a country ───────────────────────────────────────────────────

def test_country_is_applied_to_every_page():
    f = ScriptedFetcher([
        _page([_job(i) for i in range(20)], total=1807, facets=NESTED_FACETS),
        _page([_job(i) for i in range(100, 120)], total=25),
        _page([_job(i) for i in range(120, 125)], total=0),
    ])
    p = P.probe(f, "workday", SLUG + "@India", with_board_name=False)
    assert p.status == OK and len(p.postings) == 25
    assert f.bodies[0]["appliedFacets"] == {}
    assert all(b["appliedFacets"] == {"locationCountry": [INDIA_ID]} for b in f.bodies[1:])


def test_no_postings_in_the_country_is_an_empty_board():
    f = ScriptedFetcher([_page([_job(1)], total=1, facets=NESTED_FACETS)])
    p = P.probe(f, "workday", SLUG + "@Brazil", with_board_name=False)
    assert p.status == EMPTY and p.found


def test_no_country_facet_is_an_error_not_an_empty_board():
    """EMPTY would read as "nothing in India today" when the truth is "cannot tell"."""
    f = ScriptedFetcher([_page([_job(1)], total=1, facets=NESTED_FACETS[:1])])
    p = P.probe(f, "workday", SLUG + "@India", with_board_name=False)
    assert p.status == ERROR and "no country facet" in p.error


# ── detail ───────────────────────────────────────────────────────────────────

DETAIL_URL = API + "/job/Hyderabad-India/Software-Engineer_R-1"
DETAIL = Result(url=DETAIL_URL, status=200, text="", json={
    "jobPostingInfo": {
        "jobDescription": "<p><strong>About this role:</strong></p><p>Java &amp; Spring Boot</p>",
        "location": "COLUMBUS, OH",
        "additionalLocations": ["Hyderabad, India", "IRVING, TX"],
        "startDate": "2026-09-24",
    },
    "hiringOrganization": {"name": "B10 Wells Fargo Bank, N. A.", "url": ""},
})


def test_detail_brings_the_description_every_location_and_the_date():
    f = ScriptedFetcher([], gets={DETAIL_URL: DETAIL})
    public = "https://wf.wd1.myworkdayjobs.com/WellsFargoJobs/job/Hyderabad-India/Software-Engineer_R-1"
    got = P.workday_detail(f, SLUG + "@India", public)
    assert f.get_urls == [DETAIL_URL]
    assert "Java & Spring Boot" in got["description"] and "<p>" not in got["description"]
    assert got["location"] == "COLUMBUS, OH; Hyderabad, India; IRVING, TX"
    assert got["updated_at"] == "2026-09-24"


def test_fetch_details_merges_posting_fields_only():
    f = ScriptedFetcher([], gets={DETAIL_URL: DETAIL})
    posting = {"url": "https://wf.wd1.myworkdayjobs.com/WellsFargoJobs/job/Hyderabad-India/Software-Engineer_R-1"}
    got = P.fetch_details(f, "workday", SLUG, posting)
    assert set(got) == {"description", "location", "updated_at"}


def test_failed_detail_returns_nothing_rather_than_blanks():
    """Merged with dict.update: a blank would overwrite the list's location."""
    f = ScriptedFetcher([])
    public = "https://wf.wd1.myworkdayjobs.com/WellsFargoJobs/job/Hyderabad-India/Software-Engineer_R-1"
    assert P.workday_detail(f, SLUG, public) == {}
    assert P.workday_detail(f, SLUG, "https://elsewhere.invalid/x") == {}


def test_board_name_comes_from_the_hiring_organisation():
    f = ScriptedFetcher([_page([_job(1)], total=1)], gets={DETAIL_URL: DETAIL})
    p = P.probe(f, "workday", SLUG, "Wells Fargo")
    assert p.board_name == "B10 Wells Fargo Bank, N. A."


# ── discovery ────────────────────────────────────────────────────────────────

def test_search_never_guesses_workday_slugs():
    class Refuses:
        def get(self, url):
            return Result(url=url, status=404, json=None, text="")

        def post(self, url, body):
            pytest.fail("search guessed a Workday slug")

    run = search_company(Refuses(), "Wells Fargo")
    assert "workday" not in {p.provider for p in run.probes}
    assert "workday" in P.ALL_PROVIDERS
