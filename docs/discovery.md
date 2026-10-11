# Module 1 — Discovery

Finds machine-readable job boards, verifies they belong to the company we think
they do, and writes the board list the watcher will poll. **No model involved.**
Boards are shared: one list, polled once, for every profile.

```bash
python -m jobsearch.cli verify                    # re-check every known board
python -m jobsearch.cli verify --provider lever   # just one ATS
python -m jobsearch.cli search "Postman" "Zepto"  # probe candidate slugs
python -m jobsearch.cli escalations               # the open queue
python -m jobsearch.cli escalations --resolve greenhouse:purestorage --note "..."
```

## Verified live, 2026-09-23

**24/24 known boards answered. 4,869 postings.** 17 Greenhouse, 5 Lever, 2 Ashby.
One escalation, and it is the one expected: `purestorage` renders as **"Everpure"**.

Paytm moved 175 → 173 between two runs eleven minutes apart, which is the diff the
watcher exists to catch.

## The politeness contract

Every one of these is enforced in code and covered by a test.

| Rule | Where |
|---|---|
| `robots.txt` checked before every request, cached per host for 24h | `robots.py` |
| Read by RFC 9309: groups by user-agent line, most specific rule wins, Allow wins a tie | `robots.py` |
| 5xx on robots.txt ⇒ treat as disallowed (RFC 9309) | `robots.py` |
| 4xx on robots.txt ⇒ no rules, proceed | `robots.py` |
| ≤1 request/second **per host**, not globally | `http.py` |
| 4xx is never retried — it is an answer, not a failure | `http.py` |
| 5xx and 429 retried twice, jittered backoff | `http.py` |
| Honest `User-Agent` naming the project | `http.py` |

The rate limit is per host deliberately: Greenhouse and Lever are separate
infrastructure and there is no reason to make them queue behind each other. Note
that all 17 Greenhouse slugs *do* share one host, so a full verify takes about a
minute — as it should.

## What gets escalated, and what does not

> **Identity doubt only.** A plain 404 is cached, not escalated.

- **`identity_mismatch`** — the board has a name and it is not the company's.
  `purestorage` → "Everpure".
- **`identity_unverifiable`** — Lever and Ashby often expose no board name. If the
  slug does not match the company *and* no apply link mentions either the company
  or the ATS, there is no evidence of ownership.
- **Not escalated:** 404s, robots blocks, timeouts. These are answers or transient
  noise.

The queue **deduplicates**. Discovery runs on a schedule, and without dedup one
unresolved doubt would append a new row every run until the file was unreadable and
the human stopped looking — at which point the human-in-the-loop is decorative.
Resolutions are appended, never edited, so the history is the audit trail.

## Files

| Path | Written by | Notes |
|---|---|---|
| `data/ats-registry.yaml` | **a human** | The agent reads it and never writes it. Rewriting YAML strips comments, and those comments are the reasoning. |
| `var/_shared/boards.json` | discovery | The live board list the watcher reads. **Merged, not replaced.** |
| `var/_shared/discovery/<ts>.json` | discovery | Full probe record per run |
| `var/_shared/escalations.jsonl` | discovery | Append-only queue |
| `var/_shared/robots/<host>.json` | discovery | robots.txt cache |

## Two bugs the live run found

Both were invisible to the unit tests and obvious the moment real data arrived.

**1. A cp1252 console killed the run *after* the work was done.** One em-dash in an
escalation message raised `UnicodeEncodeError` while printing, and the whole result
— 24 successful probes — was lost before anything reached disk. Two fixes: force
UTF-8 on stdout, and **persist before rendering**. A display bug must never cost
data.

**2. A partial verify wiped the board list.** `verify --provider greenhouse` wrote a
`boards.json` containing only the 17 Greenhouse boards, silently deleting the other
7. Nothing looked wrong. `boards.json` is now merged: a found board is upserted, a
404 removes it, and a timeout or robots block leaves the row alone — absence of
evidence is not evidence the board is gone.

The second one would have surfaced as "the watcher stopped seeing CRED" weeks later,
with no obvious cause.

## Workday (added 2026-09-24)

Many large employers run their careers site on Workday, and it has a public JSON
API behind it. Wells Fargo's wellsfargojobs.com is a copy of it: its Apply
button goes to `wd1.myworkdaysite.com/recruiting/wf/WellsFargoJobs`. That link
is how you find the three slug parts.

- **Slug:** `tenant.wdN.site[@Country]`, e.g. `wf.wd1.WellsFargoJobs@India`.
  It can't be guessed from a company name, so `search` never tries Workday.
  Boards are added to the registry by hand.
- **`@Country`** narrows the poll using Workday's own country filter, looked up
  by name on each poll because the filter IDs are per-company GUIDs. Wells
  Fargo is ~1,800 postings unfiltered (91 requests) and 162 with `@India`
  (10 requests). A board over 1,000 postings with no country is refused rather
  than partly polled.
- **Every page must land, or the probe is an error.** Only the first page's
  `total` is real (later pages say 0). If the postings collected don't add up
  to it, the board moved while paging, and the watcher skips this poll rather
  than recording live postings as closed.
- **Hidden postings.** Wells Fargo lists one posting as just
  `{"bulletFields": ["R-500002"]}`, with no title and no link. It counts toward
  `total`, so the check counts it too, but it never becomes a Posting.
- **IDs are `<tenant>-<R-number>`**, e.g. `wf-R-500001-1`. R-numbers are only
  unique within one employer, and triage and the pipeline key on the bare ID.
- **The list withholds** the description, the posting date and, for multi-city
  roles, every location except a count ("2 Locations"). Triage fetches all
  three from the posting's own page, for new postings only, as it already does
  for Greenhouse descriptions.

Checked live: 162 India postings, all 162 descriptions fetched, 35 kept by
triage and 127 rejected, every rejection on the title.

## Oracle Recruiting Cloud (added 2026-09-28)

JPMorgan's careers site (`jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/...`)
runs on Oracle's public REST API: `recruitingCEJobRequisitions` pages the list,
and `recruitingCEJobRequisitionDetails` gives one job's text.

- **Slug:** `host@SiteNumber[@Location]`, all read off the careers URL, e.g.
  `jpmc.fa.oraclecloud.com@CX_1001@India` for `.../sites/CX_1001/jobs?location=India`.
  It can't be guessed, so `search` never tries Oracle; boards are added by hand.
- **Two things Oracle silently ignores:**
  - **the site number:** `CX_9999` returned the same jobs as `CX_1001`;
  - **an unknown location ID:** it returned all 7,478 JPMorgan postings worldwide.

  So the location is written by name, and its ID is looked up in the answer's
  own location list on every poll. The filtered count must equal that
  location's count, or the probe is an error. A misspelt location is also an
  error, never "empty": empty would close every posting.
- **Paging:** 100 a page (it allows 200), newest first; 325 India postings is 4
  requests plus 1 for the lookup. As with Workday, the unique IDs must add up
  to the total, or the board moved mid-paging and the poll is skipped. Over
  1,000 postings with no location is refused.
- **IDs are `<tenant>-<number>`**, e.g. `jpmc-210000001`. The number is the "Job
  number" the employer's application emails quote, so the inbox links
  applications to postings.
- **Second board, no new code:** DTCC is `ebxr.fa.us2.oraclecloud.com@CX_1@India`
  (93 India postings, 83 of them Hyderabad). The host names no one; the job
  text says DTCC. Its careers link also filtered to "Information Technology";
  triage drops the other categories by title instead, so no category filter
  was needed. 30 kept, 10 strong.
- **robots.txt:** Oracle's firewall refuses `/robots.txt` on these hosts for
  every client, a browser included ("W4S-402: Blocked by WAF4SaaS"); other
  Oracle hosts answer 404. RFC 9309 reads a 4xx as "no restrictions". The API
  itself is public: the careers page calls it from every visitor's browser.

## Amazon (added 2026-09-28)

amazon.jobs has its own search API, `/en/search.json`; robots.txt closes only
`/internal`. Every job arrives with its whole description and qualifications,
so nothing is fetched per posting.

- **Slug:** `COUNTRY@category+category...`, using Amazon's category slugs from
  amazon.jobs URLs. India is 2,368 postings, mostly warehouse, finance and
  support roles that triage would reject after downloading them (22 pages a
  poll). Five engineering categories are 600 postings (6 pages,
  about 130 KB each compressed): `software-development` 404,
  `machine-learning-science` 88 (Applied Science; `applied-science` returns
  nothing), `solutions-architect` 51, `systems-quality-security-engineering`
  35, `data-science` 22.
- **An unknown category returns 0**, not everything. So a board with no
  postings at all is an error, never "empty": empty would close every Amazon
  posting. A single misspelt category among good ones just adds nothing;
  check each one's count when editing the slug.
- **IDs are `amazon-<Job ID>`**, the number on amazon.jobs. It counts as a
  requisition, so the inbox can't link one Amazon job's email to another job
  with a similar title.
- 100 a page (200 returns nothing), newest first. The IDs must add up to the
  total, or the board moved mid-paging and the poll is skipped.

## Atlassian (added 2026-09-28)

atlassian.com's careers page reads `/endpoint/careers/listings`: every open
job worldwide (287) in one JSON answer, with the overview, responsibilities
and qualifications. The page's team and country filters run in the browser,
so the watcher applies them itself.

- **Slug:** `Country@Team[+Team]`, with Atlassian's own team names, e.g.
  `India@Engineering` (29 postings: Bengaluru and remote-India).
- **A team or country that appears nowhere in the answer is an error**, and
  the error lists the teams that exist. A real team with nothing in that
  country is "empty", which is correct.
- **IDs are `atlassian-<id>`**, the iCIMS job number applications go through.
  Five digits, and it counts as a requisition when the inbox links emails to
  postings.
- One request a poll, about 2 MB uncompressed. Job links go to
  `atlassian.com/company/careers/details/<id>`.

## Microsoft (added 2026-09-29)

apply.careers.microsoft.com runs on Eightfold. `/api/pcsx/search` pages the
list, and `/api/pcsx/position_details` gives one job's description. robots.txt
allows `/careers` and `/api/pcsx`; the older `/api/apply` answers 403.

- **Slug:** `location@profession[+profession]`, with Microsoft's own profession
  filter values: `india@software engineering+research, applied, & data sciences+security engineering`
  is 99 of India's 229 postings. A misspelt profession returns 0, so a board
  with no postings is an error.
- **Ten a page, whatever `num` asks for.**
- **It rate-limits bursts.** The fourth request a second apart got "429 Please
  try again later", and so did requests six seconds apart after a short rest.
  Ten seconds apart ran clean, so `http.HOST_INTERVAL` gives this host 10 s
  between requests: a poll takes about 100 s, and each new posting's
  description another 10 s. Over 600 postings is refused at that pace.
- **Its robots.txt found the second robots bug.** The file opens with
  `Disallow: /` and then allows `/careers` and `/api/pcsx`. Python's parser
  takes the first rule that matches and blocked the API; RFC 9309 (and Google)
  let the longest matching rule win, so it's allowed. `robots.py` now
  implements RFC 9309 itself instead of `urllib.robotparser`. Replayed on every
  watched board, this changed one decision: Microsoft's, from blocked to
  allowed.
- **IDs are `microsoft-<Job number>`** (`displayJobId`, e.g. 200000001), the
  number the employer's application emails quote. Eightfold's own ID is only in
  the job's URL.

## Zoho Recruit (added 2026-09-26)

Common with Indian employers. There's no public JSON API, but the career site
carries its data as JSON, so nothing is scraped from the page's layout.

- **Slug:** the career site's host, e.g. `minfytech.zohorecruit.com`, with
  `@Portal` added if the portal isn't named "Careers". The data centre (`.com`,
  `.in`, `.eu` ...) is part of the host. Read it off any job link:
  `https://minfytech.zohorecruit.com/jobs/Careers/4964...`.
- **The list:** `https://<host>/jobs/Careers` holds every published job in a
  hidden input (`id="jobs"`), HTML-escaped JSON with the ID, title,
  city/state/country, a remote flag and the job type.
- **Each job's page** (`/jobs/Careers/<id>`; the title part of Zoho's URL is
  optional) holds the full record as a JavaScript string,
  `var jobs = JSON.parse('...')`: the description, `Date_Opened`, experience,
  job type and industry. Triage fetches it for new postings only, as for
  Greenhouse and Workday.
- **Not found looks like success.** An unknown organisation still answers 200,
  with a 2.6 KB page titled "Page does not exist". A real career page with no
  job list in it is an **error**, never "empty": empty would tell the watcher
  that every posting on the board had closed.
- **`search` tries Zoho** after the JSON ATSs, as `<name>.zohorecruit.com` and
  `.zohorecruit.in`. The page's `og:site_name` is the board's name, so identity
  is checked like Greenhouse's.
- **robots.txt allows `/jobs`** and disallows everything else. Checking this
  found a bug in our robots handling: Python's parser ended a group at a blank
  line, so Zoho's file (its Allow list, a blank line, then `Disallow: /`) read
  as "allow everything". Microsoft's file then showed the opposite flaw (see
  Microsoft), and `robots.py` now implements RFC 9309 itself.
- **Weight:** each page is about 1.7 MB (gzipped on the wire). That's fine for
  a few boards polled every 15 minutes. Fetching descriptions for a whole new
  board is a one-off: 72 postings took three minutes at one request a second.
- **Old postings stay up.** Some boards list jobs opened years ago; one OTSI
  posting was opened 2022-10-26.

Checked live on four boards: Minfy (72 postings), Kanerika (26), OTSI (30) and
LoginRadius (5). Every listed job had a page, and no closed job was listed. All
133 descriptions were fetched; for one profile triage kept 31, and the AI read
all 31: 7 strong, 11 possible, 13 weak.

## Known limits

- **Tier 2 (JSON-LD `JobPosting`) is not built.** Only the five ATS APIs, Amazon's, Atlassian's and Microsoft's own APIs and
  Zoho's career pages are covered. Companies on a custom careers site are invisible to discovery today,
  unless that site is a front for Workday (look at where Apply links go).
- **Tier 3 (headless browser) is not built.** Google is in the registry as the one
  tier-3 case and is not probed.
- **Slug guessing is deliberately narrow** — three candidates per company. Searching
  "Sarvam AI" cost 9 probes to find one board. A wider guess space buys very little
  and produces a lot of 404s, which is what `slug_404_do_not_reprobe` is for.
