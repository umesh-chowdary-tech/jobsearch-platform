# Contributing

Thanks for helping. This is a job-search agent that people will trust with
their careers, so a few rules matter more than any feature.

## The rules

1. **Never invent anything about a person.** Code that writes a resume line, a
   ranking reason or a message may only use facts from the person's profile.
   No skill, number, title or claim they haven't confirmed; numbers are never
   rounded or filled in. If a change lets a model write a claim, it needs a
   check that the claim is grounded (see `grounding.py`, `outreach.check`).
2. **Every model call is capped.** A new provider gets an entry in
   `data/llm-limits.yaml` (with `per_profile`) before its first request. A cap
   reached pauses the work; it never falls back to a weaker model.
3. **Read job boards politely.** robots.txt, at most one request a second per
   host, public feeds only. No LinkedIn or Naukri scraping, and no CAPTCHA
   solving.
4. **No personal data and no secrets, anywhere in the repository** - not in
   code, tests, fixtures, docs, commit messages or screenshots. Tests use the
   fictional profiles in `profiles/sample-*` and made-up companies, people,
   emails (`@example.com`) and numbers. Never paste a real email, resume or key.
5. **Shared knowledge is shared; people are separate.** Anything that reads a
   person's facts or preferences takes a `Profile` (`src/jobsearch/profile.py`)
   and writes under `var/<id>/`. Board polling stays shared.

## Setting up

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev,server]"   # .venv/bin/python on macOS/Linux
.venv/Scripts/python -m pytest
```

The tests need no network and no keys: boards, models and mailboxes are faked.

## Making a change

- Keep the change focused, and add or update a test that would fail without it.
  A check that can't go red is decoration - where it matters, prove the test
  fails with the protection switched off.
- Write code that reads like the code around it: plain names, short functions,
  comments that say *why* (often the bug that motivated it).
- Run the tests, and scan for secrets before you push:
  `gitleaks git --redact .` (https://github.com/gitleaks/gitleaks).
- Open a pull request describing what changed and why. CI runs the tests on
  Linux and Windows and a gitleaks scan; both must pass.

## Reporting a security problem

Not in an issue - see [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the
project's license, the GNU Affero General Public License v3.0 (see LICENSE).
