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
  only the hosted screens: setup, today, postings, a posting and its apply
  kit, resumes, the profile, and running `assemble` or `rank`. The inbox,
  outreach, the board registry and the schedule stay with the local app.
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
- **Admin** (emails in `JOBSEARCH_ADMINS`): who joined, each person's AI
  requests and cost this month, invite codes.

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

1. **Supabase**: a project in the Mumbai region. Authentication > Providers:
   email (magic link) and Google. Authentication > URL configuration: the
   Render URL as the Site URL and a redirect URL. From Project settings: the
   URL, the anon key, and Database > the session pooler's connection string.
2. **Render**: New > Blueprint on this repository, keep `jobsearch-hosted`,
   fill in the values it asks for (`render.yaml`), including who runs the service for the
   privacy notice.
3. **GitHub**: repository secrets `HOSTED_URL` (the Render URL) and
   `CRON_SECRET` (copied from Render), for `.github/workflows/cycle.yml`.
4. Sign in, open Admin, make invite codes.

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
