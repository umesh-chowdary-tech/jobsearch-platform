"""Oracle Recruiting Cloud (JPMorgan's careers site). The API is scripted;
the shapes, and the two things it silently ignores, are from the live site,
2026-09-28."""
from __future__ import annotations

import re

import pytest

from jobsearch import providers as P
from jobsearch.http import Result
from jobsearch.models import ERROR, NOT_FOUND, OK

SLUG = "jpmc.fa.oraclecloud.com@CX_1001@India"
INDIA = 300000000289360
FACETS = [{"Id": 300000000289250, "Name": "United States", "TotalCount": 5412},
          {"Id": INDIA, "Name": "India", "TotalCount": 3}]


def _req(n, title="Lead Software Engineer - Java", where="Bengaluru, Karnataka, India", also=()):
    return {"Id": str(210789000 + n), "Title": title, "PrimaryLocation": where, "PostedDate": "2026-09-27",
            "secondaryLocations": [{"Name": x} for x in also], "ShortDescriptionStr": "short"}


class OracleAPI:
    """Answers like the live API: an unknown locationId is ignored (all
    postings come back); the site number is ignored too."""

    def __init__(self, india, world_total=7478, facets=FACETS, status=200):
        self.india, self.world_total, self.facets, self.status = india, world_total, facets, status
        self.urls: list[str] = []

    def get(self, url):
        self.urls.append(url)
        if self.status != 200:
            return Result(url=url, status=self.status, json=None, text="")
        limit = int(re.search(r"limit=(\d+)", url).group(1))
        offset = int(re.search(r"offset=(\d+)", url).group(1))
        loc = re.search(r"locationId=(\d+)", url)
        india = loc and int(loc.group(1)) == INDIA
        reqs = self.india if india else [_req(900 + i, "Somewhere else") for i in range(3)]
        total = len(self.india) if india else self.world_total
        item = {"TotalJobsCount": total, "locationsFacet": self.facets,
                "requisitionList": reqs[offset:offset + limit]}
        return Result(url=url, status=200, json={"items": [item]}, text="")


# ── slugs ────────────────────────────────────────────────────────────────────

def test_a_slug_is_host_site_and_an_optional_location():
    os_ = P.parse_oracle_slug(SLUG)
    assert (os_.host, os_.site, os_.location, os_.tenant) == ("jpmc.fa.oraclecloud.com", "CX_1001", "India", "jpmc")
    assert P.parse_oracle_slug("jpmc.fa.oraclecloud.com@CX_1001").location is None


@pytest.mark.parametrize("bad", ["jpmc.fa.oraclecloud.com", "jpmc@CX_1001", "evil.com@CX_1001",
                                 "jpmc.fa.oraclecloud.com@CX/1001", "jpmc.fa.oraclecloud.com@CX_1001@", ""])
def test_anything_else_is_refused(bad):
    with pytest.raises(ValueError):
        P.parse_oracle_slug(bad)


# ── the board ────────────────────────────────────────────────────────────────

def test_the_location_is_looked_up_by_name_and_postings_read():
    api = OracleAPI([_req(1), _req(2, "Applied AI ML Associate", "Hyderabad, Telangana, India",
                                   also=["Bengaluru, Karnataka, India"]), _req(3)])
    p = P.probe(api, "oracle", SLUG, "JPMorgan Chase")
    assert p.status == OK and len(p.postings) == 3
    ai = p.postings[1]
    assert ai.id == "jpmc-210789002"                      # the "Job number" in JPMorgan's emails
    assert ai.location == "Hyderabad, Telangana, India; Bengaluru, Karnataka, India"
    assert ai.url == "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210789002"
    assert ai.updated_at == "2026-09-27"
    assert "limit=1," in api.urls[0] and f"locationId={INDIA}" in api.urls[1]


def test_a_misspelt_location_is_an_error_never_the_whole_world_or_empty():
    """An unknown locationId is ignored by Oracle: all 7,478 postings."""
    p = P.probe(OracleAPI([_req(1)]), "oracle", "jpmc.fa.oraclecloud.com@CX_1001@Indai")
    assert p.status == ERROR and "Indai" in p.error


def test_a_filter_that_did_not_apply_is_caught():
    """The facet says 3 in India; if the filtered answer disagrees, the filter
    was ignored and the postings are not India's."""
    facets = [{"Id": INDIA, "Name": "India", "TotalCount": 40}]
    p = P.probe(OracleAPI([_req(1), _req(2), _req(3)], facets=facets), "oracle", SLUG)
    assert p.status == ERROR and "not applied" in p.error


def test_no_location_on_a_huge_board_is_refused():
    p = P.probe(OracleAPI([]), "oracle", "jpmc.fa.oraclecloud.com@CX_1001")
    assert p.status == ERROR and "7478 postings" in p.error


def test_every_page_is_read(monkeypatch):
    monkeypatch.setattr(P, "ORACLE_PAGE", 2)
    reqs = [_req(i) for i in range(5)]
    facets = [{"Id": INDIA, "Name": "India", "TotalCount": 5}]
    api = OracleAPI(reqs, facets=facets)
    p = P.probe(api, "oracle", SLUG)
    assert p.status == OK and len(p.postings) == 5 and len(api.urls) == 1 + 3


def test_a_board_that_moved_while_paging_is_retried_next_poll(monkeypatch):
    """Newest first: one posting added mid-paging repeats one at a page edge."""
    monkeypatch.setattr(P, "ORACLE_PAGE", 2)
    reqs = [_req(0), _req(1), _req(1), _req(2)]
    facets = [{"Id": INDIA, "Name": "India", "TotalCount": 4}]
    p = P.probe(OracleAPI(reqs, facets=facets), "oracle", SLUG)
    assert p.status == ERROR and "changed while paging" in p.error


def test_a_404_is_not_found():
    assert P.probe(OracleAPI([], status=404), "oracle", SLUG).status == NOT_FOUND


# ── one job's text ───────────────────────────────────────────────────────────

class Detail:
    def __init__(self, rec):
        self.rec, self.urls = rec, []

    def get(self, url):
        self.urls.append(url)
        return Result(url=url, status=200, json={"items": [self.rec]}, text="")


def test_the_detail_gives_the_text_and_the_date():
    d = Detail({"ExternalDescriptionStr": "<div>Build <b>Java</b> services &amp; APIs</div>",
                "ExternalQualificationsStr": "<ul><li>5+ years</li></ul>", "ExternalResponsibilitiesStr": "",
                "ExternalPostedStartDate": "2026-09-28T03:41:00+00:00"})
    got = P.fetch_details(d, "oracle", SLUG, {"id": "jpmc-210790077"})
    assert "Java services & APIs" in " ".join(got["description"].split()) and "5+ years" in got["description"]
    assert got["updated_at"] == "2026-09-28T03:41:00+00:00"
    assert 'Id=%22210790077%22,siteNumber=CX_1001' in d.urls[0]


def test_a_detail_without_a_number_asks_nothing():
    d = Detail({})
    assert P.fetch_details(d, "oracle", SLUG, {"id": "jpmc-abc"}) == {} and not d.urls
