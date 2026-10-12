# Model limits

Every model provider has a daily cap in `data/llm-limits.yaml`. Each request
is counted in `var/_shared/llm-usage.json` and checked **before** it is sent,
so a request that would pass a cap is never made. The run that wanted it
stops, and says why. Reaching a cap pauses the work until the next day; it
never falls back to a weaker model.

Two levels of cap:

- **The provider's own lines**: everyone together, per day (and per month).
- **`per_profile`**: each person's share of that provider's day. One busy
  profile can't spend everyone's budget, and the cost per person is known
  from the first request: every request is counted against the profile it
  was made for (`by_profile` in the usage file).

The shipped values are starting points for a small beta:

| Provider | Everyone, per day | Each person, per day | Why |
|---|---|---|---|
| Groq | 950 requests, 190,000 tokens; 25,000 kept for the inbox | 200 requests, 40,000 tokens | Just under the free tier (1,000 and 200,000). Ranking can't use up the share kept for unclear emails |
| OpenRouter | off (0) | - | Paid. A rate-limit fallback once spent its credits before it answered 402 |
| Claude (Haiku 5.5) | $0.50 ($0.03 of it kept for drafts), $4.00 a month | 40 requests, $0.30 | Prepaid credit; the monthly line is the budget. Set for Opus 5.5, where a person's daily share fitted one resume draft (setup); Haiku 5.5 costs a fortieth as much per token |
| Jev (TypeSafe) | 1,000 requests, 1,000,000 input tokens; 10,000,000 a month | 300 requests | $0.042 per million input tokens: at most about $0.04 a day |

- **A provider not in the file is not used.** A new provider gets a limit
  before it gets a request.
- **The provider's own count is recorded**, not an estimate, whenever the
  reply carries one. A refused request still counts as a request.
- **Days are local calendar days.** Groq's own day is a rolling 24 hours, so
  it can still refuse before our count says so. Its per-day refusals end the
  run rather than being waited out (`llm.py`).
- To change a cap, edit the file. The next request reads it.

`jobsearch usage` prints today's use against each cap; `jobsearch --profile <id>
usage` adds that person's share. The app's model status (`/api/system`) carries
the same counts.

**Claude is capped in dollars**, not just tokens. Each request's cost is
worked out from the token counts the API reports (input, output, cache writes
and reads) and the prices in `price_per_mtok`, one per model. A request whose
estimated cost would pass the day's or the month's dollars - everyone's or the
person's - is never sent, and a model with no price there is never sent one.
A reply from a model with no price (the API can switch models server side) is
counted at the dearest listed price. Measured in the first trial, on Opus 5.5,
a batch of four postings costs about $0.05-0.07, mostly thinking billed as
output.
