# jobsearch-platform

A job-search agent for anyone: it watches company job boards, ranks openings
against a person's own confirmed experience, tailors a resume per job, and
tracks applications - as a hosted service, with no API keys or installs for
the people using it.

It grows out of a personal job-search agent and is being rebuilt step by step:

| Phase | What |
|---|---|
| 0 | One engine, many people: every person-specific fact lives in a profile; sample profiles for tests |
| 1 | Setup from a resume: upload, the AI drafts the facts, the person confirms each one |
| 2 | Invite-only hosted beta: sign-up, daily ranked jobs, tailored resumes, per-person AI caps |
| 3 | Inbox: a forwarding address first, mail sign-in later |
| 4 | Mobile app on the same backend |
| 5 | Payments, if costs call for it |

Rules that carry over from the start: a resume never gains a skill, number or
claim the person hasn't confirmed; every model call is capped; job boards are
read politely (robots.txt, rate limits).
