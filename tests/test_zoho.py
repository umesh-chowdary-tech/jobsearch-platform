"""Zoho Recruit: career pages, not an API. Shapes copied from live boards
(Siliconcedars, Minfy, Kanerika, OTSI, LoginRadius), 2026-09-26."""
from __future__ import annotations

import json

import pytest

from jobsearch import providers as P
from jobsearch.discovery import provider_slugs, search_company
from jobsearch.http import Result
from jobsearch.models import EMPTY, ERROR, NOT_FOUND, OK

HOST = "minfytech.zohorecruit.com"
LIST_URL = f"https://{HOST}/jobs/Careers"


def _listing(jobs, site="Minfy Technologies Private Limited") -> str:
    """The career page: the list sits HTML-escaped in a hidden input, near the
    end of 1.7 MB of page, after other hidden inputs."""
    val = json.dumps(jobs).replace("&", "&amp;").replace('"', "&#34;")
    return (f'<html><head><title>Jobs at Careers</title>'
            f'<meta property="og:site_name" content="{site}"></head><body>'
            f'<input type="hidden" value="{{&#34;detail&#34;:{{}}}}" id="pageJson">'
            f'<input type="hidden" value="{val}" id="jobs">'
            f'<input type="hidden" value="{{}}" id="meta"></body></html>')


def _js(records) -> str:
    """How the job page writes its record: JSON inside a JS string, with \\x22
    for quotes, \\- for hyphens and \\u for anything past ASCII."""
    s = json.dumps(records)
    s = s.replace("\\", "\\\\").replace('"', "\\x22").replace("-", "\\-").replace("'", "\\'")
    s = "".join(c if ord(c) < 128 else f"\\u{ord(c):04x}" for c in s)
    return f"<script>var CVRCust = {{}};\nvar jobs = JSON.parse('{s}');\n</script>"


JOBS = [
    {"Remote_Job": False, "Job_Type": "Full time", "Posting_Title": "Senior Software Engineer(Java)",
     "Country": "India", "City": "Hyderabad", "State": "Telangana", "Publish": True, "id": "496428000003889085"},
    {"Remote_Job": True, "Job_Type": "Training", "Posting_Title": "AI Internship – GenAI & Agentic AI",
     "Country": None, "City": None, "Publish": True, "id": "818455000000535319"},
    {"Remote_Job": False, "Posting_Title": "Unpublished", "Publish": False, "id": "1"},
]
MISSING = ('<html><head><title>Page does not exist</title></head><body><h2>x.zohorecruit.com '
           '<span>does not exist.</span></h2></body></html>')


class Pages:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def get(self, url):
        self.urls.append(url)
        status, text = self.pages.get(url, (404, ""))
        return Result(url=url, status=status, json=None, text=text)


# ── slugs ────────────────────────────────────────────────────────────────────

def test_a_slug_is_the_host_and_an_optional_portal():
    assert P.parse_zoho_slug(HOST).url == LIST_URL
    assert P.parse_zoho_slug("acme.zohorecruit.in@Tech-Jobs").url == "https://acme.zohorecruit.in/jobs/Tech-Jobs"


@pytest.mark.parametrize("bad", ["minfytech", "minfytech.zoho.com", "evil.com", "a.zohorecruit.com/Careers",
                                 "a.zohorecruit.com@../x", ""])
def test_anything_else_is_refused(bad):
    with pytest.raises(ValueError):
        P.parse_zoho_slug(bad)


# ── the board ────────────────────────────────────────────────────────────────

def test_the_career_page_is_read_into_postings():
    p = P.probe(Pages({LIST_URL: (200, _listing(JOBS))}), "zoho", HOST, "Minfy")
    assert p.status == OK and p.board_name == "Minfy Technologies Private Limited"
    first, second = p.postings                                    # the unpublished one is not a posting
    assert (first.id, first.title, first.location) == (
        "496428000003889085", "Senior Software Engineer(Java)", "Hyderabad, Telangana, India")
    assert first.url == f"{LIST_URL}/496428000003889085"
    assert second.title == "AI Internship – GenAI & Agentic AI" and second.location == "Remote"


def test_an_unknown_org_is_not_found_though_zoho_answers_200():
    p = P.probe(Pages({"https://nobody.zohorecruit.in/jobs/Careers": (200, MISSING)}), "zoho",
                "nobody.zohorecruit.in")
    assert p.status == NOT_FOUND


def test_a_page_without_a_job_list_is_an_error_not_an_empty_board():
    """EMPTY would tell the watcher every posting had closed."""
    page = _listing(JOBS).replace('id="jobs"', 'id="jobList"')
    p = P.probe(Pages({LIST_URL: (200, page)}), "zoho", HOST)
    assert p.status == ERROR and "no job list" in p.error


def test_a_real_empty_list_is_empty():
    assert P.probe(Pages({LIST_URL: (200, _listing([]))}), "zoho", HOST).status == EMPTY


def test_the_watcher_skips_the_board_name_request():
    """It is on the same page, so asking costs nothing - but it is not asked for."""
    p = P.probe(Pages({LIST_URL: (200, _listing(JOBS))}), "zoho", HOST, with_board_name=False)
    assert p.board_name is None and p.status == OK


# ── one job's page ───────────────────────────────────────────────────────────

RECORD = {"id": "496428000003889085", "Posting_Title": "Senior Software Engineer(Java)",
          "Job_Description": '<span id="spandesc"><p style="margin-top:0px">Build <b>Spring Boot</b> '
                             "services &amp; APIs – 5+ years, it's hands-on.</p></span>",
          "Date_Opened": "2026-08-24", "City": "Hyderabad", "State": "Telangana", "Country": "India",
          "Work_Experience": "5+ years", "Job_Type": "Full time", "Industry": "IT Services", "Remote_Job": False}


def test_a_job_page_gives_the_description_date_and_place():
    url = f"{LIST_URL}/{RECORD['id']}"
    got = P.fetch_details(Pages({url: (200, _js([RECORD]))}), "zoho", HOST, {"url": url, "id": RECORD["id"]})
    assert "Spring Boot services & APIs – 5+ years, it's hands-on." in " ".join(got["description"].split())
    assert "Work Experience: 5+ years" in got["description"]
    assert got["updated_at"] == "2026-08-24" and got["location"] == "Hyderabad, Telangana, India"


def test_the_real_escaping_decodes():
    """Copied from Siliconcedars' page: \\x22 quotes, \\- hyphens, and a
    backslash-escaped quote inside the HTML."""
    raw = (r"[{\x22Salary\x22:null,\x22Remote_Job\x22:false,\x22Posting_Title\x22:\x22MERN Full Stack "
           r"Software Engineer\x22,\x22Job_Description\x22:\x22<span id=\\\x22spandesc\\\x22>An "
           r"enthusiastic <b>MERN</b> engineer – 5\+ years\x22,\x22id\x22:\x22818455000001348001\x22}]")
    rec = json.loads(P._js_string(raw))[0]
    assert rec["Job_Description"] == '<span id="spandesc">An enthusiastic <b>MERN</b> engineer – 5+ years'


def test_a_closed_job_gives_nothing():
    url = f"{LIST_URL}/9"
    assert P.fetch_details(Pages({url: (200, "<html>no record</html>")}), "zoho", HOST, {"url": url}) == {}


# ── robots.txt, as Zoho serves it ────────────────────────────────────────────

ZOHO_ROBOTS = ("User-agent: *\nAllow: /recruit/Portal.na\nAllow: /careers\nAllow: /jobs\nAllow: /jobs/\n"
               "Allow: /jobs/*\nAllow: /forms/*\nAllow: /privacy-policy\n\nDisallow: /\n")


def test_zohos_robots_allow_the_career_pages_and_nothing_else(tmp_path):
    from jobsearch.robots import RobotsCache

    class Canned(RobotsCache):
        def _fetch(self, scheme, host):
            return ZOHO_ROBOTS, 200
    r = Canned(tmp_path / "robots", "test-agent")
    assert r.check(f"{LIST_URL}/496428000003889085").allowed
    assert not r.check(f"https://{HOST}/recruit/Login.do").allowed


# ── finding a board by company name ──────────────────────────────────────────

def test_search_tries_zoho_on_both_data_centres():
    assert provider_slugs("zoho", "Kanerika") == ["kanerika.zohorecruit.com", "kanerika.zohorecruit.in"]


def test_search_finds_a_zoho_board_after_the_json_atss_say_no():
    class Everywhere404(Pages):
        def get(self, url):
            self.urls.append(url)
            if url == "https://loginradius.zohorecruit.in/jobs/Careers":
                return Result(url=url, status=200, json=None, text=_listing(JOBS[:1], site="LoginRadius"))
            if "zohorecruit" in url:
                return Result(url=url, status=200, json=None, text=MISSING)
            return Result(url=url, status=404, json=None, text="")
    f = Everywhere404({})
    run = search_company(f, "LoginRadius")
    found = [p for p in run.probes if p.found]
    assert [(p.provider, p.slug) for p in found] == [("zoho", "loginradius.zohorecruit.in")]
    assert not run.escalations                                     # its own name is on the page
