"""amazon.jobs. The API is scripted; the shapes are from the live site,
2026-09-28."""
from __future__ import annotations

import urllib.parse

import pytest

from jobsearch import applications as A
from jobsearch import providers as P
from jobsearch.http import Result
from jobsearch.models import ERROR, OK

SLUG = "IND@software-development+machine-learning-science"


def _job(n, title="Software Dev Engineer II, AWS FinTech", where="Hyderabad, Telangana, IND"):
    return {"id_icims": str(10560000 + n), "id": f"uuid-{n}", "title": title, "normalized_location": where,
            "job_path": f"/en/jobs/{10560000 + n}/software-dev-engineer-ii", "posted_date": "September 28, 2026",
            "description": "Build <b>Java</b> services on AWS &amp; ship them.<br/>Own them in production.",
            "basic_qualifications": "- 3+ years of non-internship professional software development experience",
            "preferred_qualifications": "- Experience with LLMs", "job_category": "Software Development"}


class API:
    def __init__(self, jobs, hits=None):
        self.jobs, self.hits, self.urls = jobs, len(jobs) if hits is None else hits, []

    def get(self, url):
        self.urls.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        off, lim = int(q["offset"][0]), int(q["result_limit"][0])
        return Result(url=url, status=200, json={"hits": self.hits, "jobs": self.jobs[off:off + lim], "error": None},
                      text="")


def test_a_slug_is_a_country_and_amazons_own_categories():
    az = P.parse_amazon_slug(SLUG)
    assert az.country == "IND" and az.categories == ("software-development", "machine-learning-science")


@pytest.mark.parametrize("bad", ["IND", "india@software-development", "IND@", "IND@Software Development",
                                 "IND@a|b", ""])
def test_anything_else_is_refused(bad):
    with pytest.raises(ValueError):
        P.parse_amazon_slug(bad)


def test_the_search_asks_only_for_the_named_categories_in_the_country():
    api = API([_job(1)])
    P.probe(api, "amazon", SLUG)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(api.urls[0]).query)
    assert q["category[]"] == ["software-development", "machine-learning-science"]
    assert q["normalized_country_code[]"] == ["IND"] and q["result_limit"] == ["100"]


def test_postings_carry_their_whole_description():
    p = P.probe(API([_job(1)]), "amazon", SLUG)
    (post,) = p.postings
    assert p.status == OK and p.company == "Amazon"
    assert post.id == "amazon-10560001" and post.url == "https://www.amazon.jobs/en/jobs/10560001/software-dev-engineer-ii"
    assert post.updated_at == "2026-09-28" and post.location == "Hyderabad, Telangana, IND"
    body = " ".join(post.description.split())
    assert "Build Java services on AWS & ship them." in body
    assert "Basic qualifications - 3+ years" in body and "Preferred qualifications - Experience with LLMs" in body
    assert "amazon" not in P.DETAIL_PROVIDERS                 # nothing to fetch per posting


def test_every_page_is_read(monkeypatch):
    monkeypatch.setattr(P, "AMAZON_PAGE", 2)
    api = API([_job(i) for i in range(5)])
    p = P.probe(api, "amazon", SLUG)
    assert p.status == OK and len(p.postings) == 5 and len(api.urls) == 3


def test_no_postings_at_all_is_an_error_not_an_empty_board():
    """A misspelt category returns 0 hits (applied-science did, live). Empty
    would close every Amazon posting."""
    p = P.probe(API([]), "amazon", "IND@applied-science")
    assert p.status == ERROR and "category" in p.error


def test_too_many_is_refused():
    p = P.probe(API([_job(1)], hits=2368), "amazon", SLUG)
    assert p.status == ERROR and "2368 postings" in p.error


def test_a_board_that_moved_while_paging_is_retried(monkeypatch):
    monkeypatch.setattr(P, "AMAZON_PAGE", 2)
    p = P.probe(API([_job(0), _job(1), _job(1), _job(2)]), "amazon", SLUG)
    assert p.status == ERROR and "changed while paging" in p.error


def test_an_amazon_job_id_is_a_requisition_for_linking():
    """So a rejection email for one Amazon job never closes another on a
    similar title - the JPMorgan mistake."""
    assert A.posting_req("amazon-10561102") == "10561102"
