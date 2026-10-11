# Scheduling

Triage has nothing to rank until the watcher has been running. Everything
downstream is worth exactly as much as the number of times `watch` gets run,
and running it by hand does not scale past the first week.

## One cycle, not three tasks

```bash
python -m jobsearch.cli cycle                       # watch once, then every profile
python -m jobsearch.cli --profile sample-backend cycle   # watch, then one profile
python -m jobsearch.cli cycle --quiet               # log only, for a scheduler
```

One cycle polls every board **once**, for everyone, then for each profile:
marks what closed, triages what that profile hasn't seen, ranks within its
caps, reads its inbox (only the profile `MAIL_PROFILE` names) and redraws its
dashboard. A profile added today reads the whole queue on its first cycle.

The steps are chained rather than scheduled separately because they are
strictly ordered: triage reads what watch wrote, the dashboard reads what
triage wrote. Independent tasks would eventually interleave and render a
dashboard from a half-finished run.

**A failed step does not stop the cycle,** and one profile's failure never
stops the next profile's. Each step is wrapped, logged with its traceback, and
the next one still runs. A cycle that died on one flaky board would stop the
watcher - and a stopped watcher looks exactly like a quiet job market, which
is the worst possible failure because it is invisible.

The exit code is non-zero only on a real failure, so a scheduler's *Last
Result* column means something. **A cycle that finds nothing new is a success.**

## Logs

`var/_shared/logs/cycle-YYYYMM.log`, one line per run:

```
2026-09-23T07:47:44+00:00  watch boards=24 new=4 closed=5 skipped=0 | sample-backend triage kept=0 | sample-backend dashboard 25KB
```

## Installing on Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts\schedule.ps1 -Action install
powershell -ExecutionPolicy Bypass -File scripts\schedule.ps1 -Action status
powershell -ExecutionPolicy Bypass -File scripts\schedule.ps1 -Action remove
```

Two tasks:

| Task | When | Why that cadence |
|---|---|---|
| **jobsearch-platform Cycle** | every 15 min | The ATS APIs read the same database as the careers page, so freshness at the source is near-instant. The polling interval *is* the freshness. |
| **jobsearch-platform Verify** | Sundays 09:00 | Boards do not move often. Re-checking identity every 15 minutes would be 96x the requests for no extra signal. |

Both run **as the current user, only while logged on, with no stored password**,
and use `pythonw.exe` so nothing flashes on screen.

- `-StartWhenAvailable` catches up a run missed while the machine was asleep.
- `-MultipleInstances IgnoreNew` means a slow cycle is never overlapped by the
  next one.
- `-ExecutionTimeLimit 30m` stops a hung fetch from blocking every later run.

The hosted service will run the same cycle in a worker (docs/ROADMAP.md).

## What 15 minutes actually costs

24 boards, one request each, at most one request a second per host. About a
minute of wall time and ~2,400 requests a day across the ATS providers - well
inside what a public JSON API expects, and every one of them is a request a
human refreshing the careers page would make anyway. The cost does not grow
with the number of profiles: the boards are polled once for everyone.

**No model is involved in watching.** The whole point of the diff is that a
posting reaches a model once per person, the day it appears.
