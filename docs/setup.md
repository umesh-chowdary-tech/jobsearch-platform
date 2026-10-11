# Setup from a resume

```bash
python -m jobsearch.cli setup <id>        # opens the app on its Setup screen
```

How a person gets a profile without writing YAML: upload a resume, let the AI
draft the facts, confirm each one, say what you're looking for, save. Phase 1
of docs/ROADMAP.md; the code is `src/jobsearch/onboard.py`.

## The steps

1. **Upload** a PDF, a Word file (.docx) or plain text, up to 5 MB. The text is
   read on this machine (pypdf for PDF, the standard library for Word). The
   file and its text are kept in `var/<id>/setup/`.
2. **Draft.** One model request, counted against the person's daily cap
   (`data/llm-limits.yaml`, `per_profile`). The screen shows exactly what will
   be sent before anything is: the resume's lines, numbered, with email,
   phone and links replaced by `[email]`, `[phone]`, `[link]`. Contact details
   are found by pattern here and never sent.
3. **Check.** Every item the model returns must quote a line of the resume,
   and its value must sit inside that quote:

   | Item | Kept only if |
   |---|---|
   | name, location, language | the value is in the quoted line |
   | job | employer, title and every year of its dates are in the quoted line |
   | entry headline, fact | every number in it is in its line ("40,000 refunds" can't become "50,000"), and no word that strengthens a claim ("led", "spearheaded", "architected"...) is missing from it |
   | skill | its line names it, as written or by an alias in the shared vocabulary |

   What fails is listed under **Left out** with the reason. A left-out fact can
   come back as the resume line itself, word for word - the resume's own words
   claim nothing it doesn't.
4. **Review.** Each item shows the line it came from. The person confirms,
   edits or removes it. An edit is their own words and stands as written, and
   they can add facts in their own words - including numbers, because they
   supply them. Nothing drafted is confirmed for them.
5. **Skills.** Only skills that confirmed lines of confirmed entries name. The
   person marks each: strong (core), used it (working), touched it (never
   listed on a resume), or not a skill of theirs.
6. **Preferences.** Cities, remote or not, years of experience (suggested from
   the earliest confirmed job), the most years a posting may ask, level, roles
   to skip, companies to watch.
7. **Save.** Only confirmed items are written, as the profile's
   `master-store.yaml`, `skills.yaml` and `search-profile.yaml`. Pending items
   are left out and counted. An existing profile is replaced only when the
   person confirms it.

## Keeping a real person's data out of the checkout

Set both in `.env` (gitignored) to folders outside the repository:

```
JOBSEARCH_PROFILES=C:\private\jobsearch\profiles
JOBSEARCH_VAR=C:\private\jobsearch\var
```

The profile then lives in the first, and the uploaded resume, the draft and
everything the agent works out later in the second. `jobsearch setup` says
where both will go before it starts.

## The no-invention check

`grounding.untraced_bullets(resume_text, store)` lists every bullet on a
rendered resume that no confirmed fact says word for word. Facts print
verbatim, so on a resume built from the store it is always empty; the tests
check it on a profile made by setup and on every sample profile.

## The local app's guards

The upload is the one request that isn't JSON, so it has its own guard: the
`Origin` header must be present and be this app's, the type must be PDF, Word
or plain text, and a body over 5 MB is refused before it is read. Every other
setup step is a JSON POST under the same rules as the rest of the app
(docs/app.md, "Security"). Before a profile exists, Setup is the only screen.

## Not yet

- Scanned resumes (images) have no text to read; the screen asks for a text
  PDF or a Word file. OCR may come later.
- The hosted version (Phase 2) runs the same steps behind sign-in, with the
  file in object storage instead of `var/`.
