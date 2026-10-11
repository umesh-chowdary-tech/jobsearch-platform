# The app

```bash
python -m jobsearch.cli --profile sample-backend serve   # opens http://127.0.0.1:8765
```

One page in your browser that runs everything the CLI does, for one profile.
It's a local server on the standard library with no build step, and the page
is plain JavaScript. The static `var/<id>/dashboard.html` is still regenerated
every cycle, so there's something to look at when the server isn't running.
This is the local, single-person app; the hosted web app is Phase 2 of
docs/ROADMAP.md.

## Screens

| | For |
|---|---|
| **Today** | Strong and possible fits you haven't decided on, referral follow-ups due, escalations |
| **Postings** | Everything kept, filtered by your decision, the model's fit, or text |
| **Posting** | Fit and gaps, then resume → apply → referral, with history. Where the work happens |
| **Resumes** | Variants with PDF, HTML, text and TeX; hand-written ones from `var/<id>/manual/resumes/` |
| **Outreach** | Every referral ask, and what each needs today |
| **Market** | The skill-gap chart, the same one the dashboard draws |
| **System** | Schedule, models, PDF engine, run any step, resolve escalations, boards, cycle log |
| **Profile** | The store and skill tiers, read-only. Edit the YAML to change them |

## Decisions: `var/<id>/pipeline.json`

The only record of what you did, as opposed to what the agent found:
`new → shortlisted → applied → interviewing → offer`, plus `skipped` and `closed`
at any point. Any move is allowed, including back, and the history keeps every
step. Marking a posting applied records which resume variant was sent. A
referral isn't a stage, because it can come before or after applying;
`outreach.json` holds it.

## Favorites and save for later

Two flags next to the stage, not stages themselves:

- **★ Favorite**: "I want this one." One click toggles it. Favorites sort first
  in every list, and the Postings filter can show only them. The star flips the
  moment you click and flips back if the save fails, so it never shows a state
  the server doesn't have.
- **Later**: "Not now, ask me again." Pick the day on a calendar (tomorrow to
  90 days out), use a 3 day / 1 week / 2 week shortcut, or choose no date. It
  leaves Today, then **comes back on its date** in its own *Back from later*
  section. A picked day means that day's local midnight, sent with the
  browser's offset, so "Oct 8" returns on the morning of Oct 8 where you are,
  not at 05:30. A saved-for-later list nobody opens is where postings go to expire.
  Undated ones are counted on Today, so they aren't forgotten either. Deciding
  anything (shortlist, apply, skip) clears it, because "later" meant "decide
  later". *Bring back now* undoes it.

Both live in `var/<id>/pipeline.json` beside the stage.

## Apply kit

Workday has no API for candidates, and every employer runs its own site, so the
form stays manual: your hands, your submit. The **Apply kit** on each posting's
page removes the thinking. It lists, each with a Copy button:

- **Experience fields** to overwrite when the parser gets them wrong (it read
  Job Title "Checker" and a January start on the first real application)
- **Skills to add**: what the posting asks for that the store backs first,
  then the rest of the sent resume's skills, in the form's own wording
- **If suggested, keep / remove**, each with a reason, plus every skill the
  store bans or only touched; and **don't add**, the posting's asks that nothing
  backs
- **Certifications** (one row each; an entry listing three courses is three rows),
  **education**, **languages**, **links**

The same kit prints with `python -m jobsearch.cli kit <posting>`. A skill the
store doesn't back can never appear in it, even when a posting asks for it or
the resume variant lists it. Tests put Kafka, Azure and LangChain in both
places to prove that.

### Learning the form's words: `ats-names.yaml`

Workday picks skills from its own list: "Python (Programming Language)", not
"Python". Every name in `data/ats-names.yaml` (shared) is marked **seen**
(observed on a real Workday form) or is a **guess**, and the kit badges each
one. When an application shows the real wording, record it there with
`seen:` and the next kit is exact for everyone. Which of Workday's
suggestions to remove or keep depends on the person, so those lists live in
the profile's own `ats-names.yaml` (see `profiles/sample-backend`).

## One writer at a time, across processes

The scheduled cycle is a separate Windows task, so an in-process lock wasn't
enough. Both the app's actions and the cycle take an OS lock on
`var/_shared/.busy.lock` (`busy.py`) - one lock for everyone, because the
watcher's files are shared. When it's held:

- the app refuses the action and says who holds it and since when
- the scheduled cycle skips its tick, logs `cycle SKIPPED busy: ...` and exits 0

An OS lock dies with its process, so a killed process never leaves a stale
lock. Tests prove both with real child processes.

## Pause and stop

The top bar has both, for the scheduled cycle and for anything the app runs.

- **Pause schedule** (1 h, 3 h, 8 h, a day, or until you resume) writes
  `var/_shared/.paused.json`. The scheduled tick then logs `cycle SKIPPED paused
  until ...` and exits 0. **Run cycle** in the app still runs: that's you
  asking. A pause that has run out is ignored, so nothing has to clear it.
- **Stop** appears while something holds the lock. It writes
  `var/_shared/.stop.json` naming that run, and the run ends at its next safe point:
  between cycle steps, between boards in watch, before each model request in
  rank. Nothing is killed, so nothing is half-written. What watch saw is
  still recorded, the boards it didn't reach keep their baselines, and the
  log says `STOPPED by you after <step>`. A request for a run that has
  already ended never stops the next one.

## Security

The server holds API keys and personal data, and a browser will send requests
to 127.0.0.1 on behalf of any page. So:

| | Stops |
|---|---|
| Binds 127.0.0.1 only | anything else on the network |
| `Host` must be `127.0.0.1:port` or `localhost:port` | DNS rebinding |
| Changes are POST + JSON + same-origin `Origin` | cross-site forms and fetches |
| Files only from the profile's `resumes` and `manual/resumes`, by resolved path | reading `.env` or the store |
| Provider names and a configured flag, never keys | keys in the browser |
| Data inserted as text; one `innerHTML`, for the server-escaped chart | script in a posting title |
| `script-src 'self'`, no inline script | injected script running |

Each protection has a test, and each test was checked by switching its
protection off: every one then fails. With path containment off, `.env` was
actually served.

## Actions

Every button calls the CLI's own function, with its output captured live into
the console at the bottom: `cycle`, `watch`, `triage`, `rank`, `verify`,
`search`, `assemble`, `dashboard`. There's one code path for each, not two.
Drafting a referral and recording a decision are quick writes to their own
files and don't take the lock.
