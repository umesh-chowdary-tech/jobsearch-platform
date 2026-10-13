# The hosted service

Phase 2 of docs/ROADMAP.md: the same engine and the same API as the local
app, behind sign-in, with one profile per account, and a web app in React +
TypeScript (`web/`).

```bash
pip install -e ".[dev,hosted]"
cd web && npm ci && npm run build && cd ..
python -m jobsearch.cli invites create 3           # codes for the people you invite
python -m jobsearch.cli hosted --dev               # http://127.0.0.1:8000, sign in as any email
```

`--dev` signs its own tokens, so any email gets in. It works only on this
machine and refuses to start alongside a Supabase project. Without `--dev`
the service needs `SUPABASE_URL` (docs below). For the web app with live
reload, run `npm run dev` in `web/` too and open http://localhost:5173; it
passes `/api` to the service.

## How it fits together

```
 web/ (React)  --HTTPS + sign-in token-->  hosted/app.py (FastAPI)
                                               |  checks the token (hosted/auth.py)
                                               |  finds the person's profile (hosted/accounts.py)
                                               v
                                          web/routes.py, hosted scope
                                               |  the same routes the local app serves
                                               v
                                          the engine: triage, ranking, resumes, setup
```

- **One API, two hosts.** `web/routes.py` holds every route. The local app
  (`web/server.py`) serves all of them for its one person. The service serves
  only the hosted screens: setup, today, every kept job (search, filters,
  newest first), a posting and its apply kit, resumes, the profile (work,
  skills, skill gaps, preferences; read-only - changes go through setup), and
  running `assemble` or `rank`, and referral asks (who to ask, a drafted
  message, marking what happened; at most 80 characters for a name and 300
  for a line about them), and applications: pasted job emails and ones added
  by hand (docs/mail.md), asking for a company to be watched (below), and
  the Claude connector at `/mcp`, each member signed in as themselves
  (docs/connector.md).
  Reading an inbox, the board registry and the schedule stay with the local
  app.
- **Who you are** comes from a Supabase access token in the `Authorization`
  header of every request. A token must be unexpired, issued by the project,
  meant for signed-in users, and signed by the project's published key (or,
  on older projects, its shared secret). The algorithm picks the key: a
  shared-secret token is never checked against a public key.
- **Invite codes.** Signed in but not yet joined, a person reaches only
  `/api/me` and `/api/invite`. A code is used once.
  `jobsearch invites create N` or the admin page makes them.
- **One profile per account**, named `u-` plus a hash of the sign-in id, so
  no folder name or log line carries an email. Profiles live in
  `var/_profiles/` on the service, and what the agent works out for each
  person in `var/<id>/`, as on a PC.
- **The scheduled cycle** starts when a cron job POSTs `/api/cron/cycle` with
  the `X-Cron-Secret` header. It watches the boards once for everyone, then
  triages and ranks for each person within their caps
  (`data/llm-limits.yaml`, `per_profile`).
- **Your data:** Settings downloads everything kept for you as a zip, and
  deletes it all on request. Deleting also forgets the account; the invite it
  used stays spent, with no link back.
- **"Good match?"** on a posting is kept in `var/<id>/feedback.jsonl`, the
  start of the ranking evaluation set.
- **Admin** (emails in `JOBSEARCH_ADMINS`, `hosted/admin.py`):
  - *Overview:* people joined and active, how far they get (joined, set up,
    ranked jobs, a resume, applied), AI spend per day against the cap,
    joins per week, "good match?" answers.
  - *People:* search; pause an account (its owner can still download or
    delete their data), give one person their own daily AI limit, delete an
    account; invite codes - make, cancel unused, see who used which.
  - *AI & budget:* which model does each job, the Claude spending limits,
    one switch to pause all AI; cost this month by job, model and person.
  - *Jobs & health:* run a job update now, pause the schedule; updates and
    new postings per day, keys set or not, the database's last save, boards
    not read for two days, recent errors.
  - *Log:* every change an admin made, and who made it.

  Admins see counts and emails, never anyone's resume, profile or jobs. The
  only thing kept for the page is the day each person last used the service.
  The page's changes live in `var/_shared/` (`llm-limits.json`,
  `models.json`, `admin-log.jsonl`), so the database keeps them too.

## Security

- No cookies: the token travels in a header, so another site's page can't
  make a signed-in request on someone's behalf.
- Every response carries a strict Content-Security-Policy (scripts from this
  origin only; connections to this origin and the Supabase project), plus
  `nosniff`, `X-Frame-Options: DENY`, no referrer, and HSTS over HTTPS.
- Request bodies are capped (64 KB of JSON, 5 MB for a resume) and refused
  before they are read in full. A resume must be PDF, Word or text.
- An unexpected error answers "something went wrong" and logs the details;
  it never shows a stack trace or a path.
- Files are served only from the signed-in person's own resume folder.

## Settings

| Variable | What |
|---|---|
| `SUPABASE_URL` | the project's URL; its published signing keys come from here |
| `SUPABASE_ANON_KEY` | public by design: lets the page start a sign-in |
| `SUPABASE_JWT_SECRET` | only for projects still signing with the legacy shared secret |
| `JOBSEARCH_ADMINS` | emails that see the admin page |
| `JOBSEARCH_APP_NAME` | the name on the pages (default "Job search") |
| `JOBSEARCH_OPERATOR`, `JOBSEARCH_CITY`, `JOBSEARCH_PRIVACY_EMAIL` | who runs this copy, their city, and where people write about their data: the blanks in the privacy notice and terms |
| `CRON_SECRET` | what the cron job sends to start a cycle |
| `ANTHROPIC_API_KEY`, `GROQ_API_KEY` | the models, capped in `data/llm-limits.yaml` |

## Keeping var/ across restarts

Render's free service loses its disk when it sleeps. With `DATABASE_URL` set,
the service pulls var/ from Postgres when it starts, pushes a few seconds
after anything changes, sweeps every two minutes for what background jobs
wrote, and pushes when it stops (`hosted/mirror.py`, on `statestore.py`).
Deleting an account deletes that person's rows in the database first; if the
database can't be reached, nothing is deleted and the page says to try again.

Use Supabase's **session** pooler URL (port 5432). The transaction pooler
(port 6543) can't hold the lock a sync takes, and the service refuses it.

## Deploying

What needs the maintainer's accounts, in order:

1. **Supabase**: a project in the Mumbai region, with **Data API off** (the
   service uses Supabase only for sign-in and its own database connection)
   and automatic row-level security on. Authentication > Providers: email
   (magic link) and Google. Authentication > URL configuration: the Render
   URL as the Site URL, and `<Render URL>/**` as a redirect URL - left at its
   default (`http://localhost:3000`), every emailed link leads there. From
   Project settings: the URL, the publishable (anon) key, and Database > the
   session pooler's connection string.

   Supabase's own email sender delivers only to the project team's addresses,
   two messages an hour. For anyone else to sign in by email, set up your own
   sender (Authentication > Emails > SMTP Settings); or turn on Google
   sign-in, which needs no email. An emailed link signs in only in the browser
   that asked for it (PKCE) - the sign-in page says so when one doesn't.
   The mirror's table locks itself down either way: row-level security with
   no policies, and no grants to Supabase's `anon` and `authenticated` roles
   (statestore.py, `SCHEMA`).
2. **Render**: New > Blueprint on this repository (`render.yaml`: the one
   service, `jobsearch-hosted`), and fill in the values it asks for, including
   who runs the service for the privacy notice.
3. **GitHub**: repository secrets `HOSTED_URL` (the Render URL) and
   `CRON_SECRET` (copied from Render), for `.github/workflows/cycle.yml`.
4. Sign in with an email in `JOBSEARCH_ADMINS`, then on the admin page:
   *Jobs & health > Check job boards* once - a new copy knows no boards until
   then - and *People > Make codes* for the people you invite. To join
   yourself, use one of your own codes.

## Not yet

- **Privacy notice and terms: drafted, not final.** They are in `web/src/legal/`, shown at
  `#/privacy` and `#/terms` before sign-in, and agreed to with a tick when a person joins;
  the version and time are kept with the account. Its blanks - who runs the service, their
  city, a contact email - are filled from `JOBSEARCH_OPERATOR`, `JOBSEARCH_CITY` and
  `JOBSEARCH_PRIVACY_EMAIL`, so no operator's details enter this repository; one left unset
  shows in [brackets], and the admin page names it. A lawyer's review is still needed. Changing the
  text means changing `CONSENT_VERSION` (hosted/accounts.py), `LEGAL_VERSION`
  (web/src/pages/Legal.tsx) and the version line in both files - a test holds them together.
- **The Claude connector for every account** (OAuth), replacing today's
  one-person connector.
- **Frontend tests** once the build machine runs Node 22 (vitest needs it).

## Asking for a company (`company_requests.py`)

A person names a company on the Jobs page, and its careers page if they know
it - shown on the admin page, never fetched. Asking twice, or two people
asking, is one request; a company whose board is already read says so at
once. Five requests can wait per person.

On the admin page, "Find job boards" runs discovery for what's waiting
(docs/discovery.md: a few polite requests per company, robots.txt and rate
limits held), in the background. A board that answers goes into
`boards.json`, so the next job update reads it; one discovery doubts is
marked "check it's theirs". A board that didn't answer leaves the request
waiting; no board at all is "no job board we can read". The admin can
decline a request or stop watching a board, with a note the person sees.

The shared list (`var/_shared/company-requests.json`) holds a company, the
page given and how many asked - no one's name. Which requests are a person's
own is in their folder, and goes with their account.

## A new profile catches up (`cycle.py`)

The watcher reports only what appears on a board after it first read it -
everything already open is its baseline - so a profile made later would see
nothing older. Once, each profile reads every posting open on the boards in
that run (`WatchRun.live`), the same triage as the queue, and is marked
caught up (`caught-up.json`). Saving a profile in setup starts an update
right away when it hasn't caught up, instead of waiting for the schedule.

Pass 1 runs on the title and the location field before any description is
fetched (`triage.rejected_unread`): what fails there gets the same verdict
it would with its description, and the board is never asked for it. So a
catch-up over thousands of open postings fetches descriptions only for the
few that could fit.

