# The inbox

```bash
python -m jobsearch.cli --profile <id> mail sync --dry-run   # read and decide, write nothing
python -m jobsearch.cli --profile <id> mail sync             # record it (the cycle does this)
python -m jobsearch.cli --profile <id> mail list             # every application and where it stands
python -m jobsearch.cli --profile <id> mail look             # what the rules were unsure of, and Groq's reading
python -m jobsearch.cli --profile <id> mail ask              # ask Groq about flagged records now
python -m jobsearch.cli --profile <id> mail rules            # rules Groq proposed; `rules approve <id>` / `reject <id>`
```

The app's **Applications** screen shows the same, with one-click corrections.

This is the local reader: one person's Gmail inbox, read with an app password.
The hosted service never asks for mail access.

## On the hosted service: pasted emails (`mail_paste.py`)

A person pastes a job email on the Applied screen, or adds an application they
made anywhere. The same rules read a pasted email - its From, Subject and Date
lines if they pasted them, else what they type - and it lands in the same
ledger, linked to a tracked job and moving its stage like a synced one.

- **No model reads it.** When the rules can't tell what an email says, the
  person picks (received, interview, rejection, offer, on hold, withdrawn, a
  recruiter reaching out); when it names no company, they type it.
- **Kept like any email:** the subject line, the sender, the sentence that
  decided it. Never the rest of the text.
- The same email pasted twice is one event (a hash of sender, subject and
  text). An email holds 20,000 characters.

A forwarding address per person, read by the same rules, is still to come
(docs/ROADMAP.md, Phase 3).

## Setup

1. On the job-search Gmail account, turn on 2-Step Verification, then create
   an app password at https://myaccount.google.com/apppasswords. It's 16
   lowercase letters. **Not the account password**: an app password only opens
   mail and can be revoked on its own.
2. In `.env` (git-ignored):
   ```
   GMAIL_USER=<the job-search address>
   GMAIL_APP_PASSWORD=<the 16 letters>
   MAIL_PROFILE=<the profile this inbox belongs to>
   ```
   An inbox is one person's: only the profile `MAIL_PROFILE` names ever reads
   it, so a second profile on the same machine can never see the first one's
   mail.

## Read-only, by construction

- All Mail is opened with EXAMINE (`readonly=True`), so the server refuses changes.
- Every fetch is `BODY.PEEK`, so nothing is marked read.
- The only IMAP commands are LOGIN, LIST, SELECT, UID SEARCH, UID FETCH and LOGOUT.
  `tests/test_mailbox.py` reads the source *and* runs a fake server that records
  every command. Switching either guarantee off fails both tests.
- The ledger stores the date, subject, sender, message-id and the one sentence
  that decided each email. **Never the body.**

Each check is one question: "anything after the last message I read?" Usually
the answer is no: a few KB, under a second. The sync itself makes no model
calls; only records it flags go to Groq (see below), and usually there are none.

## What a real inbox taught the rules

Built from a read-only survey of one job-search inbox over 60 days (880
messages, about 155 about applications). Each finding is a test in
`tests/test_mail_rules.py`, with every company and person made up:

| Found | Rule |
|---|---|
| One employer's fraud-warning footer mentions a "job offer", so all 13 of its emails looked like offers | Cut the body at the first boilerplate marker |
| Several receipts say "*If* you're not selected…" | A match inside an if-clause is a condition, not an outcome |
| One rejection is titled "Thank You for Applying" | The body decides, never the subject alone |
| One bank rejects with "other **applicants**" | Rejection wording covers applicants and candidates |
| "assessment" in a receipt's small print made 8 receipts look like interviews | Every strong pattern is tried before any weak one |
| LinkedIn's body often says nothing | Its links name the event (`application_rejected`) |
| Naukri's "you applied for 19 jobs" lists one; the rest are recommendations | Naukri is counted, never listed job by job |
| Neo's "applied on your behalf" was 0 in every email | Read the numbers; don't assume |
| Two employers write from Oracle's hiring platform | The most specific sender listing wins, so they aren't "Oracle" |
| "we are no longer recruiting for this role" | A separate outcome: **cancelled**, not rejected |

The first review of *Needs a look* (six records) added five more:

| Found | Rule |
|---|---|
| An employer writes as `<tenant>@myworkday.com`; another from Greenhouse's no-reply | The body names the employer ("a career with Globex", "applying to Initech!"). Read after an organisation's own sender name, since a receipt's body may carry the legal "Pvt. Ltd." name, but before a bare one |
| "Globex" writing from `globex@myworkday.com` | A Workday tenant named after the company is the company, not a person |
| A marketplace: "your application is almost there" | Unfinished isn't applied: it wanted a profile on the marketplace first and named no employer |
| An agency "submitted an application on your behalf" | An agency's submission is a receipt. Only the weak "next steps" matched, so it read as an interview |
| "begin your assessment", "move you to the next stage" | Rounds said plainly count as strong matches, so they're no longer unsure |

Replayed from scratch over the same 60 days, the only differences were those
records: nothing lost, split or renamed elsewhere.

Then a marketplace's "Congratulations on your first matches: 53 roles awaiting
your response" showed as an **interview**. Its only signal was the weak "next
steps". Groq read it correctly as "not an application", but its answer waited
for a click while the guess showed.

| Found | Rule |
|---|---|
| A marketplace's "your first matches", "roles awaiting your response" | Dropped by subject, like job alerts |
| "You're receiving this because you opted in to job updates" | A subscription footer makes an email **marketing**, which is never recorded. It decides only when no strong pattern did: a rejection with a subscription footer is still a rejection |
| A weak match set the status anyway | An unsure reading never sets the status. Until Groq or the person settles it, the record says **checking** |

## Matching an email to an application

1. **Requisition id.** One employer had three applications in flight; only the
   id told the rejection of one from the two still open.
2. **Mail thread**, but never across different ids or clearly different roles:
   Gmail threads by subject, so one employer's three receipts shared one
   thread, and so did another's two.
3. **Company and role.** Two roles at one company are two applications.
4. **Company alone**, only when one application there is open, and flagged.

A dry run on real mail linked one requisition to a *different* tracked posting
on a matching title. Now two different ids never link, however alike the
titles. The guard first knew only Workday's `R-` numbers: when an Oracle board
was added, a rejection linked by title to a different open job and closed it.
Oracle's plain numbers count as requisitions now.

## What it changes, and what it never does

- **Applications** (`var/<id>/applications.json`) are created and updated from
  email: applied, interviewing, rejected, cancelled, offer, on hold, "update on
  the portal", recruiter approached.
- A **tracked posting** linked to an application moves with it: applied →
  interviewing → offer, or closed on a rejection or cancellation. It only moves
  forward, never over the person's own *skipped* or *closed*, and each move
  carries a note naming the email.
- **Needs a look**: anything the rules were unsure of, a company name taken
  from a bare sender name, or a match by company alone. *Looks right* (optionally
  correcting company and role), *Set status*, *Same as…* (merge two records), or
  *Not an application*. A corrected company name is remembered, so the next
  email from the old name still finds the record, and a merge keeps the names
  of the record it removes.
- **A correction and a sync never overwrite each other.** Both load, change
  and save the ledger under a short lock (`var/<id>/.edit.lock`). Without it, a
  click between the sync's load and save was lost, or it erased the sync's new
  events, whose mail the saved inbox position then never read again. Tracked
  postings' stages use the same lock.

## When the rules aren't sure: Groq's reading

Anything in *Needs a look* is sent to Groq at the next check (at most three
records a cycle). It comes back with the company, the role, and what each
email means, **each with the words it quoted**.

**Applied without a click when it can back everything**: if every claim
survived the quote check, and it answers every question the record was flagged
with, the reading is applied at once. It shows under *Settled by Groq*, with
what it changed and an **Undo**. Undo puts the record back exactly as it was,
with the reading waiting for a click, and it is never applied on its own
again. Otherwise it waits on the card for *Use Groq's reading* or *Dismiss*.
That happens when:

- it said something it couldn't quote,
- it left an unsure email unsettled or the company unnamed,
- it says the record is the same job as another (merging stays a click),
- it says "not an application" about a record that has emails it didn't read,
- or an email was matched to the record by company alone.

Either way those emails aren't asked about again. A rule learned from a
reading (below) always waits for approval.

**What is sent, per flagged record, and nothing else** (`mail_ai.py`):

- why it was flagged, and the record: company, role, status, dates
- the flagged emails, at most three: date, sender's display name and domain
  (never the address), subject, and the text above the sign-off, cut to 1200
  characters. Email addresses, links, phone and ID numbers, money and the
  person's name are replaced by `[email]`, `[link]`, `[number]`, `[money]`,
  `[you]`; requisition ids and dates stay, because they tell applications apart
- other records it might be the same job as (same company or mail thread)

Never the resume, other mail, or anything else in the inbox.

- **Only Groq.** The inbox's calls are pinned to it and never fall back to
  another provider. With no key, or one Groq rejects, nothing is sent. The key
  is checked first with a request that carries no email text, and the app
  says why Groq wasn't used. Every call is held to the caps, the person's
  included.
- **What left the machine can be read back.** *What was sent to Groq* on the
  card shows each call's exact text, from `var/<id>/mail/sent-to-model.jsonl`.
  Once answered, the text is deleted and only the time, size and SHA-256 stay.
- **Believed only on its quotes.** A company or role must sit inside words
  Groq quoted, and those words must be in what it was sent; an outcome must
  quote its sentence; "same as" must be one of the records it was shown.
  Anything else is dropped and listed on the card.

Found on the first real run: the Groq key had expired, and the first version
sent all five records to a key that was refusing them. Now the key is checked
before anything is sent, and a rejection mid-run stops the run.

## Learned rules

A reading can also propose a rule (`mail_learn.py`), so the same kind of email
isn't unsure next time. There are two kinds, and neither is code:

| Kind | Example | Replayed against |
|---|---|---|
| phrase | "submitted an application on your behalf" means *applied* | the last 60 days of mail, read again |
| sender | `globex@myworkday.com` is Globex | the ledger |

A rule is approved on **what it would change**, listed on the card under
*Rules waiting for you*, not on its wording: a rule that reads right once broke
a record nobody was looking at. Approving writes it to the profile's
`mail-learned.yaml` (each person's rules are their own), applies it to the past
records it listed, and every later email is read with it. Each approved rule
becomes a case in `tests/test_mail_learned.py` for the sample profiles. A
rejected rule is never proposed again.

Refused before anyone sees it:
- a phrase that isn't in the email, isn't 4 to 15 words, contains a removed
  detail, names the company or role, is already a rule, or changes nothing
  in 60 days of mail
- a sender that speaks for more than one company: a platform's no-reply, a
  person at a job board or agency, a free mail domain, a platform that shares
  its name with a customer (`kekamail.com`), or an address already seen
  writing for another company

What a rule can't express, such as the order names are tried in, comes to a
person as a code change.

## Measuring Jev against the rules

`jobsearch mail eval-jev` sends recorded emails to TypeSafe's Jev, a
classification model that answers with a label and a confidence and writes no
text. It also sends 40 job alerts and newsletters the rules dropped unread. Jev's
labels are then scored against the answers already known (`mail_eval.py`).
It is a measurement only: nothing in the cycle calls Jev until it scores well.

- **Known answers are graded.** An answer the person, Groq or an approved rule
  settled counts as *confirmed*. One the rules were sure of counts as *rules*.
  The rules' guesses are never scored; Jev's reading of them is listed for the
  person to judge.
- **The comparison is lopsided.** Most known answers came from the rules, so
  only Jev can be scored fairly on them. Where they disagree, the email is
  listed, because either may be wrong.
- **What is sent per email:** the sender's domain (not the address), the
  subject, and the body cut to 3,000 characters, redacted as for Groq.
- **What is kept:** `var/<id>/mail/jev-eval.json` holds message ids, subjects,
  the answers, and each sent text's size and SHA-256. It never holds a body.
- `--dry-run` counts what would be sent and its cost, and sends nothing. The
  run is held to Jev's caps in `data/llm-limits.yaml`.
