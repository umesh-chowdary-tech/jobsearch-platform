# The shared job pool

Every posting the watcher reads, kept once for everyone (`pool.py`). Each person
keeps only their own verdicts (`var/<id>/triage-results.json`).

## Why

Before the pool, every profile fetched the same Greenhouse or Workday description
for itself, and a profile added later caught up on what was already open through a
special one-off pass. The pool fixes both: the watcher records every open posting
on every poll, a description is fetched the first time anyone's filters let a
posting through, and everyone after reads that copy. A person who joins today
simply reads everything open in the pool.

## Files

| Path | What |
|---|---|
| `var/_shared/pool/<provider>__<slug>.json` | One file per board: a record per posting - title, place, link, `posted_at`, `first_seen`, `closed_at`. Written only when something on that board changed, so a quiet poll costs the database nothing. |
| `var/_shared/pool/text/<provider>__<slug>/<id>.json` | One description per file, written once. |

Lever, Ashby, Amazon and Atlassian send descriptions with their lists, so those
are kept when a posting first enters the pool (only for postings inside the
window). Greenhouse, Workday, Oracle, Microsoft and Zoho withhold them; they are
fetched on demand (`pool.details`).

## The 60-day window

Only postings posted in the last **60 days** (`WINDOW_DAYS`) are triaged, sent
to a model, or listed. The date is the board's own where it gives one
(Greenhouse's `first_published`, Lever's `createdAt`, Ashby's `publishedAt`,
Amazon, Microsoft, Oracle, and Workday and Zoho from the posting's page);
otherwise the day the pool first saw it. For a board watched before the pool
existed that is the queue's date, or the day the board was first read - the
most the watcher can vouch for.

A posting older than the window stays listed for a person who has done
something with it - starred it, saved it for later, moved it along, built a
resume or drafted a referral ask for it - and its own page always opens.

## Closed postings

- The watcher closes a posting the poll after it leaves its board, and reopens
  it if it comes back. A failed poll (timeout, 5xx, robots) closes nothing.
- A board no longer watched (dropped by discovery) has its open postings closed
  (`pool.retire`): nobody will poll it again to say otherwise.
- **Opening a posting, or building a resume for it, asks its board** whether it
  is still open (`pool.check_live`), since the last poll can be up to two hours
  old. Greenhouse and Lever are asked about the one posting; Ashby and Atlassian
  answer for the whole board in one request. Workday, Oracle, Microsoft, Zoho
  and Amazon have no plain answer for one posting, so the watcher's word stands
  for them. A board polled in the last 15 minutes is not asked, and one answer
  is remembered for 15 minutes. A posting found closed is closed in the pool and
  in the person's results at once, and no resume is built for it.
- Closed postings never reach a model and are off Today and the Claude
  connector. The Jobs screen shows them only when "Closed on the board" is ticked.

## 90 days after closing

`pool.shrink` removes a posting's description 90 days after it closed
(`SHRINK_DAYS`); the record stays, as the pool's history. Each person's stored
excerpt goes at the same time (`triage.sync_pool`), and their verdict and the
model's judgement stay. The database follows: files under
`_shared/pool/text/` are the one part of var/ where a removal on one side is a
removal in the database (`statestore.DELETE_FOLLOWS`). A fresh disk has no
sync manifest, so it pulls them back instead of deleting anything.

## Size

Records are a few hundred bytes. Descriptions are a few KB each, and Postgres
compresses them. With about 6,000 postings open at a time and descriptions kept
only inside the window and until 90 days after closing, the pool should stay
around 100 MB - an estimate, against Supabase's 500 MB free plan.
