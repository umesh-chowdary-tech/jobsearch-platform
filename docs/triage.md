# Triage — ranking postings, and counting what's missing

Between the watcher and the assembler. It runs once per profile, against that person's
`search-profile.yaml` and `skills.yaml`, over the shared queue of new postings.

Not every new posting deserves a tailored resume. Most will be in the wrong city, at the
wrong level, or on the wrong stack. Triage ranks, explains itself, and stops — **it never
decides.**

## Three passes, cheap to expensive

### Pass 1 — hard filters. Deterministic, free.

Kills most postings at zero cost:

- **Location** — the profile's preferred or acceptable places, or remote. Remote means
  remote *from where the person is*: "Remote - CA" or "Remote, Austria" is out for
  someone in India, because a location that names any place not on the profile's list
  is out.
- **Seniority** — `years_experience` and `max_years_required` from the profile: anything
  asking for more years than the cap, or with a title in `reject_titles`, is out.

### Pass 2 — coverage scoring. Deterministic, free.

Of the skills the posting names, how many are backed and at what tier?

| tier | weight |
|---|---|
| `core` | heaviest |
| `working` | less |
| `scoped` | counts, but only within its `scope` |
| `incidental` | **zero** |

Azure appearing in a posting must not score. If it did, the skills layer was pointless.

### Pass 3 — model, on the shortlist only.

Top ~10 only. This is where keyword matching fails and reasoning pays for itself: a posting
saying *"take our AI platform from prototype to production"* is a near-perfect match to the
headline entry, and no keyword overlap would ever surface that.

## The number must not lie

> Coverage 8/11 means **"8 of the 11 skills this posting names are backed by an entry."**
> It does not mean 73% chance of an interview, and must never be rounded into one.

Same rule that killed the invented percentages, turned on the agent's own output. The
ranking is a sorting aid, not a prediction — so it always ships with its reasoning:

```
Druva — Senior Backend Engineer — Pune                 coverage 8/11
  strong  Python(core) PostgreSQL(core) AWS(core) Docker(w) pytest(w)
  gap     Kubernetes, Terraform, Go
  angle   backend (9 entries) — deepest
```

You read the gap line, not the number.

## The gap ledger

The ranking is the obvious output. **The tally of what keeps appearing in the gap column is
the valuable one** — a learning list assembled from real demand rather than from a blog
post about what's hot.

Four distinctions decide whether it is actionable or just a word cloud:

**1. Required vs nice-to-have.** Most postings separate them. Counted together, wishlists
drown the signal.

**2. Missing entirely vs missing at depth.** Azure recurring does not mean "learn Azure
from zero" — it is already `incidental`. Promoting a scoped skill to `working` is far
cheaper than starting something new. Separate columns, because they imply different amounts
of a weekend.

**3. Synonyms.** Kubernetes / K8s / k8s collapse to one entry or the counts are
meaningless. Needs a synonym map, maintained by hand.

**4. Recency.** Rolling 90-day window, not all-time. What the market asks for *now*.

Stored per person in `var/<id>/skill-gaps.json`.

## When the profile changes

A change to `search-profile.yaml` only reaches postings triaged after it. Once,
three analyst roles and three remote-abroad roles were still keeps on the dashboard after
the filters that reject them had landed. `triage --all` is not the fix: it re-reads only
the watcher's queue (20 of 29 stored results were not in it), counts the gap ledger and
the reject tallies a second time, and rewrote each record without its model judgement.

`triage --recheck` re-applies pass 1 to the stored results, from disk alone: no network,
no gap ledger, no tallies. It is a dry run that lists what would change until it is given
`--write`. It changes `verdict` and `reasons` and nothing else, so the `ai` judgement
survives. A posting that now fails is marked `reject` rather than deleted, because
outreach may still refer to it by id. Pass 2 is not re-run: scoring a 2000-character
excerpt would under-count every posting, so a change to `skills.yaml` still needs a
re-triage.

**The excerpt can prove a pass, not always a fail.** Pass 1 read the whole description.
A stored record keeps an excerpt, plus the years pass 1 found in the full text. When a
check fails on text that may have been cut off, the verdict stays and the report says so.
A recheck that rejected a posting because it had stored too little would be the same bug
it exists to fix.

It also marks postings the watcher no longer sees as `closed_at`. It only does so for a
posting triaged before the board's last poll, so a fresh `--board` read is never called
closed. Closed postings stay in the file, are hidden from the dashboard with a count,
and are skipped by `rank` and `assemble --top`.

## The loop closes

When the person learns something it becomes a store entry with its own `tech`, which
changes what future postings match against. The system gets better at representing them
as they get better.

## The caution

The gap list tells you **what the market asks for most often.** That is not the same as
**what makes you worth hiring.**

Chase every gap and you converge on the median candidate — competent at the twelve things
everyone lists. A person's real edge is usually narrow and rare: a platform taken to
production, a flattering metric caught as an artefact and replaced with the honest one.
Almost nobody applying to the same roles has it.

So the gap list **informs** what to learn. It does not drive it. If Kubernetes appears in
60% of postings, that is worth a weekend. If getting it means giving up the agentic work,
it is not.


---

# Running it

Pass 3 (a model on the shortlist) is in docs/ranking.md.

```bash
python -m jobsearch.cli --profile <id> triage                       # the new-postings queue
python -m jobsearch.cli --profile <id> triage --board lever:meesho  # everything live on one board
python -m jobsearch.cli --profile <id> triage --all                 # re-triage, ignoring what was seen
```

`--board` exists because the watcher's queue only holds what changed, and the
other real question is "what is on this company's board today that fits me". It
is also how the filters got exercised against 148 real postings instead of a
trickle.

## What it produced on eight live boards, for one profile

148 postings triaged, 21 kept. Sample output:

```
meesho — Software Engineer - II
  Bangalore, Karnataka   coverage 5/10   score 13   angle fullstack
  strong  AWS(c) Docker(w) MongoDB(w) PostgreSQL(c) Python(c)
  gap     Azure, GCP, Kubernetes, LLM, MySQL
```

One board rejected 36/36 — and every rejection was correct. All 12 of its India roles
were Staff, Principal, Manager or operations. The right answer was "nothing here
for you today", not a forced match.

## Three bugs only live data could find

**1. Non-engineering roles dodged the title filter.** "Cluster Head - Last Mile",
"Senior Talent Partner" and "Trainee - Recruitment Coordinator" all passed, because
enumerating every non-engineering job title is a losing game. Replaced with a
general rule: **a posting that names no technology at all is not an engineering
role**, whatever its title says.

**2. "Spark" matched the English word.** Meesho's boilerplate uses it in prose, so
Apache Spark was about to top the gap ledger for a technology nobody had asked for.
Terms that are also ordinary English — Spark, Swift, Rust, Flask, Go, React — are
now matched case-sensitively, or via an unambiguous alias like `pyspark`.

**3. The years parser was wrong twice.** `"100 years"` read as 10, and
`"8+ years overall, 3+ years with Python"` returned 8 because the first match
consumed the text the second needed. Fixed with digit boundaries and a
non-consuming lookahead. This one mattered: postings routinely state a high overall
figure and a lower one for the specific stack, and reading only the high number
rejects jobs that were within reach.

## And one finding about the store, not the postings

The first gap ledger put **LLM (8 required)** and **Agentic (7)** at the top, marked
*absent — learn from zero*, for someone whose store showed an agentic LLM platform
taken to production. **Observability (10)** likewise, for someone who had built
hourly production health checks.

The store's `tech` lists were too literal: they named products (Python, FAISS,
Claude APIs) and missed the practice-level words a posting actually uses. Nine terms
were added to entries that already evidenced them — LLM, Agentic, RAG, Fine-tuning,
Vector DB, REST, CI/CD, Observability, Prompt Engineering. **No new claim was made**;
existing entries were tagged with the vocabulary of the people reading them.

A second category was handled differently. Git, Agile, Code Review, Linux and Unit
Testing are table stakes — detected so a posting parses correctly, but never counted
as gaps. "Learn Git" is not a plan, and leaving them in crowded the real gaps off
the top.

After both corrections the ledger reads as it should:

| skill | required | status |
|---|---|---|
| Kubernetes | 17 | absent |
| Terraform | 7 | absent |
| Azure | 6 | shallow — deepen it |
| Redis, Elasticsearch, Prometheus | 4 each | absent |

**Kubernetes is the answer** for that profile, and it took 148 postings to say so rather
than a blog post. The caution above still applies: that is what the market asks for
most often, not what makes a person worth hiring.
