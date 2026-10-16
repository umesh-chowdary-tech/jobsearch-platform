"""The tier-1 ATS APIs: Greenhouse, Lever, Ashby, Workday and Oracle Recruiting
Cloud; Amazon's, Atlassian's and Microsoft's (Eightfold) job APIs; and Zoho
Recruit's career pages, which carry their job list as JSON.

Each provider knows how to build a URL from a slug and how to read that ATS's
particular JSON shape into a list of Postings. Nothing else in the codebase
should know these shapes.
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from .http import Blocked, Fetcher
from .models import BLOCKED, EMPTY, ERROR, NOT_FOUND, OK, BoardProbe, Posting


def _s(v: Any) -> str:
    return "" if v is None else str(v)


# ── Greenhouse ───────────────────────────────────────────────────────────────

def greenhouse_jobs_url(slug: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def greenhouse_board_url(slug: str) -> str:
    return f"https://boards-api.greenhouse.io/v1/boards/{slug}"


def parse_greenhouse(payload: Any) -> list[Posting]:
    jobs = (payload or {}).get("jobs") or []
    out = []
    for j in jobs:
        out.append(
            Posting(
                id=_s(j.get("id")),
                title=_s(j.get("title")),
                location=_s((j.get("location") or {}).get("name")),
                url=_s(j.get("absolute_url")),
                # updated_at moves whenever the company touches its board -
                # every posting at once - so it says nothing about age.
                updated_at=j.get("first_published") or j.get("updated_at"),
            )
        )
    return out


# ── Lever ────────────────────────────────────────────────────────────────────

def lever_jobs_url(slug: str) -> str:
    return f"https://api.lever.co/v0/postings/{slug}?mode=json"


def parse_lever(payload: Any) -> list[Posting]:
    if not isinstance(payload, list):
        return []
    out = []
    for j in payload:
        cats = j.get("categories") or {}
        # Lever splits the body across three fields plus a list of sections.
        # Requirements usually live in `lists`, so dropping it would lose most
        # of what triage needs to read.
        parts = [_s(j.get("descriptionPlain")), _s(j.get("additionalPlain"))]
        for section in j.get("lists") or []:
            parts.append(_s(section.get("text")))
            parts.append(re.sub(r"<[^>]+>", " ", _s(section.get("content"))))
        out.append(
            Posting(
                id=_s(j.get("id")),
                title=_s(j.get("text")),
                location=_s(cats.get("location")),
                url=_s(j.get("hostedUrl")),
                updated_at=_s(j.get("createdAt")) or None,
                description="\n".join(p for p in parts if p),
            )
        )
    return out


# ── Ashby ────────────────────────────────────────────────────────────────────

def ashby_jobs_url(slug: str) -> str:
    return f"https://api.ashbyhq.com/posting-api/job-board/{slug}"


def parse_ashby(payload: Any) -> list[Posting]:
    jobs = (payload or {}).get("jobs") or []
    out = []
    for j in jobs:
        out.append(
            Posting(
                id=_s(j.get("id")),
                title=_s(j.get("title")),
                location=_s(j.get("location")),
                url=_s(j.get("jobUrl")),
                updated_at=j.get("publishedAt"),
                description=_s(j.get("descriptionPlain")),
            )
        )
    return out


# ── Workday ──────────────────────────────────────────────────────────────────
#
# Not one API per company but one per career site, so the slug carries three
# parts, all read off the careers URL:
#
#   https://wf.wd1.myworkdayjobs.com/WellsFargoJobs  ->  wf.wd1.WellsFargoJobs
#
# plus an optional "@India" that narrows the poll to one country (see
# probe_workday for why a big employer needs it). Dots and "@", never "/" or
# ":": the slug becomes a file name in var/seen/, and ":" already separates
# provider from slug in every board key.
#
# The API takes a POST and pages 20 at a time - it answers 400 to anything more.

WORKDAY_PAGE = 20
# Past this, polling costs a minute of requests every fifteen minutes. Refuse
# and ask for a country instead of quietly polling a fraction of the board.
WORKDAY_MAX = 1000


@dataclass(frozen=True)
class WorkdaySite:
    tenant: str
    dc: str                 # data centre: wd1, wd3, wd5, ...
    site: str
    country: str | None = None

    @property
    def host(self) -> str:
        return f"{self.tenant}.{self.dc}.myworkdayjobs.com"

    @property
    def api(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"


def parse_workday_slug(slug: str) -> WorkdaySite:
    board, _, country = slug.partition("@")
    parts = board.split(".", 2)
    if len(parts) != 3 or not all(parts) or not re.fullmatch(r"wd\d+", parts[1]):
        raise ValueError(f"workday slug must be tenant.wdN.site[@Country], got {slug!r}")
    return WorkdaySite(parts[0], parts[1], parts[2], country.strip() or None)


def workday_id(tenant: str, external_path: str) -> str:
    """The posting's own number, with the tenant in front: wf-R-500001-1.

    The number is the tail of externalPath after its last "_"; what comes before
    it is the title, which gets edited in place. And unlike Greenhouse's ids it
    is unique only within one employer - plenty of them count R-100000 upward -
    while triage and the pipeline key on the bare id. Hence the tenant.
    """
    tail = external_path.rstrip("/").rsplit("/", 1)[-1]
    return f"{tenant}-{tail.rsplit('_', 1)[-1]}"


def parse_workday(payload: Any, ws: WorkdaySite) -> list[Posting]:
    out = []
    for j in (payload or {}).get("jobPostings") or []:
        path = _s(j.get("externalPath"))
        if not path:
            continue
        out.append(
            Posting(
                id=workday_id(ws.tenant, path),
                title=_s(j.get("title")),
                # "3 Locations" for a multi-city role. The real list comes with
                # the description, from workday_detail.
                location=_s(j.get("locationsText")),
                url=f"https://{ws.host}/{ws.site}{path}",
                # postedOn is "Posted 30+ Days Ago" - a bucket, not a date.
                # The real date comes with the description too.
                updated_at=None,
            )
        )
    return out


def workday_country_facet(facets: Any, country: str) -> tuple[bool, tuple[str, str] | None]:
    """(has a country facet at all, (facetParameter, id) for `country` or None).

    The facet ids are opaque per-tenant GUIDs, so they are looked up by name on
    every poll rather than written into the registry. Wells Fargo nests the
    country facet inside "locationMainGroup"; other tenants put it at the top.
    """
    want = country.casefold()
    has = False
    stack = list(facets or [])
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        param = _s(node.get("facetParameter"))
        values = node.get("values") or []
        if "country" in param.casefold():
            has = True
            for v in values:
                if isinstance(v, dict) and _s(v.get("descriptor")).casefold() == want:
                    return True, (param, _s(v.get("id")))
        else:
            stack.extend(values)
    return has, None


def _workday_search(offset: int, facets: dict[str, list[str]]) -> dict[str, Any]:
    return {"appliedFacets": facets, "limit": WORKDAY_PAGE, "offset": offset, "searchText": ""}


def probe_workday(
    fetcher: Fetcher, slug: str, company: str | None = None, with_board_name: bool = True
) -> BoardProbe:
    """Page through one Workday career site, all of it or one country of it.

    Wells Fargo has ~1,800 postings - 91 requests a poll - of which ~160 are in
    India. "@India" in the slug makes it 10. Past WORKDAY_MAX without a country
    the probe refuses rather than polling part of the board.

    Every page must land, or the whole probe is an error. A board that comes
    back short would look to the watcher like postings closing, and then
    reopening as "new" on the next poll.
    """
    p = BoardProbe(company=company or slug, provider="workday", slug=slug, status=ERROR, url="")
    try:
        ws = parse_workday_slug(slug)
    except ValueError as exc:
        p.error = str(exc)
        return p
    p.url = f"{ws.api}/jobs"

    try:
        facets: dict[str, list[str]] = {}
        res = fetcher.post(p.url, _workday_search(0, facets))
        p.http_status = res.status
        # 404 is an unknown site; 422 is an unknown tenant, or the right tenant
        # on the wrong data centre (checked live, 2026-09-24).
        if res.status in (404, 422):
            p.status, p.error = NOT_FOUND, f"HTTP {res.status}"
            return p
        if not _page_ok(p, res):
            return p

        if ws.country:
            has, hit = workday_country_facet(res.json.get("facets"), ws.country)
            if not has:
                p.error = f"no country facet on this site - cannot narrow to {ws.country}"
                return p
            if hit is None:
                p.status = EMPTY            # a real board, nothing in that country
                return p
            facets = {hit[0]: [hit[1]]}
            res = fetcher.post(p.url, _workday_search(0, facets))
            if not _page_ok(p, res):
                return p

        # Only the first page's total is real. Later pages report 0 - seen
        # live - and a loop that re-read it would stop after page two.
        total = int(res.json.get("total") or 0)
        if total > WORKDAY_MAX:
            p.error = (f"{total} postings - too many to poll; narrow the slug to "
                       f"one country, e.g. {slug}@India")
            return p

        posts: list[Posting] = []
        hidden: set[tuple[str, ...]] = set()
        offset = 0
        while True:
            jobs = res.json.get("jobPostings") or []
            posts.extend(parse_workday(res.json, ws))
            # Wells Fargo lists a posting it will not show - {"bulletFields":
            # ["R-500002"]}, no title, no path (seen live 2026-09-24). It counts
            # toward total, so it counts here too, but with nothing to apply to
            # it never becomes a Posting.
            hidden.update(
                tuple(_s(x) for x in j.get("bulletFields") or [])
                for j in jobs if isinstance(j, dict) and not j.get("externalPath")
            )
            offset += WORKDAY_PAGE
            if not jobs or offset >= total:
                break
            res = fetcher.post(p.url, _workday_search(offset, facets))
            if not _page_ok(p, res):
                return p
    except Blocked as exc:
        p.status, p.error = BLOCKED, str(exc)
        return p
    except Exception as exc:
        p.status, p.error = ERROR, f"{type(exc).__name__}: {exc}"
        return p

    # A posting added or closed mid-paging shifts every later page by one: one
    # posting is seen twice and another not at all. Retrying next poll is
    # cheaper than a watcher that believes a live posting closed.
    unique = {post.id: post for post in posts}
    if len(unique) + len(hidden) != total:
        p.error = (f"board changed while paging ({len(unique) + len(hidden)} of {total})"
                   " - next poll retries")
        return p

    p.postings = list(unique.values())
    p.status = OK if p.postings else EMPTY
    if with_board_name and p.postings:
        p.board_name = workday_detail(fetcher, slug, p.postings[0].url).get("employer")
    return p


def _page_ok(p: BoardProbe, res: Any) -> bool:
    if res.status != 200:
        p.status, p.error = ERROR, f"HTTP {res.status}"
        return False
    if not isinstance(res.json, dict):
        p.status, p.error = ERROR, "response was not JSON"
        return False
    return True


def workday_detail(fetcher: Fetcher, slug: str, url: str) -> dict[str, str]:
    """What Workday's list withholds: description, every location, the date.

    Fetched on demand, for new postings only, the same as Greenhouse. Returns
    only the fields it actually got - an empty dict on failure - so a caller
    merging it in never overwrites a field with a blank.
    """
    try:
        ws = parse_workday_slug(slug)
    except ValueError:
        return {}
    path = urllib.parse.urlsplit(url).path
    prefix = f"/{ws.site}/"
    if not path.startswith(prefix):
        return {}
    try:
        res = fetcher.get(f"{ws.api}/{path[len(prefix):]}")
    except Exception:
        return {}
    if res.status != 200 or not isinstance(res.json, dict):
        return {}

    info = res.json.get("jobPostingInfo") or {}
    out: dict[str, str] = {}
    body = re.sub(r"<[^>]+>", " ", html.unescape(_s(info.get("jobDescription"))))
    if body.strip():
        out["description"] = body
    places = [_s(info.get("location"))] + [_s(x) for x in info.get("additionalLocations") or []]
    places = [x for x in places if x]
    if places:
        out["location"] = "; ".join(places)
    if info.get("startDate"):
        out["updated_at"] = _s(info.get("startDate"))
    employer = _s((res.json.get("hiringOrganization") or {}).get("name"))
    if employer:
        out["employer"] = employer
    return out


# ── Amazon ───────────────────────────────────────────────────────────────────
# amazon.jobs has its own search API, /en/search.json, and robots.txt only
# closes /internal. Each job arrives with its whole description, so there is
# nothing to fetch per posting. Checked live 2026-09-28.
#
# India alone is 2,368 postings, mostly warehouse, finance and support roles
# that triage would reject after downloading them - 22 pages a poll. So the
# slug names Amazon's own job categories and only those are asked for:
#   IND@software-development+machine-learning-science+...
# (Applied Science's slug is machine-learning-science; applied-science is 0.)
# An unknown category returns nothing rather than being ignored, so a board
# that comes back with no postings at all is an error, never "empty".

AMAZON_PAGE = 100            # 200 returns nothing
AMAZON_MAX = 1000
AMAZON_API = "https://www.amazon.jobs/en/search.json"


@dataclass(frozen=True)
class AmazonSearch:
    country: str
    categories: tuple[str, ...]


def parse_amazon_slug(slug: str) -> AmazonSearch:
    country, _, cats = slug.strip().partition("@")
    categories = tuple(c for c in cats.split("+") if c)
    if not re.fullmatch(r"[A-Z]{3}", country) or not categories or \
            not all(re.fullmatch(r"[a-z0-9-]+", c) for c in categories):
        raise ValueError(f"amazon slug must be <COUNTRY>@<category>[+<category>...], got {slug!r}")
    return AmazonSearch(country, categories)


def _amazon_params(az: AmazonSearch, offset: int) -> str:
    q = [("normalized_country_code[]", az.country), ("result_limit", str(AMAZON_PAGE)),
         ("offset", str(offset)), ("sort", "recent")] + [("category[]", c) for c in az.categories]
    return f"{AMAZON_API}?{urllib.parse.urlencode(q)}"


def _amazon_date(s: str) -> str | None:
    from datetime import datetime
    try:
        return datetime.strptime(s.strip(), "%B %d, %Y").date().isoformat()   # "September 28, 2026"
    except ValueError:
        return None


def parse_amazon(payload: Any) -> list[Posting]:
    out = []
    for j in (payload or {}).get("jobs") or []:
        jid = _s(j.get("id_icims"))
        if not jid:
            continue
        parts = [("", j.get("description")), ("Basic qualifications", j.get("basic_qualifications")),
                 ("Preferred qualifications", j.get("preferred_qualifications"))]
        body = "\n".join(f"{h}\n{re.sub(r'<[^>]+>', ' ', html.unescape(_s(t)))}".strip()
                         for h, t in parts if t)
        out.append(Posting(
            id=f"amazon-{jid}",               # the Job ID on amazon.jobs and in its emails
            title=_s(j.get("title")),
            location=_s(j.get("normalized_location")),
            url=f"https://www.amazon.jobs{_s(j.get('job_path'))}",
            updated_at=_amazon_date(_s(j.get("posted_date"))),
            description=body,
        ))
    return out


def probe_amazon(
    fetcher: Fetcher, slug: str, company: str | None = None, with_board_name: bool = True
) -> BoardProbe:
    p = BoardProbe(company=company or "Amazon", provider="amazon", slug=slug, status=ERROR, url="")
    try:
        az = parse_amazon_slug(slug)
    except ValueError as exc:
        p.error = str(exc)
        return p
    p.url = _amazon_params(az, 0)
    posts: list[Posting] = []
    try:
        res = fetcher.get(p.url)
        p.http_status = res.status
        if not _page_ok(p, res):
            return p
        total = int(res.json.get("hits") or 0)
        if total == 0:
            p.error = "no postings in those categories - check the category names in the slug"
            return p
        if total > AMAZON_MAX:
            p.error = f"{total} postings - too many to poll; name fewer categories"
            return p
        offset = 0
        while True:
            page = parse_amazon(res.json)
            posts.extend(page)
            offset += AMAZON_PAGE
            if not page or offset >= total:
                break
            res = fetcher.get(_amazon_params(az, offset))
            if not _page_ok(p, res):
                return p
    except Blocked as exc:
        p.status, p.error = BLOCKED, str(exc)
        return p
    except Exception as exc:
        p.status, p.error = ERROR, f"{type(exc).__name__}: {exc}"
        return p

    unique = {post.id: post for post in posts}
    if len(unique) != total:
        p.status = ERROR
        p.error = f"board changed while paging ({len(unique)} of {total}) - next poll retries"
        return p
    p.postings = list(unique.values())
    p.status = OK
    return p


# ── Atlassian ────────────────────────────────────────────────────────────────
# atlassian.com's careers page reads /endpoint/careers/listings: every open
# job worldwide (287 on 2026-09-28) in one JSON answer, descriptions included.
# The team and country filters on the page are the browser's own, so they are
# applied here: slug `India@Engineering` (teams joined with "+"). A team or
# country that appears nowhere in the answer is an error, not an empty board -
# a typo must not close every posting. Applications go through iCIMS.

ATLASSIAN_API = "https://www.atlassian.com/endpoint/careers/listings"


def parse_atlassian_slug(slug: str) -> tuple[str, tuple[str, ...]]:
    country, _, teams = slug.strip().partition("@")
    names = tuple(t.strip() for t in teams.split("+") if t.strip())
    if not re.fullmatch(r"[A-Za-z][A-Za-z ]+", country) or not names or \
            not all(re.fullmatch(r"[\w &,-]+", t) for t in names):
        raise ValueError(f"atlassian slug must be <Country>@<Team>[+<Team>...], got {slug!r}")
    return country.strip(), names


def _in_country(loc: str, country: str) -> bool:
    return bool(re.search(rf"\b{re.escape(country)}\b", loc, re.I))


def parse_atlassian(jobs: Any, country: str, teams: tuple[str, ...]) -> list[Posting]:
    out = []
    for j in jobs if isinstance(jobs, list) else []:
        locs = [_s(x) for x in j.get("locations") or []]
        if _s(j.get("category")) not in teams or not any(_in_country(l, country) for l in locs):
            continue
        # "Bengaluru - India -   Bengaluru,  560071 India" -> "Bengaluru - India"
        places = [" - ".join(p.strip() for p in l.split(" - ")[:2]) for l in locs if _in_country(l, country)]
        body = "\n".join(re.sub(r"<[^>]+>", " ", html.unescape(_s(j.get(k))))
                         for k in ("overview", "responsibilities", "qualifications") if j.get(k))
        out.append(Posting(
            id=f"atlassian-{_s(j.get('id'))}",
            title=_s(j.get("title")),
            location="; ".join(dict.fromkeys(places)),
            url=f"https://www.atlassian.com/company/careers/details/{_s(j.get('id'))}",
            updated_at=None,
            description=body,
        ))
    return out


def probe_atlassian(
    fetcher: Fetcher, slug: str, company: str | None = None, with_board_name: bool = True
) -> BoardProbe:
    p = BoardProbe(company=company or "Atlassian", provider="atlassian", slug=slug, status=ERROR,
                   url=ATLASSIAN_API)
    try:
        country, teams = parse_atlassian_slug(slug)
    except ValueError as exc:
        p.error = str(exc)
        return p
    try:
        res = fetcher.get(ATLASSIAN_API)
    except Blocked as exc:
        p.status, p.error = BLOCKED, str(exc)
        return p
    except Exception as exc:
        p.error = f"{type(exc).__name__}: {exc}"
        return p
    p.http_status = res.status
    if res.status != 200 or not isinstance(res.json, list) or not res.json:
        p.error = f"HTTP {res.status}" if res.status != 200 else "no job list in the answer"
        return p
    known = {_s(j.get("category")) for j in res.json if isinstance(j, dict)}
    missing = [t for t in teams if t not in known]
    if missing:
        p.error = f"no team named {', '.join(missing)} - teams today: {', '.join(sorted(known))}"
        return p
    if not any(_in_country(_s(l), country) for j in res.json for l in (j.get("locations") or [])):
        p.error = f"no posting anywhere in {country!r} - check the spelling"
        return p
    p.postings = parse_atlassian(res.json, country, teams)
    p.status = OK if p.postings else EMPTY
    return p


# ── Microsoft (Eightfold) ────────────────────────────────────────────────────
# apply.careers.microsoft.com runs on Eightfold. /api/pcsx/search pages the
# list, ten a page whatever `num` says; /api/pcsx/position_details gives one
# job's description. robots.txt allows /careers and /api/pcsx (the older
# /api/apply answers 403 "Not authorized for PCSX"). The host rate-limits
# bursts, so http.HOST_INTERVAL spaces its requests ten seconds apart.
#
# India is 229 postings; its `profession` filter narrows it the way Amazon's
# categories do: software engineering, research/applied/data sciences and
# security engineering are 99 (79 + the rest) - ten pages. A misspelt
# profession returns 0, so a board with no postings is an error, not empty.
#
# Each job has two ids: Eightfold's (in the URL) and Microsoft's job number
# (displayJobId, "200057883") - the "Job number" in Microsoft's emails, so it
# is the posting id and the inbox links on it.

MS_BASE = "https://apply.careers.microsoft.com"
MS_PAGE = 10
MS_MAX = 600                  # at ten seconds a page, a minute and a half a poll already


def parse_ms_slug(slug: str) -> tuple[str, tuple[str, ...]]:
    """`india@software engineering+security engineering`: Microsoft's location
    search term, then its profession filter values."""
    location, _, profs = slug.strip().partition("@")
    names = tuple(p.strip() for p in profs.split("+") if p.strip())
    if not re.fullmatch(r"[a-z][a-z ]*", location) or not names or \
            not all(re.fullmatch(r"[a-z][a-z ,&]*", p) for p in names):
        raise ValueError(f"microsoft slug must be <location>@<profession>[+<profession>...], got {slug!r}")
    return location.strip(), names


def _ms_search_url(location: str, professions: tuple[str, ...], start: int) -> str:
    q = [("domain", "microsoft.com"), ("query", ""), ("location", location), ("start", str(start)),
         ("sort_by", "timestamp"), ("filter_include_remote", "1")] + [("filter_profession", p) for p in professions]
    return f"{MS_BASE}/api/pcsx/search?{urllib.parse.urlencode(q)}"


def parse_ms(data: Any) -> list[Posting]:
    from datetime import datetime, timezone
    out = []
    for j in (data or {}).get("positions") or []:
        number, eid = _s(j.get("displayJobId")), _s(j.get("id"))
        if not number or not eid:
            continue
        ts = j.get("postedTs")
        out.append(Posting(
            id=f"microsoft-{number}",
            title=_s(j.get("name")),
            location="; ".join(_s(x) for x in (j.get("standardizedLocations") or j.get("locations") or [])),
            url=f"{MS_BASE}/careers/job/{eid}",
            updated_at=(datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat()
                        if isinstance(ts, (int, float)) and ts > 0 else None),
        ))
    return out


def probe_ms(fetcher: Fetcher, slug: str, company: str | None = None, with_board_name: bool = True) -> BoardProbe:
    p = BoardProbe(company=company or "Microsoft", provider="microsoft", slug=slug, status=ERROR, url="")
    try:
        location, professions = parse_ms_slug(slug)
    except ValueError as exc:
        p.error = str(exc)
        return p
    p.url = _ms_search_url(location, professions, 0)

    def page(start: int) -> dict[str, Any] | None:
        res = fetcher.get(_ms_search_url(location, professions, start))
        p.http_status = res.status
        if not _page_ok(p, res) or not isinstance(res.json.get("data"), dict):
            p.error = p.error or "no search result in the answer"
            return None
        return res.json["data"]

    posts: list[Posting] = []
    try:
        data = page(0)
        if data is None:
            return p
        total = int(data.get("count") or 0)
        if total == 0:
            p.error = "no postings for that location and those professions - check the names in the slug"
            return p
        if total > MS_MAX:
            p.error = f"{total} postings - too many to poll at this host's pace; name fewer professions"
            return p
        start = 0
        while True:
            batch = parse_ms(data)
            posts.extend(batch)
            start += MS_PAGE
            if not batch or start >= total:
                break
            if (data := page(start)) is None:
                return p
    except Blocked as exc:
        p.status, p.error = BLOCKED, str(exc)
        return p
    except Exception as exc:
        p.status, p.error = ERROR, f"{type(exc).__name__}: {exc}"
        return p

    unique = {post.id: post for post in posts}
    if len(unique) != total:
        p.status = ERROR
        p.error = f"board changed while paging ({len(unique)} of {total}) - next poll retries"
        return p
    p.postings = list(unique.values())
    p.status = OK
    return p


def ms_detail(fetcher: Fetcher, url: str) -> dict[str, str]:
    eid = urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    if not eid.isdigit():
        return {}
    q = urllib.parse.urlencode({"position_id": eid, "domain": "microsoft.com", "hl": "en"})
    try:
        res = fetcher.get(f"{MS_BASE}/api/pcsx/position_details?{q}")
    except Exception:
        return {}
    data = res.json.get("data") if res.status == 200 and isinstance(res.json, dict) else None
    if not isinstance(data, dict):
        return {}
    body = re.sub(r"<[^>]+>", " ", html.unescape(_s(data.get("jobDescription"))))
    return {"description": body} if body.strip() else {}


# ── Oracle Recruiting Cloud ──────────────────────────────────────────────────
# The "CandidateExperience" career sites (JPMorgan's jpmc.fa.oraclecloud.com)
# read a public REST API: recruitingCEJobRequisitions pages the list,
# recruitingCEJobRequisitionDetails gives one job's text. Checked live
# 2026-09-28 on JPMorgan: 325 India postings, 4 requests at 100 a page, and
# the unique ids added up to the total.
#
# Two things it silently ignores, and what that means here:
#   - the site number: CX_9999 returned the same 325 jobs as CX_1001.
#   - an unknown locationId: it returned all 7,478 postings worldwide.
# So the location is written by name (`@India`), its id looked up in the
# answer's own location facets on every poll, and the filtered total must
# equal that facet's count - or the probe is an error, never a flood of new
# postings. /robots.txt on these hosts is refused by Oracle's firewall for
# every client, browsers included; RFC 9309 reads a 4xx as no restrictions.

ORACLE_PAGE = 100
ORACLE_MAX = 1000
ORACLE_HOST = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.oraclecloud\.com")


@dataclass(frozen=True)
class OracleSite:
    host: str
    site: str
    location: str | None = None

    @property
    def api(self) -> str:
        return f"https://{self.host}/hcmRestApi/resources/latest"

    @property
    def tenant(self) -> str:
        return self.host.split(".", 1)[0]

    def job_url(self, rid: str) -> str:
        return f"https://{self.host}/hcmUI/CandidateExperience/en/sites/{self.site}/job/{rid}"


def parse_oracle_slug(slug: str) -> OracleSite:
    """`jpmc.fa.oraclecloud.com@CX_1001@India`: the host, the site number from
    the careers URL (/sites/CX_1001/), and optionally a location by name."""
    parts = slug.strip().split("@")
    if len(parts) not in (2, 3) or not ORACLE_HOST.fullmatch(parts[0].lower()) \
            or not re.fullmatch(r"[A-Za-z0-9_]+", parts[1]) or (len(parts) == 3 and not parts[2].strip()):
        raise ValueError(f"oracle slug must be <host>.oraclecloud.com@<SiteNumber>[@Location], got {slug!r}")
    return OracleSite(parts[0].lower(), parts[1], parts[2].strip() if len(parts) == 3 else None)


def _oracle_list_url(os_: OracleSite, offset: int, location_id: str | None, limit: int = ORACLE_PAGE) -> str:
    loc = f",locationId={location_id}" if location_id else ""
    return (f"{os_.api}/recruitingCEJobRequisitions?onlyData=true"
            f"&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={os_.site},"
            f"limit={limit},offset={offset}{loc},sortBy=POSTING_DATES_DESC")


def _oracle_item(res: Any) -> dict[str, Any] | None:
    items = res.json.get("items") if isinstance(res.json, dict) else None
    return items[0] if items and isinstance(items[0], dict) else None


def parse_oracle(item: Any, os_: OracleSite) -> list[Posting]:
    out = []
    for q in (item or {}).get("requisitionList") or []:
        rid = _s(q.get("Id"))
        if not rid:
            continue
        places = [_s(q.get("PrimaryLocation"))] + [
            _s(s.get("Name")) for s in q.get("secondaryLocations") or [] if isinstance(s, dict)]
        out.append(Posting(
            # Requisition numbers are per-employer and 9 digits, like
            # Greenhouse ids; the tenant in front keeps them apart. It is also
            # the "Job number" an employer's emails quote: jpmc-210000001.
            id=f"{os_.tenant}-{rid}",
            title=_s(q.get("Title")),
            location="; ".join(dict.fromkeys(p for p in places if p)),
            url=os_.job_url(rid),
            updated_at=_s(q.get("PostedDate")) or None,
        ))
    return out


def probe_oracle(
    fetcher: Fetcher, slug: str, company: str | None = None, with_board_name: bool = True
) -> BoardProbe:
    p = BoardProbe(company=company or slug, provider="oracle", slug=slug, status=ERROR, url="")
    try:
        os_ = parse_oracle_slug(slug)
    except ValueError as exc:
        p.error = str(exc)
        return p
    p.url = f"https://{os_.host}/hcmUI/CandidateExperience/en/sites/{os_.site}/jobs"
    try:
        # With a location, this first answer is only for its facet list.
        res = fetcher.get(_oracle_list_url(os_, 0, None, limit=1 if os_.location else ORACLE_PAGE))
        p.http_status = res.status
        if res.status == 404:
            p.status, p.error = NOT_FOUND, "HTTP 404"
            return p
        if not _page_ok(p, res) or (item := _oracle_item(res)) is None:
            p.error = p.error or "no search result in the answer"
            return p

        location_id = None
        if os_.location:
            want = os_.location.casefold()
            hit = next((f for f in item.get("locationsFacet") or []
                        if isinstance(f, dict) and _s(f.get("Name")).casefold() == want), None)
            if hit is None:
                # Facets list only places with postings - but a typo looks the
                # same, and "empty" would close every posting on the board.
                p.error = f"no location named {os_.location!r} in the site's list - check the spelling"
                return p
            location_id, expected = _s(hit.get("Id")), int(hit.get("TotalCount") or 0)
            res = fetcher.get(_oracle_list_url(os_, 0, location_id))
            if not _page_ok(p, res) or (item := _oracle_item(res)) is None:
                p.error = p.error or "no search result in the answer"
                return p
            if int(item.get("TotalJobsCount") or 0) != expected:
                p.error = (f"location filter not applied: {item.get('TotalJobsCount')} postings, "
                           f"{expected} in {os_.location}")
                return p

        total = int(item.get("TotalJobsCount") or 0)
        if total > ORACLE_MAX:
            p.error = (f"{total} postings - too many to poll; narrow the slug to one country, "
                       f"e.g. {slug}@India")
            return p
        posts: list[Posting] = []
        offset = 0
        while True:
            page = parse_oracle(item, os_)
            posts.extend(page)
            offset += ORACLE_PAGE
            if not page or offset >= total:
                break
            res = fetcher.get(_oracle_list_url(os_, offset, location_id))
            if not _page_ok(p, res) or (item := _oracle_item(res)) is None:
                p.error = p.error or "no search result in the answer"
                return p
    except Blocked as exc:
        p.status, p.error = BLOCKED, str(exc)
        return p
    except Exception as exc:
        p.status, p.error = ERROR, f"{type(exc).__name__}: {exc}"
        return p

    unique = {post.id: post for post in posts}
    if len(unique) != total:
        # Newest first: a posting added mid-paging shifts every later page.
        p.status = ERROR
        p.error = f"board changed while paging ({len(unique)} of {total}) - next poll retries"
        return p
    p.postings = list(unique.values())
    p.status = OK if p.postings else EMPTY
    return p


def oracle_detail(fetcher: Fetcher, slug: str, posting_id: str) -> dict[str, str]:
    """The job's text and its posting date. {} on any failure."""
    try:
        os_ = parse_oracle_slug(slug)
    except ValueError:
        return {}
    rid = posting_id.split("-", 1)[-1]
    if not rid.isdigit():
        return {}
    url = (f"{os_.api}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
           f"&finder=ById;Id=%22{rid}%22,siteNumber={os_.site}")
    try:
        res = fetcher.get(url)
    except Exception:
        return {}
    if res.status != 200 or (rec := _oracle_item(res)) is None:
        return {}
    parts = [re.sub(r"<[^>]+>", " ", html.unescape(_s(rec.get(k))))
             for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")]
    out: dict[str, str] = {}
    body = "\n".join(x for x in parts if x.strip())
    if body.strip():
        out["description"] = body
    if rec.get("ExternalPostedStartDate"):
        out["updated_at"] = _s(rec.get("ExternalPostedStartDate"))
    return out


# ── Zoho Recruit ─────────────────────────────────────────────────────────────
# No public JSON API. The career site is https://<org>.zohorecruit.<dc>/jobs/<Portal>
# ("Careers" unless renamed), and robots.txt allows /jobs. Its page carries the
# job list as JSON in a hidden input (id="jobs"): id, title, city/state/country,
# remote, type. Each job's page carries the whole record, description and
# Date_Opened included, as a JavaScript string: `var jobs = JSON.parse('...')`.
# Checked live 2026-09-26 on Minfy (72 jobs), Kanerika, OTSI, LoginRadius:
# every listed job had a page, and no closed job was listed.
#
# An unknown org still answers 200 - a 2.6 KB "<title>Page does not exist"
# page. A career page with no job list in it is an ERROR, never EMPTY: empty
# would tell the watcher every posting on the board had closed.

ZOHO_HOST = re.compile(r"[a-z0-9][a-z0-9-]*\.zohorecruit\.(?:com|in|eu|com\.au|jp|ca|com\.cn|sa)")
_ZOHO_LIST = re.compile(r'<input type="hidden" value="([^"]*)" id="jobs"')
_ZOHO_RECORD = re.compile(r"var jobs = JSON\.parse\('((?:[^'\\]|\\.)*)'\)")
_ZOHO_SITE = re.compile(r'<meta[^>]+property="og:site_name"[^>]+content="([^"]*)"')
ZOHO_MISSING = "<title>Page does not exist</title>"


@dataclass(frozen=True)
class ZohoSite:
    host: str
    portal: str = "Careers"

    @property
    def url(self) -> str:
        return f"https://{self.host}/jobs/{self.portal}"


def parse_zoho_slug(slug: str) -> ZohoSite:
    """`minfytech.zohorecruit.com`, or `acme.zohorecruit.in@OtherPortal` for a
    portal not named Careers. The whole host, because the data centre (.com,
    .in, .eu ...) is part of where the board lives. "@", never "/": the slug
    becomes a file name in var/seen/, as Workday's does."""
    host, _, portal = slug.strip().partition("@")
    host = host.lower()
    if not ZOHO_HOST.fullmatch(host) or (portal and not re.fullmatch(r"[\w-]+", portal)):
        raise ValueError(f"zoho slug must be <org>.zohorecruit.<com|in|eu|...>[@Portal], got {slug!r}")
    return ZohoSite(host, portal or "Careers")


def _js_string(s: str) -> str:
    """Undo a JavaScript string literal's escapes: \\x22, \\u2013, \\n, \\-."""
    def one(m: re.Match) -> str:
        c = m.group(1)
        if c[0] in "xu" and len(c) > 1:
            return chr(int(c[1:], 16))
        return {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}.get(c, c)
    return re.sub(r"\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)", one, s, flags=re.S)


def _zoho_place(j: dict[str, Any]) -> str:
    parts = [_s(j.get(k)).strip() for k in ("City", "State", "Country") if j.get(k)]
    if j.get("Remote_Job"):
        parts.append("Remote")
    return ", ".join(p for p in parts if p)


def parse_zoho(jobs: Any, zs: ZohoSite) -> list[Posting]:
    out = []
    for j in jobs if isinstance(jobs, list) else []:
        jid = _s((j or {}).get("id"))
        if not jid or j.get("Publish") is False:
            continue
        out.append(Posting(
            id=jid,                                  # 18 digits, the org's prefix built in
            title=_s(j.get("Posting_Title") or j.get("Job_Opening_Name")),
            location=_zoho_place(j),
            url=f"{zs.url}/{jid}",                   # the title part of Zoho's URL is optional
            updated_at=None,                         # Date_Opened is on the job's own page
        ))
    return out


def probe_zoho(
    fetcher: Fetcher, slug: str, company: str | None = None, with_board_name: bool = True
) -> BoardProbe:
    p = BoardProbe(company=company or slug, provider="zoho", slug=slug, status=ERROR, url="")
    try:
        zs = parse_zoho_slug(slug)
    except ValueError as exc:
        p.error = str(exc)
        return p
    p.url = zs.url
    try:
        res = fetcher.get(p.url)
    except Blocked as exc:
        p.status, p.error = BLOCKED, str(exc)
        return p
    except Exception as exc:
        p.status, p.error = ERROR, f"{type(exc).__name__}: {exc}"
        return p

    p.http_status = res.status
    if res.status == 404 or ZOHO_MISSING in (res.text or "")[:4000]:
        p.status = NOT_FOUND
        return p
    if res.status != 200:
        p.error = f"HTTP {res.status}"
        return p
    m = _ZOHO_LIST.search(res.text or "")
    if not m:
        p.error = "no job list in the career page - Zoho may have changed its site"
        return p
    try:
        jobs = json.loads(html.unescape(m.group(1)))
    except ValueError:
        p.error = "the career page's job list was not JSON"
        return p
    p.postings = parse_zoho(jobs, zs)
    p.status = OK if p.postings else EMPTY
    if with_board_name:
        site = _ZOHO_SITE.search(res.text or "")
        p.board_name = html.unescape(site.group(1)).strip() if site else None
    return p


def zoho_detail(fetcher: Fetcher, url: str) -> dict[str, str]:
    """Description, date opened and the full location, from the job's page.
    Only fields actually found; {} for a closed job or any failure."""
    jid = urllib.parse.urlsplit(url).path.rstrip("/").split("/")[-1]
    try:
        res = fetcher.get(url)
    except Exception:
        return {}
    m = _ZOHO_RECORD.search(res.text or "") if res.status == 200 else None
    if not m:
        return {}
    try:
        records = json.loads(_js_string(m.group(1)))
    except ValueError:
        return {}
    rec = next((r for r in records if isinstance(r, dict) and _s(r.get("id")) == jid), None)
    if rec is None:
        return {}
    out: dict[str, str] = {}
    body = re.sub(r"<[^>]+>", " ", html.unescape(_s(rec.get("Job_Description"))))
    extra = [f"{k.replace('_', ' ')}: {_s(rec.get(k))}" for k in ("Work_Experience", "Job_Type", "Industry")
             if rec.get(k)]
    if body.strip():
        out["description"] = "\n".join([body, *extra])
    if rec.get("Date_Opened"):
        out["updated_at"] = _s(rec.get("Date_Opened"))
    place = _zoho_place(rec)
    if place:
        out["location"] = place
    return out


PROVIDERS: dict[str, dict[str, Any]] = {
    "greenhouse": {
        "jobs_url": greenhouse_jobs_url,
        "parse": parse_greenhouse,
        "board_url": greenhouse_board_url,
        "board_name_key": "name",
    },
    "lever": {
        "jobs_url": lever_jobs_url,
        "parse": parse_lever,
        "board_url": None,     # Lever exposes no board-metadata endpoint
        "board_name_key": None,
    },
    "ashby": {
        "jobs_url": ashby_jobs_url,
        "parse": parse_ashby,
        "board_url": None,     # name arrives inside the jobs payload, if at all
        "board_name_key": None,
    },
}

# Workday is not in PROVIDERS: it needs a POST and paging, not one GET, and its
# slugs cannot be guessed from a company name, so discovery's search must never
# try it. Oracle is the same: a host and a site number, never guessable, and
# Amazon and Atlassian are one company each with their own API. Zoho reads
# HTML, not JSON; discovery guesses its hosts itself. All are reached through
# probe() like the rest.
ALL_PROVIDERS = (*PROVIDERS, "workday", "oracle", "amazon", "atlassian", "microsoft", "zoho")


def probe(
    fetcher: Fetcher,
    provider: str,
    slug: str,
    company: str | None = None,
    with_board_name: bool = True,
) -> BoardProbe:
    """Ask one ATS whether `slug` is a live board, and what is on it.

    `with_board_name=False` skips the extra metadata request Greenhouse needs.
    Discovery wants the name to check identity; the watcher polls every fifteen
    minutes and does not, so it would be doubling its request count to re-learn
    something that has not changed.
    """
    if provider == "workday":
        return probe_workday(fetcher, slug, company, with_board_name)
    if provider == "zoho":
        return probe_zoho(fetcher, slug, company, with_board_name)
    if provider == "oracle":
        return probe_oracle(fetcher, slug, company, with_board_name)
    if provider == "amazon":
        return probe_amazon(fetcher, slug, company, with_board_name)
    if provider == "atlassian":
        return probe_atlassian(fetcher, slug, company, with_board_name)
    if provider == "microsoft":
        return probe_ms(fetcher, slug, company, with_board_name)
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider: {provider}")
    spec = PROVIDERS[provider]
    url = spec["jobs_url"](slug)
    p = BoardProbe(company=company or slug, provider=provider, slug=slug, status=ERROR, url=url)

    try:
        res = fetcher.get(url)
    except Blocked as exc:
        p.status, p.error = BLOCKED, str(exc)
        return p
    except Exception as exc:
        p.status, p.error = ERROR, f"{type(exc).__name__}: {exc}"
        return p

    p.http_status = res.status
    if res.status == 404:
        p.status = NOT_FOUND
        return p
    if res.status != 200:
        p.status, p.error = ERROR, f"HTTP {res.status}"
        return p
    if res.json is None:
        p.status, p.error = ERROR, "response was not JSON"
        return p

    # Lever answers 200 with [] for an unknown slug on some accounts, and
    # Ashby can answer 200 with an error object. Neither is a board.
    if isinstance(res.json, dict) and res.json.get("error"):
        p.status, p.error = NOT_FOUND, _s(res.json.get("error"))
        return p

    p.postings = spec["parse"](res.json)
    p.status = OK if p.postings else EMPTY
    if with_board_name:
        p.board_name = _board_name(fetcher, provider, slug, res.json)
    return p


def fetch_description(fetcher: Fetcher, provider: str, slug: str, job_id: str) -> str:
    """Fetch one posting's body. Only Greenhouse needs this.

    Lever and Ashby return the full description in their list response, so the
    watcher already has it for free. Greenhouse does not, and asking for
    `?content=true` on every poll would mean pulling 884 full job descriptions
    from Databricks every fifteen minutes to read the handful that changed.
    So: fetched here, on demand, only for postings that are genuinely new.
    """
    if provider != "greenhouse":
        return ""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
    try:
        res = fetcher.get(url)
    except Exception:
        return ""
    if res.status != 200 or not isinstance(res.json, dict):
        return ""
    content = _s(res.json.get("content"))
    # Greenhouse returns HTML-escaped HTML. Unescape once, then strip tags.
    content = html.unescape(content)
    return re.sub(r"<[^>]+>", " ", content)


# Providers whose list endpoint withholds something triage needs.
DETAIL_PROVIDERS = ("greenhouse", "workday", "oracle", "microsoft", "zoho")


def fetch_details(fetcher: Fetcher, provider: str, slug: str, posting: dict[str, Any]) -> dict[str, str]:
    """Fields to merge into one new posting, from its own page.

    Greenhouse withholds the description. Workday withholds the description,
    the date, and - for a multi-city role - every location but a count. Zoho
    withholds the description and the date. Lever and Ashby withhold nothing.
    """
    if provider == "greenhouse":
        return {"description": fetch_description(fetcher, provider, slug, _s(posting.get("id")))}
    if provider == "workday":
        got = workday_detail(fetcher, slug, _s(posting.get("url")))
        got.pop("employer", None)     # the board's name, not the posting's
        return got
    if provider == "zoho":
        return zoho_detail(fetcher, _s(posting.get("url")))
    if provider == "oracle":
        return oracle_detail(fetcher, slug, _s(posting.get("id")))
    if provider == "microsoft":
        return ms_detail(fetcher, _s(posting.get("url")))
    return {}


def _board_name(fetcher: Fetcher, provider: str, slug: str, payload: Any) -> str | None:
    """Best-effort display name for the board, used only for identity checks."""
    if provider == "greenhouse":
        try:
            meta = fetcher.get(greenhouse_board_url(slug))
        except Exception:
            return None
        if meta.status == 200 and isinstance(meta.json, dict):
            return meta.json.get("name")
        return None
    if provider == "ashby" and isinstance(payload, dict):
        return payload.get("organizationName") or payload.get("name")
    return None
