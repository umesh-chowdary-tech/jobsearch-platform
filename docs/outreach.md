# Module 5 — Outreach

```bash
python -m jobsearch.cli --profile <id> outreach find 1000001                  # who to ask
python -m jobsearch.cli --profile <id> outreach draft 1000001 --to "Priya Sharma" --how alumni
python -m jobsearch.cli --profile <id> outreach draft 1000001 --to "Ravi Kumar" --how warm \
    --context "we were on the Acme Track team together until Ravi moved to Razorpay"
python -m jobsearch.cli --profile <id> outreach draft 1000001 --to "Asha Rao" --how ex-colleague \
    --employer "Umbrella Apps"                                  # a past employer you share
python -m jobsearch.cli --profile <id> outreach list            # what needs you today
python -m jobsearch.cli --profile <id> outreach mark razorpay-1000001-priya-sharma sent
```

## The agent drafts. The person sends.

`outreach.py` imports no HTTP client, no mail library and no browser driver. A
test parses its imports and fails if one appears. The boundary is enforced by
what the file *can* do, not by what its docstring promises. The only network
call it can cause is the optional opener sentence, and that goes to the model
provider, never to a person.

A draft moves to `sent` only when the person runs `mark ... sent`.

## Finding the person is still unsolved

`find` gives LinkedIn people-search links, strongest connection first:

1. **People from each employer in the profile** (`identity.employment`) now at
   the company. A shared employer is the warmest cold contact.
2. **College alumni** at the company. A real reason to write.
3. **Peers on the team** (the role title with the level stripped, plus the
   city, if it is one of the profile's places).
4. **Engineering managers**. Ask them a question about the team, not for a referral.

The person opens the links in their own browser. Nothing here fetches
LinkedIn: its terms prohibit scraping, and the account is the one asset that
cannot be risked. Picking whom to ask is still a human job, and `find` also
shows who has already been asked at that company.

## What a draft is

| Part | For | Limit |
|---|---|---|
| Connection note | the LinkedIn invite (none for a `warm` contact) | 200 chars |
| Message | the ask, with the posting link | 150 words |
| Email subject | the same message by email | — |
| Blurb | third person, for the referrer to paste into their company's referral form | 70 words |

Where the person works and lives comes from the profile: the current employer
from `identity.employment`, the city from `identity.location`, the title from
`identity.title`. The templates name no employer of their own.

A draft needs an assembled resume. If no variant's `sent_to` names the posting,
`draft` refuses and prints the `assemble` command. A referral request should
never promise a resume that does not exist yet, or claim something the
forwarded resume does not say.

## A model gets one sentence

The first version let a model rewrite the whole draft. On one posting it passed
every check that existed and was still worse than the template:

- It wrote the warm message *about* the person instead of *as* them: *"would
  you be open to referring <name>?"*
- It upgraded *"architected … as second-in-command"* to *"led the development of"*.
- It cut the company from the connection note.

Paraphrasing the facts is the failure the store exists to end. Assembly reached
the same conclusion for resume bullets.

So the draft is deterministic, built from the store's own headlines turned into
first person ("Built X" → "I built X"; "Backend engineer on Acme Track" → "I
work on Acme Track"). Headlines start with a past-tense verb or a role noun, so
this always reads right. The model writes **only the personal opener**, from
the `--context` line. That's the one part a template can't write. **No context
means no model call.**

The opener has its own gate. It is grounded against the context alone, may not
mention the job, and has any leading greeting stripped. The recipient's name
never reaches the provider: the model sees `{FIRST}`, and the name is filled in
afterwards.

## The checks

Every draft, template or not, runs the same gate. `fail` blocks; `warn` is shown.

| Check | Catches |
|---|---|
| `numbers_grounded` | a number **with its unit** that's in no fact. Digits alone would pass "4 years" because "under 4 months" is a fact. Spelled-out numbers are checked too. Shared with the resume summary (`grounding.py`) |
| `claims_grounded` | "led", "spearheaded", "owned", "expert", "large-scale"… unless a fact, or the person's own title, uses the word |
| `first_person` | a message written about the person rather than as them |
| `skills_backed` | a banned, incidental or unbacked technology. The role title and the company name are removed first, because MongoDB the employer is not MongoDB the skill |
| `no_invented_familiarity` | "great to reconnect" to a stranger; "I loved your recent post" to anyone |
| `names_company`, `names_role`, `has_link`, `easy_out`, `greets_recipient` | a message that isn't a usable ask |
| `blurb_uses_name` | a guessed pronoun; nothing in the store says which to use |
| `no_placeholders`, `no_cliches`, lengths | the obvious |
| `company_name_confirmed` *(warn)* | a name not in `data/company-names.yaml`. ATS slugs and board names are not names: *"razorpaysoftwareprivatelimited"*, *"Rubrik Job Board"*, *"Everpure"* |
| `company_identity` *(warn)* | the board has an open identity escalation |
| `posting_open` *(warn)* | the watcher saw the posting close |
| `one_ask_per_company` *(warn)* | someone else there already has an open ask |

## Tracking

`drafted → sent → followed-up → replied / referred / declined / closed`.
`list` puts what needs doing today first:

- drafted: *review and send*
- sent, 5+ days ago: *follow up*
- followed-up, 7+ days ago: *close it*

`show` prints the nudge. It says *"still open"* only when the watcher's last
poll saw the posting. If the posting closed, it says not to send a nudge at all.

Redrafting an ask already marked `sent` is refused without `--force`, because it
would overwrite the record of what was actually sent.

## Files

- `var/<id>/outreach.json`: the ledger (atomic writes)
- `var/<id>/outreach/<draft>.md`: one copy-paste file per draft, with its checks
- `data/company-names.yaml`: how each board's company is written in a sentence (shared)
