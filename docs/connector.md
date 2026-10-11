# The connector: the agent inside a normal Claude chat

`python -m jobsearch.cli --profile <id> connector` serves the agent as an MCP
server. Added to claude.ai as a custom connector, any chat - phone or web - can
find postings, rank them, and record decisions, with the same memory the local
app has.

**Today one connector serves one profile.** Its only lock is a secret URL, so
it belongs to one person. The bigger version - every user connects their own
Claude account to the hosted service, signs in, and the tools see only their
data - needs accounts, so it comes with the hosted beta (docs/ROADMAP.md,
Phase 2). claude.ai's custom connectors support OAuth, which is what that
sign-in will use.

## How it fits together

| Piece | Where | Cost |
|---|---|---|
| The server (`connector.py`) | Render, free web service | free; a card is needed once for verification |
| The memory (`statestore.py`) | Neon, free Postgres | free, no card |
| The schedule | GitHub Actions, every 3 hours (`.github/workflows/refresh.yml`) | free for public repositories |
| The ranker | Claude, in the chat that asks | the person's Claude plan |

**Why Postgres:** Render's free disk is wiped every time the service sleeps (15
quiet minutes). All of var/ - the profile's state and the shared board state -
is mirrored to one table, a file per row; the server pulls before each tool
call and pushes after each write. Nothing else in the code knows a database
exists. Discovery history, trials and dashboards stay local. (Phase 2 replaces
this with a real schema; it exists so one person's connector has a memory.)

**Why Claude ranks:** the server has no model key. `jobs_to_rank` hands the chat
the same profile, level line and rules the Groq pass reads (`rank.SYSTEM`);
`save_rankings` stores the result with `by` naming Claude, and enforces the
years cap itself rather than trusting the chat. Claude is the most trusted judge
in `rank.TRUST`, so Groq's strong/possible calls are offered for a re-read first.

**Auth:** without OAuth, claude.ai custom connectors take no credentials at
all - not a static header. The endpoint therefore lives at
`/<CONNECTOR_SECRET>/mcp`, and every other path is a bare 404. **The URL is the
password**: anyone holding it can read the resume store and change decisions.
Don't paste it into chats or screenshots; to revoke it, change
`CONNECTOR_SECRET` on Render and re-add the connector.

## Tools

| Tool | Does |
|---|---|
| `today` | What needs a decision, what is in progress, referral follow-ups due |
| `find_jobs` | Open kept postings by status, fit or text, best first |
| `get_job` | One posting in full (a unique id prefix works) |
| `jobs_to_rank` / `save_rankings` | Claude judges the unranked; the server records it |
| `set_status` / `save_for_later` / `set_favorite` | Record what the person decided - never acts for them |
| `resume_store` | The defensible profile and the skills by tier |
| `application_kit` | What an application form asks for, for one posting |
| `refresh_jobs` / `refresh_status` | Poll every board now, in the background, and triage for this profile |

## Setup

1. **Neon** - sign up at neon.com and create a project in the region nearest
   your Render service. On the dashboard's *Connect* dialog, turn **Connection
   pooling off** and copy the URL. (The pooled `-pooler` URL is refused: it
   breaks the locks that keep a PC and the server from polling at once.)
2. **Seed it** - put `DATABASE_URL=<that URL>` in `.env`, then
   `pip install -e ".[server]"` and `python -m jobsearch.cli state push`.
3. **Render** - *New -> Blueprint*, pick your copy of this repo. It reads
   `render.yaml` and asks for `DATABASE_URL` (the same Neon URL),
   `CONNECTOR_SECRET` (a long random string) and `JOBSEARCH_PROFILE` (the
   profile it serves). The profile's files must be on the server: keep a real
   person's profile in a private place (`JOBSEARCH_PROFILES`), never in this
   public repository.
4. **claude.ai** - *Settings -> Connectors -> Add custom connector*. URL:
   `https://<your-service>.onrender.com/<CONNECTOR_SECRET>/mcp`. Leave the OAuth
   fields empty.
5. **Schedule (optional)** - in GitHub, *Settings -> Secrets -> Actions*, add
   `CONNECTOR_URL` = `https://<your-service>.onrender.com/<CONNECTOR_SECRET>`.
   Without it the workflow does nothing.

## A PC and the server together

With `DATABASE_URL` in a PC's `.env`, every scheduled `cycle` pulls var/ first
and pushes after, and both sides take the same "cycle" lock - whichever starts
second skips that tick. Without it, the PC runs on its own files and the two
drift apart.

If both sides changed one file between syncs, the database copy wins and the
local one is kept in `var/sync-conflicts/`. The likeliest case is a decision
made in the local app while a chat recorded another on the same posting.

## Limits of the free plan

- **Cold start:** the first call after 15 quiet minutes waits about a minute
  while Render wakes the service. If the chat says the tool timed out, ask again.
- **750 hours a month** per Render workspace - one service running all month
  fits. A second free service in the same workspace would share it.
- **Neon:** 1 GB per project on the free plan; var/ without discovery history
  is a few MB.
