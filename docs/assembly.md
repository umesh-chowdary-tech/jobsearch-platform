# Assembly — the resume library

How a posting becomes a resume, from one person's profile.

```bash
python -m jobsearch.cli --profile <id> assemble --top 3        # for the best-ranked postings
python -m jobsearch.cli --profile <id> assemble --posting <id>
python -m jobsearch.cli --profile <id> assemble --no-model     # skip the summary line
```

Writes `var/<id>/resumes/<variant>.html`, `.txt`, `.tex` and (with a TeX engine)
`.pdf`, and records the variant in `var/<id>/resume-variants.json` with every
posting it was sent to.

## The hard constraint

> The assembler may only ever choose among facts and skills the person's store
> already backs. It optimizes **presentation**. It never optimizes **content**.

Everything below follows from that.

## Facts are emitted verbatim

Selection, ordering and emphasis are deterministic. The facts themselves are
copied from the store unchanged.

"The assembler selects and rewords" invites paraphrase, and paraphrase is how a
user count turns into a bigger, rounder one - one small rewording at a time,
across several resume variants, until nobody can say where the number came
from. An assembler that rewrites reintroduces exactly that, on every
generation.

**A model gets exactly one job: the summary line.** Everything else is code.

## The summary guardrail

Every number in the summary must appear in the facts it was given, **with the
word it counts**. Not a prompt saying "no new numbers" - the numbers are checked,
by `numbers_grounded` in `grounding.py`, the same check every outreach draft
goes through.

A model asked to write a resume summary will reach for "improved performance by
40%", because that is what resume summaries sound like. That is precisely the
invented-percentage failure the store exists to end, and a prompt is a hope
where a check is not.

Checking digits alone was not enough. "3 years of experience" passed, because
"under 3 months" was a fact - same digit, invented claim. So each number is
paired with its unit, `40%` is not `40`, and spelled-out numbers ("ten years")
must appear in a fact as words.

Rejected summaries are dropped and the resume ships without one. The prompt's
own example is generic (`assemble.SUMMARY_SYSTEM`); the person's facts arrive
as data, grouped by role.

### Grouped by role, not flattened

The first summaries conflated roles: a "second-in-command" fact from one job
was attributed to another, because the facts had been flattened from four
entries into one list and the model had no way to tell which job was which.
Grouped by role (`ROLE: <headline> (<where>, <period>)` and its own facts), the
same model kept each fact with its job. Flattening that loses structure while
the output stays confident is a recurring bug in this project: the boards file,
the gap ledger, the profile digest, and this.

## Parse safety, not a vendor's "ATS score"

| | Rule | Verified by |
|---|---|---|
| **Parse safety** | Single column. No tables, text boxes, images. Contact details in the body, never a header or footer. Standard section headings. Real text layer. | Extract the text; assert name, email, phone, every employment date and every emitted skill survives |
| **Vocabulary** | Store says `Node.js`, posting says `NodeJS` → use theirs. Both true, matching the string is free | Diff emitted tokens against the posting's vocabulary |
| **Title** | The official title plus a descriptor from `identity.approved_descriptors`. Never a coined one; with none approved, the official title stands alone | Assert against that list |

Eight checks run on every build, and two tests exist purely to prove the check
can fail - a missing skill and an injected table. A green check that cannot go
red is decoration.

## What the first real build found

The deterministic resume passed 8/8 parse checks on the first run. Reading it
found four things no test would have:

- **Duplicate claims.** A store held the same claim as a fact *and* as a
  measured metric, worded differently. Both printed. Substring matching did not
  catch it - the words were reordered - so bullets now compare significant
  tokens.
- **Reverse-chronology broken.** Relevance sorting put a 2023 workstream above
  a 2026 one. Relevance decides *which* entries appear; it must not decide the
  order, or the reader sees 2023 above 2026 and stops trusting the dates.
- **An empty achievement.** A `profile` entry's headline printed without the
  facts that carried its actual numbers.
- **26 skills**, including model tooling on a backend posting. Capped,
  posting-relevant first, so the cut falls on the irrelevant tail.

## The PDF is what gets checked

`assemble` compiles the `.tex` with [Tectonic](https://tectonic-typesetting.github.io)
and runs the parse checks on **text extracted from the PDF** (`pypdf`), not on
the source. `--no-pdf` skips the step; with no engine installed, the step
reports that and everything else still runs.

The first real compile showed why the source checks weren't enough. The PDFs
looked right and extracted wrong:

| Extracted | Should be | Cause |
|---|---|---|
| `Conﬂuence`, `ﬁne-tuned` | Confluence, fine-tuned | ligature glyphs in the text layer |
| `Post-` / `greSQL` | PostgreSQL | hyphenation across a line |
| `T echnical` | Technical | a kerning gap read as a space |

The fix is OpenType Charter (`fontspec` + XCharter) under XeTeX, hyphenation
off, ragged right. A mutation test showed the font is what matters. The
preamble branches on the engine, so the same file still compiles with pdfLaTeX
on Overleaf.

**Install:** download the Tectonic release for your platform from the
project's GitHub releases, check its SHA-256 against the release page, and put
the binary on `PATH` (or set `$TECTONIC`). The first compile fetches the
packages it needs, then works offline; the package server fails
intermittently, so `compile_pdf` retries those failures. `python -m
jobsearch.cli tex-check` compiles a sample resume and checks the PDF, so you
know the engine works before a real resume needs it.

**On Render** the build does this itself (`render.yaml`):
`scripts/install-tectonic.sh` puts a pinned, checksum-checked Tectonic in
`.tectonic/bin/` and runs `tex-check`, so the packages are fetched into
`.tectonic/cache/` before anyone builds a resume. Both stay inside the
checkout, the part of the build Render keeps.

## What Workday found

A real Workday application parsed a resume that read well and passed every
check into **Job Title "Checker"**, **a January start date**, and skills that
included a database the person never used. Three causes:

| Parsed | Cause | Fix |
|---|---|---|
| Job Title "Checker" | The title was only in the header. Under Experience, six workstream headings each had a date range, and a parser reads every dated line as a job | One dated line per job, title first: `Software Engineer II (Backend AI Engineer)  Apr 2022 - Present`, then `Acme Freight, Pune, India`. Workstreams carry no range |
| A January start | The range was rebuilt from the selected entries. The oldest entry was cut for length, leaving "2022 - Present" | `identity.employment` holds the employer, place and start month. The range comes from there |
| QA and database skills | Workday ignores the Skills section and infers from text: "extractive QA" and a course named "DBMS" | "extractive question answering" in the store. A course name is a fact and stays; the Apply kit lists the suggestions to remove |

A workstream that began after the job did says **"· since 2025"**: a single
year, which no parser takes for a job. Three checks run on every format, PDF
included: *job title in experience*, *one dated line per job*, and *start
month*. A test runs them on the old layout and requires all three to fail.

Workday doesn't fill **Certifications** or **Languages** from the resume.
Those are always manual (the Apply kit lists them).

## The ATS match (`src/jobsearch/ats.py`)

The first design picked one resume per "archetype" (an angle tag), reused it,
and rejected optimizing any "ATS score". It broke on a *Java Full Stack
Developer* posting: the angle was a coin toss between three tied archetypes,
the only Java on record was a low-priority entry that could never print, and
the resume named neither Java nor Spring Boot. Patching any one rule fixes that
posting and leaves the next one to the same kind of rule. So there is one
mechanism instead: **the match decides, and a new variant is written whenever
it beats every existing one.**

### Why optimizing a match is safe

The danger was real: an optimizer told to maximise keyword match will discover
that adding Kubernetes raises it. The constraint answering it is unchanged -
**the assembler chooses; it never writes.** It picks store entries, whose facts
print verbatim, and skills `skills.yaml` backs. A keyword the person cannot back
stays in the denominator, lowers every candidate equally, and is reported as a
gap.

One hole had to be closed for that to hold: a fact can *name* a skill it does
not let the person claim ("stored uploaded documents in Azure Blob Storage" is
true, but Azure is `incidental`). Only claimable skills, plus table-stakes words like Agile when a
fact really says them, earn credit.

And "ATS score" here is not a resume-checker's invented number. Workday and
Oracle rank or grade applicants on their own, Workday read skills from the body
text and ignored the Skills section, and recruiters on every system search by
keyword. All three reward the same thing, which is what `ats.py` measures.

### The weights

| Where the posting names a skill | Weight | Why |
|---|---|---|
| the **title** | 3 | what a recruiter types into search |
| under **required** | 2 | `split_requirements`, as triage reads it |
| **preferred** / elsewhere | 1 | a wishlist is not the ask |
| title **role words** ("full stack developer") vs the descriptor | 3 | answered by the approved descriptor that shares the most words |

| Where the resume says it | Credit |
|---|---|
| Experience text (bullets, headline, stack line) | 1.0 |
| Projects | 0.8 |
| Summary | 0.7 |
| Education, certifications, achievements | 0.6 |
| Skills line only | 0.5 |

A skill named in a second and third entry adds a little (depth, saturating at
two repeats) and only breaks ties. The match is the weighted share of the
posting's asks on the page; the **ceiling** is the most any page from the store
could reach. Both are recorded on every `sent_to` row and shown in the app.

### Selection is one rule

Each of the six work and three project slots goes to the entry that raises the
match most, given what is already chosen. The newest role is always first.
When two entries raise it equally: the person's own "cut this first"
(`priority: low`), then a measured number, then a link a reader can open, then
recency. So a low-priority entry is the first thing cut, unless it is the only
place something lives - it prints on postings that need what only it carries.

### Stack lines and practices

Scoring a real store exposed the larger problem: **bullets say what was built
and almost never with what.** Everything else lived in `tech` and reached the
page only through the Skills line - the section Workday ignored.

So each workstream and project ends with `Stack: ...`: the skills from the
page's own Skills line that this entry's `tech` backs, posting-relevant first, at
most six. It moves no claim and makes none - it only puts the claim where a
parser reads it.

Ways of working are not a stack. Agile, Code Review, Debugging, Unit Testing,
System Design and TDD (the `methods` category in `skill-vocabulary.yaml`) print
once per job, as `Practices: ...` under the employer line. Under every
workstream they repeated six times and pushed the real stack off the line.

The Skills line uses the posting's spelling when it differs only in punctuation
(`NodeJS`), but not a lowercase word from running prose ("member of an agile
team"): every ATS matches case-insensitively, so the lowercase form gains
nothing and reads worse.

### Pinned skills

`always:` in a profile's `skills.yaml` pins skills to every resume - still only
where an emitted entry backs them. Pinned skills follow the posting's own asks
on the Skills line, so the cap never drops them. A pin is outside the match, so
the match alone would undo it (a stored variant without the pin ties a fresh
one with it, and ties reuse); a stored variant missing a pin is therefore never
reused. The 19-skill cap counts technologies only, so practices and pins never
push a real technology off.

### The library

The best page for the posting is built fresh; every stored variant is scored
against the same posting by the same match. **The existing one is reused only if
the fresh one does no better** - so rebuilding a posting, or building a twin of
it, reuses; anything better becomes a new variant. A variant that has been sent
is never edited: reuse once rewrote a variant in place, and the record of what
went to one employer stopped being true. There is no cap: merging two variants
makes both worse for the postings they were built for, and since the facts are
verbatim the variants cannot drift apart in what they claim - only in what they
choose.

New ids say what the resume leads with (`java-react-aws`), made unique: two
variants once shared an id, and one overwrote the other's PDF.

### Measured

For one profile, on every kept posting (527), old selection vs new, scored by
the same match:

| | Mean match |
|---|---|
| Old selection, as it printed | 29% |
| Old selection, with stack lines | 35% |
| New, same store | 45% |
| New, after the person confirmed practices they had used but never written down | 63% |
| Ceiling (every keyword the store reaches, at full credit) | 64% |

The new page is never below the old one. **Is it the mechanism or the
constants?** Changing any one weight (Skills-only credit 0.3 or 0.7, projects
0.6 or 0.95, title 2 or 5, required 1.5, depth 0.05 or 0.25) left the chosen
entries and descriptor identical on all 132 postings sampled. The choices come
from which entries carry which keywords. `test_ats` checks the same on the
sample profile.

### What the match cannot fix

The asks no entry says anything about. Some are gaps; some are almost
certainly true and simply never written down - but only the person can say
which, and a word goes into the store only when they confirm it. In the
measurement above, confirming a handful of practices (System Design, Code
Review, Agile, Debugging, NLP, Git, unit testing), each tagged only on the
entries it belongs to, moved the mean match from 45% to 63%. The ones the
person left out stay out.
