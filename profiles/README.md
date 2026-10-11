# Profiles

One folder per person. Everything the engine knows about someone - what they
did, what they may claim, what they are looking for - lives in their folder,
and nowhere else.

```
profiles/<id>/
  master-store.yaml     the facts: identity, jobs, projects, education - each one defensible
  skills.yaml           what may go under Skills, by how confidently it can be claimed
  search-profile.yaml   the hard filters: places, years, titles out of reach, roles to skip
  mail-learned.yaml     inbox rules the person approved (optional; written by the app)
  ats-names.yaml        Workday suggestions to remove or keep for them (optional)
```

What the agent works out for a person goes to `var/<id>/` (triage results,
decisions, resumes, applications, outreach). Board polling is shared and goes
to `var/_shared/`: one watcher polls each board once for every profile.

Shared knowledge stays in `data/`: the job boards, company names, the skill
vocabulary, Workday's names for skills, mail senders and the model caps.

## The samples are fictional

| Profile | Who | Exercises |
|---|---|---|
| `sample-backend` | Kavya Example, mid-level backend and AI engineer, Pune | every part of the schema: several workstreams at one employer, projects, measured metrics, a low-priority entry, a scoped skill, incidental and banned skills, pins, an unconfirmed entry |
| `sample-frontend` | Rohan Sample, junior frontend developer, Bengaluru | a small store, "senior" out of reach |
| `sample-android` | Meera Placeholder, senior Android lead, Chennai | two employers, lead roles within reach, no approved descriptor |

The people, employers and projects are made up; emails are `@example.com` and
links point at `example.com`. The tests run against these files.

## A real person's profile never goes in this repository

`.gitignore` ignores everything in `profiles/` except `sample-*` folders and
this README. Better still, keep real profiles outside the checkout and point
`JOBSEARCH_PROFILES` at them:

```bash
JOBSEARCH_PROFILES=/path/to/private/profiles python -m jobsearch.cli profiles
```

## Choosing a profile

```bash
python -m jobsearch.cli profiles                          # list them
python -m jobsearch.cli --profile sample-backend triage   # one person's command
JOBSEARCH_PROFILE=sample-backend python -m jobsearch.cli rank
python -m jobsearch.cli cycle                             # watch once, then everyone
```

With one profile, it is used without asking. With several, a command that needs
a person says so rather than guessing. An id is lowercase letters, digits and
hyphens.

## Starting a new profile

```bash
python -m jobsearch.cli setup <id>
```

Upload a resume, check each fact the AI drafts against the line it came from,
say what you're looking for, and save (docs/setup.md). Only what you confirm
is written. To keep your data outside this checkout, set `JOBSEARCH_PROFILES`
and `JOBSEARCH_VAR` in `.env` to a private folder first.

By hand works too: copy a sample folder, rename it, and replace every fact
with the person's own. The rules in the sample files' comments are the rules:
nothing goes in the store that the person cannot defend in an interview, no
number is rounded or filled in, and a skill appears on a resume only when an
entry's `tech` backs it.
