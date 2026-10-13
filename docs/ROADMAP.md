# Roadmap

jobsearch-platform is a hosted job-search agent for anyone, technical or not.
It watches company career pages, ranks each opening against the person's own
confirmed experience, writes a resume tailored to the job, and tracks where
each application stands. People sign up and use it. They never create API
keys, install anything or run a server.

The code is public. People's data never is.

## Principles

These hold in every phase. A change that breaks one doesn't ship.

1. **Never invent anything about a person.** A resume, a ranking reason or a
   message never gains a skill, number, title or claim the person hasn't
   confirmed. The AI drafts and the person confirms. Numbers are never rounded
   up or filled in.
2. **Every AI call has a cap.** Each provider, each person and each day has
   one, set before the first request. Reaching a cap pauses the work; it never
   silently switches to something worse.
3. **Read job boards politely.** Follow robots.txt and rate limits, and use
   each board's public jobs feed. No LinkedIn or Naukri scraping.
4. **Keep the minimum of a person's data, and delete it when asked.** No email
   bodies are stored. A person can export or delete everything at any time.
5. **Public code, private secrets.** No key, token, password or personal data
   is ever committed. Secret scanning runs on every push.
6. **Measured, not assumed.** Ranking quality is checked against an evaluation
   set before a change ships. Cost per person is tracked from day one.

## Architecture (where it's heading)

```
 web app (React + TypeScript)     mobile app (Expo, phase 4)
              \                    /
               HTTPS JSON API (Python, FastAPI)
               |        |         |
          Postgres   object     model gateway (caps per person,
          (people,   storage    per provider, per day: Claude,
          profiles,  (resume    Groq)
          postings,  files)
          ratings,
          applications)
               |
          workers: board watcher (shared: one poll serves everyone),
                   ranking queue (per person, within caps),
                   inbox forwarding (phase 3)
```

- **The engine stays Python.** The existing triage, ranking, resume assembly
  and board providers become a library the API and the workers call.
- **Boards are shared, people are separate.** One watcher polls each company
  once and every person's filters read the result. Ranking is per person.
- **Sign-in:** email magic link, plus "Sign in with Google" for identity only.
  No Gmail access: that would need Google's restricted-scope security review.
- **Hosting to start:** one managed Postgres, one API service, one worker,
  object storage. A managed bundle such as Supabase (Postgres, auth, storage)
  plus a Python service on Render or Fly keeps it within free and starter
  tiers for the beta.
- **Errors and costs:** error tracking, plus a usage table of requests and
  dollars per person per day, shown on an admin page.

## Phases

Each phase ends with something real people can use, and has a check that says
it's done.

### Phase 0: An engine for anyone, open-source ready
- Copy the engine in, with no personal data and no history of any.
- Per-person profiles: each person's facts, skills and search settings live in
  a profile. Shared knowledge (job boards, company names, skill vocabulary)
  stays shared. 2–3 fictional sample profiles.
- Prompts with no one person's examples in them.
- Repo hygiene: a license, README, CONTRIBUTING, SECURITY, `.env.example`,
  CI running the tests on every push, secret scanning with push protection,
  Dependabot, and branch protection on `main`.
- **Done when:** the repo is public, CI is green, and a scan finds no
  personal data or secrets.

### Phase 1: Setup from a resume
- Upload a PDF or DOCX. The AI drafts the person's facts, each with the line
  of the resume it came from. The person confirms, edits or removes each one,
  and supplies any numbers they want used.
- A preferences form covering cities, remote or not, years of experience,
  roles to skip and companies to watch.
- The resume-tailoring and ranking flows run on the confirmed profile only.
- **Done when:** 3 friends with different backgrounds are set up from their
  own resumes, and every fact on their tailored resumes traces back to
  something they confirmed.

### Phase 2: Invite-only hosted beta
- Accounts, a database, the API and workers, and the web app with these pages:
  setup, today's ranked jobs, every kept job with search and filters, job
  detail with a tailored resume, applied tracker, resumes, the profile with
  its skill gaps, settings.
- Per-person AI caps, a global monthly cap, and the admin cost page.
- Privacy policy and terms (India's DPDP Act: consent, purpose, export,
  deletion, breach handling), data export and account deletion.
- A feedback button on every rating ("good match?") to build the evaluation set.
- **The Claude connector for every account.** Each person adds the agent to
  their own Claude (claude.ai or the app) and signs in with OAuth; its tools
  see only that person's profile and results, within their caps. It replaces
  today's one-person connector behind a secret URL (docs/connector.md).
- Invite codes to keep the beta to 20–50 people.
- **Done when:** 20+ people have used it for two weeks, cost per person is
  measured, and at least 100 "good match?" answers are collected.

### Phase 3: Applications and more boards
- Application tracking by email forwarding: each person gets an address and
  forwards job emails to it. The existing reader classifies them (rules first,
  then the model on unclear ones, every claim quoted).
- People can ask for a company to be added. Board discovery finds its careers
  feed, and the shared watcher starts polling it.
- **Done when:** forwarded emails update applications with no manual steps,
  and the board list grows from what people ask for.

### Phase 4: Mobile app
- An Expo (React Native) app on the same API: today's matches, job detail,
  applied tracker, and a push notification for a strong new match.
- **Done when:** it's on TestFlight and in Play's internal testing for the
  beta group.

### Phase 5: Sustain and scale
- Payments only if AI and hosting costs call for it: a free tier with caps,
  and a paid tier with more ratings.
- Gmail sign-in for inbox reading, only after Google's verification.
- Open beta.

## Costs and guardrails

- **Models:** ranking runs in two passes. A low-cost model reads every new
  posting, then Claude re-checks the strong and possible ones within each
  person's daily cap. In the first trial, a batch of four postings on Claude
  cost about $0.06.
- **Caps:** per person per day, a global per month, and per provider (in
  `data/llm-limits.yaml`). Reaching one pauses that work until the next day.
- **Jev for the cheap, typed questions.** Jev (TypeSafe) answers fixed
  questions - pick one, yes/no, a score - with a confidence, writes no prose,
  and costs about $0.04 per million tokens. Candidate uses, each switched on
  only after it scores well on a set with known answers:
  1. sorting the inbox emails the rules can't place (the evaluation,
     `mail eval-jev`, already exists);
  2. the first ranking pass, before Claude re-checks the strong ones;
  3. the no-invention check below: "is this resume line backed by a
     confirmed fact? yes/no" - an answer that can't add a claim.
- **Hosting:** free and starter tiers during the beta. Cost per active person
  is reported weekly.

## Quality

- **Evaluation set for ranking:** postings with known answers, scored before
  any prompt or model change. It starts from a blind trial and grows from the
  beta's "good match?" answers.
- **No-invention check:** an automated test that every fact on a generated
  resume traces back to a confirmed profile fact.
- **Tests and CI on every push.** `main` is always deployable.

## Decisions

Made:

- **License: AGPL-3.0** (2026-10-11). Anyone running a modified copy as a
  service must publish their changes.
- **Hosting: Supabase + Render** (2026-10-11). Supabase for sign-in (email
  magic link and Google), Postgres and resume-file storage; Render for the
  Python API and the worker.
- **Order: Phase 1 before any hosting** (2026-10-11). The maintainer tests as a
  real user would - upload a resume, confirm the facts - with his own data
  kept outside the repository.
- **Hosted web app: React + TypeScript** (2026-10-11), in `web/`, on the
  same API as the local app (docs/hosted.md). The local app keeps its
  no-build page.
- **Market: India first** (2026-10-12). The boards, cities and salary terms
  already lean Indian; the beta invites people looking for work in India, and
  the privacy terms follow India's DPDP Act.

Waiting on the maintainer:

- **Product name and domain** for the hosted app.
- **Hosting accounts:** creating the Supabase and Render accounts (Phase 2).
- **Monthly budget** for the beta: AI and hosting.
