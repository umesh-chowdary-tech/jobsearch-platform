# Triage pass 3 — a model reads the shortlist

```bash
python -m jobsearch.cli --profile <id> rank --limit 10
```

Passes 1 and 2 are keyword work. They know a posting names Python and that
Python is backed. What they cannot see is that *"take our AI platform from
prototype to production"* describes the person's headline entry almost
exactly, with no keyword overlap at all.

## The model cannot touch a claim

It ranks and explains. It never adds a skill, never edits the store, never
writes a resume. Every fact it receives comes from the profile's
`master-store.yaml`, and the only thing it returns is `{id, fit, why}`.

That makes **"never claim an unearned keyword" true by construction** rather
than by instruction. A prompt that says "don't invent skills" is a hope. A model
with no write path to the claims is a guarantee.

The prompt (`rank.SYSTEM`) holds no one person's examples: its good and bad
"why" lines are generic, and everything about the person - their work, their
level, the titles out of reach - arrives as data from their profile.

Output is folded in as a sort key — judgement first, keyword score as the
tiebreak. An **un-ranked posting keeps its keyword place** rather than sinking
below things judged weak: the model simply never saw it, which is not evidence
against it.

## Providers

Claude, Groq and OpenRouter, with their keys in `.env` (gitignored; see
`.env.example`). `data/models.yaml` says which provider and model does each
job:

| Job | Model |
|---|---|
| Ranking postings (`rank`) | Groq's free tier reads new ones first; Claude Haiku 5.5 re-reads what it calls strong or possible |
| A resume's summary line (`resume`) | Claude Sonnet 5.5 |
| Drafting a profile from a resume (`setup`) | Claude Sonnet 5.5 |
| A message's first line (`outreach`) | Groq (free) |
| Sorting the inbox (`mail`) | Groq only, always |

A job not listed there uses `LLM_PROVIDERS`, in order. Every provider is capped
in `data/llm-limits.yaml`, for everyone and for each person (docs/limits.md).

**The pipeline works without any of it.** Discovery, watching and passes 1–2
never call a model. A missing key degrades the system rather than breaking it,
and the dashboard says which — *"Keyword ranking only — No AI involved"*. A
dashboard that silently drops to keyword-only when a key expires looks identical
to one that is working, and you would trust the ordering exactly as much. You
should not.

### Two provider lessons, both from real failures

**`response_format: json_object` can itself be the failure.** `gpt-oss-120b` is
a reasoning model; it emits thinking tokens, and the server rejected the whole
completion as invalid JSON. Every call was silently falling through to the
slower provider. Fixed by hardening the system prompt and retrying the *same*
provider without the strict format before moving on — **0.9s on Groq instead of
3.1s on the fallback**, a 3.4× difference caused entirely by a guardrail.

**Reasoning models need budget for the reasoning.** `max_tokens` has to cover
the thinking as well as the answer. Too tight and the completion truncates
mid-object, which arrives as "reply was not JSON" and looks like a model
problem rather than a budget one.

Postings are sent **four at a time**. One call for everything produced a long
reply that models truncate and pad with generic reasons.

## The bug worth remembering

The first run judged a machine-learning posting:

> *"lacks PyTorch/transformer fine-tuning experience"*

For someone who had fine-tuned an open-weight model with QLoRA and trained two
transformer models with measured metrics.

The model was not wrong. `profile_digest` capped at 9 entries sorted work-first;
there were 8 work entries, so exactly one project survived and the entire ML
portfolio never reached it. It reasoned correctly from a profile that had been
silently truncated.

**A partial profile does not give a slightly worse ranking. It gives a
confident wrong one.** The cap is gone — every work and project entry goes in,
with facts trimmed instead, which costs detail rather than whole capabilities.
Re-ranked, the same posting came back **strong**.

`tests/test_rank.py` guards it on the sample profile: the digest must contain
every work and project entry, the ML projects (`DistilBERT`, `QLoRA`) and the
measured `token F1`.

It is a recurring shape - the gap ledger telling someone to learn LLMs from
zero, `boards.json` wiped by a partial run, and this. **Something upstream
quietly dropped data, and everything downstream stayed confident.**

## The second bug: fit without level

A *Lead AIML Engineer* posting that says **"Experience: 10 + Years"** came back
a strong fit for someone with three years of experience. It couldn't be. Three
things let it through, one after another:

1. **The years filter couldn't read it.** Pass 1 rejects a posting asking for
   more than `max_years_required`, but its pattern wanted "experience" *after*
   the number ("5+ years of experience"). "Experience: 10 + Years", with the
   label first, read as "no years stated". A second pattern now reads the
   label-first form.
2. **The title rule matched a phrase, not a word.** `reject_titles` listed
   "lead engineer", which as a substring misses "Lead AIML Engineer", "Lead
   Software Engineer" and "Applied AI/ML Lead". Titles now match whole words.
3. **The model was never told the level.** The prompt defined fit as "the
   posting's core work matches work the person has done", and the profile
   listed projects but not years, level or degree. It matched "built an
   agentic AI platform" to "Lead Agentic AI Engineer" and said strong. Every
   batch now starts with a LEVEL line, built from the profile: years, cap,
   `seniority.level` and the titles out of reach from `search-profile.yaml`
   (the same values pass 1 filters on), jobs and degrees from the store. Each
   posting shows its stated minimum years. The prompt says strong requires
   the level to be within reach: more than the cap, or a title out of reach,
   is at most weak; a required degree the person doesn't have is at most
   possible.
4. **And a backstop in code:** whatever the model answers, a posting whose
   stated minimum exceeds the cap is stored as weak, and its "why" says so.

Applied to what was stored at the time, a recheck moved **60 kept postings to
rejected, 25 of them rated strong**. Every strong and possible posting was then
re-read with the level-aware prompt.

It's the same shape as the truncated profile: the model was confident because
the information that would have stopped it never reached it.

## In the cycle

`jobsearch cycle` reads, for each profile, every kept posting no model has
judged yet, four to a request, and nothing when there are none.

It reads for at most three minutes per cycle, shared between the profiles
(`RANK_BUDGET` in `cycle.py`, with a floor per profile), saves after every
answered request, and stops at the first rate limit or cap. What is left waits
for the next cycle. The log line says how many were read, how many are left,
and why it stopped early:

    sample-backend rank read=12 of=167 left=155 by=openai/gpt-oss-120b via groq stopped='time budget'

Why: a PC woke from a week asleep with 167 postings unread. The cycle tried to
read them all at once, which held the watcher and the inbox for as long as
that took, and wrote nothing until the end. When the PC slept again mid-run,
everything it had read so far was at risk. A per-day limit is never waited
out, even when Groq names a short wait (its day is a rolling window), because
waiting one out after another would hold the cycle to the pace the day's
budget frees up. `jobsearch rank` by hand has no time budget, but it also saves
as it goes and stops at a limit.

## Claude checks, Groq reads first

A blind trial on 40 postings already ranked by Groq compared Groq with Claude
Opus 5.5 on the same prompt:

- **They agreed on 18 of 40.** Groq rated 14 "strong" and Claude rated 5. Of
  Groq's 14, Claude put 9 at "possible" and 1 at "weak".
- **Groq credited the profile with work it doesn't have** - a framework and a
  payments integration that appear nowhere in the store.
- **Groq misapplied the level rule.** It called 5-years-asked "beyond reach"
  for two roles, when the cap was 5.
- **Groq's reasons were keyword lists,** the exact pattern the prompt forbids.

Claude through the API agreed with the blind in-session ratings on 7 of 8.

So Claude (the `rank` route in `data/models.yaml`) judges, and its judgement
replaces Groq's.
On a small prepaid budget Claude reads about eight postings a person a day,
and many more new ones are kept. So ranking runs in two passes:

1. **Groq** (free) reads every posting no model has read (`RANK_FIRST_PASS`,
   Groq by default).
2. **Claude** re-reads what Groq called **strong** first, then **possible**,
   within the daily dollars (`data/llm-limits.yaml`, everyone's and the
   person's). Groq's **weak** stands: in the trial none of its 13 came back
   better than possible.

The first pass gets half of the time. A judgement records which model made it,
so the app shows Claude's and Groq's apart. When a Claude cap is reached, its
share waits for the next day and never falls back to Groq.

Jev, a low-cost model that answers fixed questions with a confidence and writes
no prose, is a candidate for the first pass. It goes in only after it scores
well on an evaluation set with known answers (docs/ROADMAP.md, "Quality").
