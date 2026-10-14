# Setup from a person's resumes

```bash
python -m jobsearch.cli setup <id>        # opens the app on its Setup screen
```

How a person gets a profile without writing YAML: in Setup, add their
resumes - every version they have - and, if they like, their GitHub, a
portfolio page and a note; let the AI read each; answer its questions if
they like; check what it found, one card at a time, choosing the true
version wherever their documents disagree; say what they're looking for;
save. After that, the profile page is for changing and adding, and every
change there saves as it's made. The code is
`src/jobsearch/onboard.py`, with `setup_sources.py` (reading) and
`setup_merge.py` (combining); the pages are `web/src/pages/Setup.tsx` and
`Profile.tsx`, with `web/src/checking.tsx`.

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
5. **Questions** (`setup_interview.py`). No question is written in the code:
   the model reads what was drafted, works out who this person is - a
   fresher, an experienced engineer, a manager, someone switching careers or
   returning after a break - and asks the few (8 at first, 5 after, 4 rounds
   at most) whose answers would most help their real experience get them
   shortlisted: their own part in team work, results and whether they were
   measured, where a skill from a list was used, unclear dates or the employer
   behind a client, missing work, a certification's status. The code holds
   the rules around it:
   - only what the documents and earlier answers don't say; nothing asked
     twice; neutral wording, never suggesting a stronger claim; any skippable;
   - never age, family, religion, caste, gender, health, salary, notice
     period, address or IDs - such a question is dropped even if the model
     writes it;
   - answers are kept with the draft. "Use my answers" turns them into facts,
     results (measured or an estimate - only a measured one is ever printed as
     a number), notes for whoever words the resume, skills used, a client, a
     missing piece of work - each quoting the answer, every number from it, no
     claim stronger than it. An answer that contradicts a file becomes a choice.
     All of it arrives "to check".

   With the questions, the model suggests each piece of work's angles (from a
   fixed list: backend, data, ml, ...) and which a resume should cut first.
   They're labels, not claims; the person changes them, and the model doesn't
   overrule them after.
6. **Check, in Setup, one card at a time.** Each card asks one plain
   question about one thing, shows the line it came from and which file, and
   takes one tap: "Your files say different things - which is right?" (or
   write your own); "Is this right?" about you, a fact, a note; "Did you work
   here?"; a piece of work with what it says ticked, to untick what isn't
   true; "Did you use Kubernetes in the refund service?" for a skill only in
   a skills list; "Is this number measured, or your guess?"; and "Add this
   line from your resume, word for word?" for a line the AI couldn't use. Yes
   adds it, Change edits it in the person's own words first, No leaves it
   out; "Skip for now" comes round again. What's under a piece of work is
   asked only once the work is added. What the person said no to can be
   asked again. Nothing is added for them. The profile page shows only how
   many cards are left, as a link to them.
7. **Edit, on the profile page.** Each part has Edit: name, location, email,
   phone and links (email and phone are shown only in this form, and never to
   the Claude connector), languages; the work history; each piece of work -
   its kind, name, where, when, what it was, facts, results (each measured or
   an estimate - only a measured one is ever printed as a number), the skills
   used in it, a note for whoever words the resume, angles and "cut first".
   The person can add a job, a piece of work or any of those in their own
   words, numbers included; dates are YYYY-MM. Skills: only those that
   confirmed lines of confirmed work name - strong (core), used it (working),
   touched it (never listed on a resume), or not a skill of theirs; a new one
   is added to the work where it was used.
8. **Preferences.** Cities, remote or not, years of experience (suggested
   from the earliest confirmed job), the most years a posting may ask, level,
   roles to skip, companies to watch - kept with the draft and checked.
9. **Save.** The first save is in Setup, under what jobs they want (and
   again after **Start over**); after it, every change writes the profile
   again (`onboard.resave`). A change that leaves
   nothing to write (no name, say) waits in the draft, and the page says why.
   Only confirmed items are written, as `master-store.yaml`, `skills.yaml`
   and `search-profile.yaml`. Each entry records where it came from
   (`sources`: "file: line", or "typed by you") and, for public work, a link
   to open (`evidence`). Saving over an existing profile keeps what was added
   to it by hand: an entry added by hand, an entry's notes, metrics, angles,
   priority, approved title descriptors, skills marked banned. What the draft
   has a say on goes by the draft, so removing it on the profile page removes
   it: a piece of work, a skill, a contact detail, an entry's results or
   notes, its angles and "cut first" once the person has set them.

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
- A job-title descriptor ("Software Engineer II (Backend)") suggested from the
  person's own titles.
