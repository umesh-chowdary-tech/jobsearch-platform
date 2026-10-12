# Setup from a person's resumes

```bash
python -m jobsearch.cli setup <id>        # opens the app on its Setup screen
```

How a person gets a profile without writing YAML: add their resumes - every
version they have - and, if they like, their GitHub, a portfolio page and a
note; let the AI read each; confirm every fact, choosing the true version
wherever their documents disagree; say what they're looking for; save. The
code is `src/jobsearch/onboard.py`, with `setup_sources.py` (reading) and
`setup_merge.py` (combining).

## The steps

1. **Add.** Up to 5 resumes (PDF, Word .docx or text, 5 MB each), a GitHub
   profile, up to 3 portfolio pages, a note. Old versions help: they often say
   what the newest leaves out, and where they disagree the person is asked.
   - **Word:** headers and footers are read (contact details often sit there),
     text boxes once. Text a reader can't see - hidden, white, under 4 pt, a
     trick for resume filters - is left out and counted.
   - **PDF:** the header, footer and page number repeated on each page are
     read once. A password-protected or scanned PDF gets a message saying what
     to upload instead.
   - **GitHub:** the person's own public repositories, newest first, through
     GitHub's API - names, descriptions, languages, topics, READMEs (no code,
     images or badges). Never stars or followers.
   - **Portfolio page:** fetched once, after its robots.txt; a public address
     only, every redirect checked again, 1.5 MB at most. A page built by
     JavaScript has no text to read: the person is told to paste it as a note.
   - **LinkedIn is never read** - kept as a link for resumes. A LinkedIn or
     Naukri address given as a portfolio page is refused.
   - **Private details are removed as each source is read**, so they are
     neither kept nor sent: date of birth, Aadhaar, PAN, passport, parents' and
     spouse's names, marital status, gender, religion, caste, home address, a
     whole "Personal Details", "References" or "Declaration" block (languages
     in it stay). What was removed is listed under the source. The uploaded
     file itself is not kept - only its text.
   - The same file twice is refused; a very long one says how much of it is
     read (60,000 characters) instead of cutting it silently.
2. **Read.** Each source is one request to the setup model (Claude Opus 5.5,
   thinking hard - `data/models.yaml`), streamed so a long answer isn't cut
   off. The screen shows exactly what each will be sent: numbered lines,
   email, phone and links replaced by `[email]`, `[phone]`, `[link]`.
3. **Check.** Every item must quote a line of its source, and its value must
   sit inside that quote:

   | Item | Kept only if |
   |---|---|
   | name, location, language | the value is in the quoted line |
   | job | employer, title and every year of its dates are in the quoted line |
   | entry headline, fact | every number in it is in its line ("40,000 refunds" can't become "50,000"), and no word that strengthens a claim ("led", "spearheaded", "architected"...) is missing from it |
   | skill | its line names it, as written or by an alias in the shared vocabulary - and a line that only *lists* skills (a "Skills" section, "Skills: ...", a run of names) marks it "only in a skills list" |

   What fails is listed under **Left out** with the reason, and can come back
   as the source's own line, word for word.
4. **Combine.** Each source after the first is folded into what came before:
   - the same job, piece of work or fact becomes one item quoting each
     source (names, jobs and languages by plain matching; pieces of work and
     facts paired by the model, which only pairs - it writes nothing);
   - **where sources disagree** - other dates, another title for the same
     time, other numbers, or one claiming more ("helped build" in one, "led" in
     another) - both versions stay on the item and it can't be confirmed until
     the person picks one or writes their own;
   - a new title at the same employer after the old one ended is the next row
     of their title history, not a disagreement;
   - a confirmed item a new source contradicts goes back to "to check".
5. **Review.** Each item shows the line it came from and which file. The
   person confirms, edits or removes it; an edit is their own words, and they
   can add facts in their own words, numbers included. A skill only in a
   skills list is confirmed only if they used it in that work. Nothing is
   confirmed for them.
6. **Skills.** Only skills that confirmed lines of confirmed entries name:
   strong (core), used it (working), touched it (never listed on a resume), or
   not a skill of theirs.
7. **Preferences.** Cities, remote or not, years of experience (suggested
   from the earliest confirmed job), the most years a posting may ask, level,
   roles to skip, companies to watch.
8. **Save.** Only confirmed items are written, as `master-store.yaml`,
   `skills.yaml` and `search-profile.yaml`. Each entry records where it came
   from (`sources`: "file: line", or "typed by you") and, for public work, a
   link to open (`evidence`). Saving over an existing profile keeps what was
   added to it by hand: an entry's notes, metrics, angles, priority, an entry
   added by hand, approved title descriptors, skills marked banned.

Removing a source takes what only it said, unless the person confirmed it;
where it was one side of a disagreement, the other side's version stays.
**Start over** clears the draft and the sources; a saved profile stays.

## Cost

Setup is the one step that reads a person's whole history with the strongest
model, so it has its own room: `setup_usd_per_day` (per person, the day it
runs) instead of the everyday `usd_per_day`, inside each person's
`usd_per_month` ($10). See docs/limits.md.

## Keeping a real person's data out of the checkout

Set both in `.env` (gitignored) to folders outside the repository:

```
JOBSEARCH_PROFILES=C:\private\jobsearch\profiles
JOBSEARCH_VAR=C:\private\jobsearch\var
```

The profile then lives in the first, and each source's text, the draft and
everything the agent works out later in the second.

## The no-invention check

`grounding.untraced_bullets(resume_text, store)` lists every bullet on a
rendered resume that no confirmed fact says word for word. Facts print
verbatim, so on a resume built from the store it is always empty; the tests
check it on a profile made by setup and on every sample profile.

## The local app's guards

The upload is the one request that isn't JSON, so it has its own guard: the
`Origin` header must be present and be this app's, the type must be PDF, Word
or plain text, and a body over 5 MB is refused before it is read. Its name
(`X-Filename`) only labels it on the screen. Every other setup step is a JSON
POST under the same rules as the rest of the app (docs/app.md, "Security").

## Not yet

- Scanned resumes (images) have no text to read; OCR may come later.
- Two-column PDFs can come out with the columns interleaved; the person sees
  the text before it is sent.
- Hidden text in a PDF (white or invisible) isn't detected yet - Word's is.
- Questions after the draft - what *you* did in team work, whether a number
  was measured, the employer behind a client - and filling in angles, metrics
  and priorities: the next step (setup plan, PR B).
