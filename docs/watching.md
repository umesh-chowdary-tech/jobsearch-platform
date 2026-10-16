# Module 2 — The watcher

Polls the boards discovery found, diffs job IDs against last time, emits only
what is new. **No model involved**, and that is the entire point. The watcher is
shared: one poll of each board serves every profile.

```bash
python -m jobsearch.cli watch                  # poll every known board
python -m jobsearch.cli watch --provider lever # just one ATS
```

## Why the diff matters more than the fetch

24 boards polled every 15 minutes is ~2,400 fetches a day. On a normal day maybe
5–15 of those turn up something new.

If a model read every fetch, ~99% of the budget would go on re-reading postings it
saw yesterday. The watcher exists so a model sees a posting **once**, the day it
appears. Fetching is cheap; reasoning is not.

## Verified live, 2026-09-23

- **First run seeded 24 boards, 4,869 posting IDs, and reported nothing.**
- **Second run, two minutes later: 24 polled, 0 changes.** No false positives.
- Diff path forced by deleting one ID from CRED's baseline → next poll reported
  exactly that posting, with title, location and URL, appended to
  `var/_shared/new-postings.jsonl`.

## Four decisions worth knowing

**1. First sight seeds; it does not report.** A board seen for the first time has
its IDs recorded and emits nothing. Without this the first run would emit all 4,869
postings — noise that would bury the first genuine diff and teach you to skim the
output.

**2. The diff is on the ATS's own ID.** Never a hash of title or URL, both of which
get edited in place. A retitled posting is not a new job, and the test for that is
explicit.

**3. A failed poll never touches the baseline.** A timeout, a 5xx or a robots block
leaves the stored IDs exactly as they were. Overwriting with an empty result would
make the next successful poll report the whole board as new — a hundred-item false
flood from one dropped connection.

A 404 is different in meaning but handled the same way: the baseline is preserved
and the error says *re-run discovery*, which is what actually removes the board.

**4. State is one file per board, written atomically.** `var/_shared/seen/<provider>__<slug>.json`,
written to a temp file in the same directory and then `os.replace`d. A half-written
baseline is worse than none — the next poll would read a truncated ID list and
report stale postings as new.

Per-board files also mean `watch --provider lever` is inherently safe, which is the
same bug that bit `boards.json` in Module 1 and had to be fixed there by merging.

## Files

| Path | Notes |
|---|---|
| `var/_shared/boards.json` | Read. Written by discovery. |
| `var/_shared/seen/<provider>__<slug>.json` | The baseline: IDs, `poll_count`, `seeded_at`, `last_poll` |
| `var/_shared/new-postings.jsonl` | Append-only log of arrivals, one line per posting the run it was first seen - no descriptions. |
| `var/_shared/pool/` | Every open and closed posting, and descriptions kept once - what triage reads (docs/pool.md). Each profile records in its own `triaged.json` which postings it has read. |

Resetting one board is `rm var/_shared/seen/lever__cred.json` — it re-seeds on the next
poll and reports nothing.

## What it does not do

- **No filtering.** Location, seniority and fit are triage's job. The watcher emits
  everything new and stays dumb on purpose.
- **Closed postings are counted, not emitted.** A job disappearing is real signal
  about a company, but it is not something to apply to.
- **No scheduling.** Run it from cron or Task Scheduler. Every 15 minutes is what
  the ATS APIs comfortably support; freshness at the source is near-instant, so the
  polling interval *is* the freshness.
