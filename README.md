# jobsearch-platform

A job-search agent for anyone: it watches company job boards, ranks openings
against a person's own confirmed experience, tailors a resume per job, and
tracks applications - heading for a hosted service, with no API keys or
installs for the people using it.

It grows out of a personal job-search agent and is being rebuilt step by step
(the full plan, principles and architecture: [docs/ROADMAP.md](docs/ROADMAP.md)):

| Phase | What |
|---|---|
| 0 | One engine, many people, open-source ready: per-person profiles, sample profiles, CI, secret scanning, license |
| 1 | Setup from a resume: upload, the AI drafts the facts, the person confirms each one (built: `jobsearch setup`) |
| 2 | Invite-only hosted beta: sign-up, daily ranked jobs, tailored resumes, per-person AI caps (in progress: docs/hosted.md) |
| 3 | Inbox: a forwarding address first, mail sign-in later |
| 4 | Mobile app on the same backend |
| 5 | Payments, if costs call for it |

Rules that carry over from the start: a resume never gains a skill, number or
claim the person hasn't confirmed; every model call is capped, per provider and
per person; job boards are read politely (robots.txt, rate limits, public feeds
only - no LinkedIn or Naukri scraping).

The code is public; people's data never is. No key, token or personal data is
ever committed - see [SECURITY.md](SECURITY.md) and docs/ROADMAP.md, "Principles".

## How it works

| Stage | Does | Model? |
|---|---|---|
| Discovery | Find companies whose boards are machine-readable | No |
| Watching | Poll each board once for everyone, diff against last time, queue what is new | No |
| Triage | Filter and score each new posting against one person's profile; count the gaps | No |
| Ranking | A model reads the shortlist and says why it fits - it can't touch a claim | Yes, capped |
| Assembly | Build the best-matching resume from the person's store, facts verbatim | One summary line, numbers checked |
| Outreach | Draft a referral request from the resume that was sent; track follow-ups | One opener sentence, only with context |
| Inbox | Read job emails and update applications (local reader today) | Only for unclear emails, quoted |

Boards are shared and people are separate: each person's facts and preferences
live in a profile ([profiles/README.md](profiles/README.md)), and what the agent
works out for them in `var/<id>/`.

## Running it

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"     # add ,server for the MCP connector
.venv/Scripts/python -m pytest                      # no network needed

.venv/Scripts/python -m jobsearch.cli profiles                                # the fictional samples
.venv/Scripts/python -m jobsearch.cli setup my-name                           # your own profile, from your resume
.venv/Scripts/python -m jobsearch.cli verify                                  # find and check boards
.venv/Scripts/python -m jobsearch.cli watch                                   # what is new, for everyone
.venv/Scripts/python -m jobsearch.cli --profile sample-backend triage         # against one profile
.venv/Scripts/python -m jobsearch.cli --profile sample-backend rank           # a model reads the shortlist
.venv/Scripts/python -m jobsearch.cli --profile sample-backend assemble --top 3
.venv/Scripts/python -m jobsearch.cli --profile sample-backend serve          # the local app
.venv/Scripts/python -m jobsearch.cli cycle                                   # the scheduled loop, every profile
.venv/Scripts/python -m jobsearch.cli hosted --dev                            # the hosted service, on this machine
```

The hosted service's web app is in `web/` (React + TypeScript; `npm ci && npm
run build` there) - see [docs/hosted.md](docs/hosted.md).

(`.venv/bin/python` on macOS and Linux.) Copy `.env.example` to `.env` for
model keys; without any, everything up to ranking still runs.

## Docs

[setup from a resume](docs/setup.md) · [discovery](docs/discovery.md) · [watching](docs/watching.md) ·
[triage](docs/triage.md) · [ranking](docs/ranking.md) ·
[assembly](docs/assembly.md) · [outreach](docs/outreach.md) ·
[inbox](docs/mail.md) · [model limits](docs/limits.md) ·
[scheduling](docs/scheduling.md) · [the local app](docs/app.md) ·
[the connector](docs/connector.md) · [the hosted service](docs/hosted.md)

## Contributing and license

See [CONTRIBUTING.md](CONTRIBUTING.md). Licensed under the GNU Affero General
Public License v3.0 ([LICENSE](LICENSE)): anyone running a modified copy as a
service must publish their changes.
