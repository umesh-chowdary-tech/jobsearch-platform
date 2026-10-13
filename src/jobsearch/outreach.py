"""Module 5 - Outreach.

Drafts a referral request for one posting and one recipient. The person sends
it; this module cannot. It imports no HTTP client, no mail library and no browser
driver, and a test holds it to that. The only network call it can cause is the
optional opener sentence, which goes through llm.py to the model provider and
never to a person.

  find   Search links for the people worth asking, in the order worth asking
         them. The person opens them in their own browser; nothing here
         fetches LinkedIn, whose terms prohibit scraping and whose account is
         the one asset that cannot be risked. Choosing whom to ask stays a
         human job - that part is still unsolved, and a link list does not
         pretend otherwise.

  draft  A connection note, a message, an email subject, and a blurb the
         referrer can paste into their company's referral form. Built from the
         resume variant that was actually assembled for this posting, so the
         message never claims something the forwarded resume does not say.

  track  drafted -> sent -> followed-up -> replied / referred / declined /
         closed. Follow-ups fall due on their own, and the watcher's state
         decides whether a nudge may say the posting is still open.

Same shape as assembly: the draft is deterministic, built from the store's own
words, and a model gets exactly one sentence - the personal opener, from the
context line the person writes about the recipient. No context, no model call.
Everything is checked, and "No AI involved" is a complete answer.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import yaml

from .assemble import Variant, emittable, entry_relevance, load_library, load_store
from .grounding import URL as _URL, numbers_grounded
from .llm import LLM
from .models import utcnow
from .profile import Profile
from .registry import open_escalations
from .skills import BANNED, INCIDENTAL, SkillIndex, load_index

# ex-colleague: they worked where the person works or worked (identity.employment).
RELATIONSHIPS = ("warm", "ex-colleague", "alumni", "cold")
STATUSES = ("drafted", "sent", "followed-up", "replied", "referred", "declined", "closed")
FINISHED = {"replied", "referred", "declined", "closed"}

FOLLOW_UP_AFTER_DAYS = 5     # one nudge, after a working week
GIVE_UP_AFTER_DAYS = 7       # after the nudge; then close it and move on

# 200 fits every LinkedIn tier's connection-note limit. Shorter always fits;
# a note cut off mid-sentence by the site is worse than no note.
NOTE_LIMIT = 200
MESSAGE_WORDS = 150
BLURB_WORDS = 70


class OutreachError(Exception):
    """A precondition a human has to fix - never a crash."""


# ── small io ─────────────────────────────────────────────────────────────────

def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "x"


def _squash(s: Any) -> str:
    """Whitespace-normalised, with the typographic characters models emit
    (narrow no-break space, non-breaking hyphen) mapped to plain ones - the
    ranker's stored output already carries U+202F and U+2011."""
    t = str(s or "")
    for a, b in (("\u202f", " "), ("\u00a0", " "), ("\u2011", "-"), ("\u2010", "-"),
                 ("\u2019", "'"), ("\u2018", "'")):
        t = t.replace(a, b)
    return re.sub(r"[ \t]+", " ", t).strip()


# ── the posting, the company, the variant ────────────────────────────────────

def load_postings(prof: Profile) -> dict[str, dict[str, Any]]:
    return (_read_json(prof.var / "triage-results.json", {}) or {}).get("postings") or {}


def find_posting(prof: Profile, ref: str) -> dict[str, Any]:
    """By id, or by a unique prefix - Ashby and Lever ids are 36-char UUIDs."""
    postings = load_postings(prof)
    if ref in postings:
        return postings[ref]
    hits = [p for pid, p in postings.items() if pid.startswith(ref)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise OutreachError(f"no triaged posting with id {ref!r}")
    raise OutreachError(f"{ref!r} matches {len(hits)} postings - give more of the id")


_NAMES: dict[str, tuple[float, dict[str, Any]]] = {}


def _company_names(path: Path) -> dict[str, Any]:
    """Parsed once per change of the file. A postings list called this once
    per row and spent 11 of its 12 seconds re-reading the same YAML."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    hit = _NAMES.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    names = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    _NAMES[str(path)] = (mtime, names)
    return names


def company_name(prof: Profile, board: str, posting: dict[str, Any]) -> tuple[str, bool]:
    """(name as written in a sentence, whether a human confirmed it).

    Unconfirmed names are still returned, but the draft carries a warning, so a
    guessed "Rubrik Job Board" is seen before it is sent.
    """
    names = _company_names(prof.data / "company-names.yaml")
    if board in names:
        return str(names[board]).strip(), True

    b = (_read_json(prof.shared / "boards.json", {}) or {}).get(board) or {}
    slug = str(b.get("slug") or board.split(":")[-1])
    company = _squash(b.get("company") or posting.get("company") or "")
    if company and company.lower() != slug.lower():
        return company, True               # a real name recorded at discovery
    board_name = re.sub(r"\s+(careers|job board|jobs)$", "",
                        _squash(b.get("board_name")), flags=re.I)
    if board_name:
        return board_name, False
    return (company or slug).title(), False


def variant_for(prof: Profile, posting_id: str) -> Variant | None:
    """The variant whose `sent_to` names this posting - the resume the referrer
    will actually forward. The most recent wins if several do."""
    best: tuple[str, Variant] | None = None
    for v in load_library(prof):
        for s in v.sent_to:
            if str(s.get("posting_id")) == str(posting_id):
                if best is None or str(s.get("date")) >= best[0]:
                    best = (str(s.get("date")), v)
    return best[1] if best else None


def posting_live(prof: Profile, board: str, posting_id: str) -> bool | None:
    """True/False from the watcher's last poll; None when the watcher does not
    track this board. A follow-up may only say "still open" on True."""
    if ":" not in board:
        return None
    provider, slug = board.split(":", 1)
    seen = _read_json(prof.shared / "seen" / f"{provider}__{slug}.json", None)
    if not seen:
        return None
    return str(posting_id) in {str(i) for i in seen.get("ids") or []}


def identity_in_doubt(prof: Profile, board: str) -> bool:
    if ":" not in board:
        return False
    provider, slug = board.split(":", 1)
    return any(r.get("provider") == provider and r.get("slug") == slug
               for r in open_escalations(prof.shared / "escalations.jsonl"))


# ── find: who to ask ─────────────────────────────────────────────────────────

_SENIORITY = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|junior|jr\.?|associate|intern|"
    r"i{1,3}|iv|v|[1-5])\b", re.I)


def places_of(prof: Profile, store: dict[str, Any]) -> list[str]:
    """The cities worth naming in a search: the person's preferred and
    acceptable places, less their country ("India" in "Pune, India") - a
    search for peers "in India" narrows nothing."""
    from .triage import load_profile
    loc = (load_profile(prof.dir).get("location") or {}) if prof.file("search-profile.yaml").exists() else {}
    home = str((store.get("identity") or {}).get("location") or "")
    country = home.rsplit(",", 1)[-1].strip().lower() if "," in home else ""
    return [str(p) for p in (loc.get("preferred") or []) + (loc.get("acceptable") or [])
            if str(p).strip().lower() != country]


def _city(location: str | None, places: list[str]) -> str:
    loc = str(location or "").lower()
    return next((c for c in places if re.search(rf"(?<![\w]){re.escape(c.lower())}(?![\w])", loc)), "")


def team_of(role: str) -> str:
    """'Senior Backend Engineer' -> 'Backend Engineer'. Peers, not the level."""
    head = re.split(r"\s[-–|,(]\s?", _squash(role))[0]
    return _squash(_SENIORITY.sub(" ", head)) or _squash(role)


def _college(store: dict[str, Any]) -> str:
    edu = [e for e in store.get("entries") or [] if e.get("type") == "education"]
    return str(edu[0].get("where") or "") if edu else ""


def _li(query: str) -> str:
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(query)


def employers_of(store: dict[str, Any]) -> list[dict[str, Any]]:
    """identity.employment, current first."""
    jobs = list((store.get("identity") or {}).get("employment") or [])
    return sorted(jobs, key=lambda j: str(j.get("end") or "present").lower() != "present")


def find_leads(prof: Profile, posting: dict[str, Any]) -> dict[str, Any]:
    """Search links to open by hand, strongest connection first."""
    store = load_store(prof)
    board = str(posting.get("board") or "")
    company, _ = company_name(prof, board, posting)
    role = _squash(posting.get("title"))
    team, college = team_of(role), _college(store)
    city = _city(posting.get("location"), places_of(prof, store))

    leads = []
    for job in employers_of(store):
        employer = _squash(job.get("employer"))
        if not employer or employer.lower() == company.lower():
            continue
        leads.append(
            {"who": f"People from {employer} now at {company}",
             "why": "A shared employer is the warmest cold contact there is. They know "
                    f"what the work at {employer} involves, and are the likeliest to say yes.",
             "how": "ex-colleague",
             "employer": employer,
             "url": _li(f'"{company}" "{employer}"')})
    if college:
        leads.append(
            {"who": f"{college} alumni at {company}",
             "why": "A shared college is a real reason to write, and an easy one "
                    "to reply to.",
             "how": "alumni",
             "url": _li(f'"{company}" "{college}"')})
    leads += [
        {"who": f"{team}s at {company}" + (f" in {city}" if city else ""),
         "why": "Peers on the team know whether the role is still open and who "
                "is hiring. Ask about the team first; the referral follows.",
         "how": "cold",
         "url": _li(" ".join(x for x in (f'"{company}"', team, city) if x))},
        {"who": f"Engineering managers at {company}",
         "why": "Possibly the hiring manager. Do not ask a stranger in this seat "
                "for a referral - ask one question about the team.",
         "how": "cold",
         "url": _li(" ".join(x for x in (f'"{company}"', "engineering manager", city) if x))},
    ]

    asked = [d for d in load_ledger(prof).values()
             if d.get("company") == company and d.get("status") != "drafted"]
    return {"company": company, "role": role, "url": posting.get("url"),
            "posting_id": posting.get("id"), "leads": leads,
            "already_asked": [{"to": d["to"], "status": d["status"],
                               "role": d["role"]} for d in asked]}


# ── draft: the store's own words, in first and third person ──────────────────

_VERB_START = {"built", "set", "led", "ran", "won", "wrote", "made", "shipped"}


def _is_verb_phrase(headline: str) -> bool:
    first = headline.split()[0].lower() if headline.split() else ""
    return first.endswith("ed") or first in _VERB_START


def _lower_first(s: str) -> str:
    """Lowercase the first letter unless the word is an acronym ("IBM's")."""
    if len(s) > 1 and (s[1].islower() or s[1] == "-"):
        return s[0].lower() + s[1:]
    return s


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


# "Backend engineer on Acme Track, ..." - a role noun, then where. The
# message has already given the role, so repeating it read as "I'm a
# Backend Software Engineer ... I'm a backend engineer on Acme Track".
# Dropping the role word and keeping the rest adds nothing to the fact.
_ROLE_ON = re.compile(
    r"^(?:[\w-]+\s){0,3}?(?:engineer|developer|lead|member|contributor)\s+"
    r"(on|at|for|in)\s+(.+)$", re.I)


def first_person(headline: str, also: bool = False) -> str:
    h = _squash(headline).rstrip(".")
    a = "also " if also else ""
    if _is_verb_phrase(h):
        return f"I {a}{_lower_first(h)}"
    m = _ROLE_ON.match(h)
    if m:
        return f"I {a}work {m.group(1).lower()} {m.group(2)}"
    body = _lower_first(h)
    return f"I'm {a}{_article(body)} {body}"


def third_person(first: str, headline: str, also: bool = False) -> str:
    """By name, never by pronoun: nothing in the store says which pronoun to
    use, and a blurb that guesses is a blurb the person has to edit."""
    h = _squash(headline).rstrip(".")
    a = "also " if also else ""
    if _is_verb_phrase(h):
        return f"{first} {a}{_lower_first(h)}"
    m = _ROLE_ON.match(h)
    if m:
        return f"{first} {a}works {m.group(1).lower()} {m.group(2)}"
    body = _lower_first(h)
    return f"{first} is {a}{_article(body)} {body}"


def pick_entries(store: dict[str, Any], index: SkillIndex, variant: Variant,
                 posting: dict[str, Any], n: int = 3) -> list[dict[str, Any]]:
    """The entries most worth a sentence, drawn only from what the variant emits."""
    wanted = {index.canonical(k) or k for k in (posting.get("matched") or {})}
    emits = set(variant.emits)
    pool = [e for e in store.get("entries") or []
            if e.get("id") in emits and emittable(e)
            and e.get("type") in ("work", "project") and e.get("priority") != "low"]
    pool.sort(key=lambda e: -entry_relevance(e, index, wanted, variant.angle))

    # The newest role leads, whatever its keyword overlap - the same lesson as
    # select_entries. On one backend posting relevance put an older workstream
    # first, and the draft said "I also built..." about the most senior work,
    # demoted to an afterthought.
    from .render import chronological
    newest = chronological([e for e in pool if e.get("type") == "work"])[:1]
    if newest:
        pool = newest + [e for e in pool if e is not newest[0]]
    return pool[:n]


def _measured(e: dict[str, Any]) -> list[str]:
    return [str(m["claim"]) for m in e.get("metrics") or [] if m.get("measured")]


def facts_for(entries: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for e in entries:
        out.append(str(e.get("headline") or ""))
        out += [str(f) for f in (e.get("facts") or [])[:4]]
        out += _measured(e)
    return [f for f in out if f]


@dataclass
class Context:
    """Everything a draft is built from and checked against."""
    me_full: str
    me_first: str
    to_first: str
    how: str
    company: str
    role: str
    url: str
    descriptor: str
    band: str
    college: str
    context: str
    facts: list[str]
    name_confirmed: bool = True
    identity_doubt: bool = False
    live: bool | None = None
    open_ask_elsewhere: str = ""
    employer: str = ""        # where the person works now; "" between jobs
    city: str = ""            # where the person is, from identity.location
    shared: str = ""          # for "ex-colleague": the employer they share
    shared_current: bool = False
    to_rest: str = ""         # the rest of their name, kept from the model too


def _role(ctx: Context) -> str:
    """"a Backend AI Engineer" - the descriptor this resume uses, else the title."""
    r = ctx.descriptor or ctx.band or "software engineer"
    return f"{_article(r)} {r}"


def _here(ctx: Context) -> str:
    """"a Backend AI Engineer at Acme Freight in Pune" - all from the store."""
    return " ".join([_role(ctx)] + ([f"at {ctx.employer}"] if ctx.employer else [])
                    + ([f"in {ctx.city}"] if ctx.city else []))


def template(ctx: Context, entries: list[dict[str, Any]],
             personal: str | None = None) -> dict[str, Any]:
    """The deterministic draft. Always exists, and must pass its own checks.

    `personal` is the one model-written sentence, from the person's context
    line. It replaces the stock greeting for a warm contact and precedes the
    introduction for anyone else - a contact with shared context is no longer
    cold, so "I saw you were at Acme" gives way to what the context says.
    """
    e1 = entries[0] if entries else None
    e2 = entries[1] if len(entries) > 1 else None

    why = []
    if e1:
        m = _measured(e1)
        why.append(first_person(e1["headline"]) + (f" ({m[0]})" if m else "") + ".")
    if e2:
        why.append(first_person(e2["headline"], also=True) + ".")

    here = _here(ctx)
    in_city = f" in {ctx.city}" if ctx.city else ""
    opener = {
        "warm": "Hope you're doing well!",
        "ex-colleague": (f"I saw you were at {ctx.shared} before {ctx.company}. "
                         + (f"I'm there now, as {_role(ctx)}{in_city}." if ctx.shared_current
                            else "I worked there too.")),
        "alumni": (f"I'm a fellow {ctx.college} graduate, now {here}."
                   if ctx.college else f"I'm {ctx.me_first}, {here}."),
        "cold": f"I'm {ctx.me_first}, {here}.",
    }[ctx.how]
    if personal:
        opener = personal if ctx.how == "warm" else f"{personal} I'm now {here}."

    message = "\n\n".join([
        f"Hi {ctx.to_first},",
        f"{opener} I'm applying for the {ctx.role} role at {ctx.company}:\n{ctx.url}",
        " ".join(why),
        "Would you be open to referring me? I can send my resume and a short "
        "blurb you can paste straight into the referral form. No worries at all "
        "if not, and thanks for reading.",
        ctx.me_first,
    ])

    note = None
    if ctx.how != "warm":
        intro = {
            "ex-colleague": (f"I'm {_role(ctx)} at {ctx.shared}, where you were too"
                             if ctx.shared_current else f"I worked at {ctx.shared} too"),
            "alumni": (f"fellow {ctx.college} grad here" if ctx.college else f"I'm {here}"),
            "cold": f"I'm {here}",
        }[ctx.how]
        for cand in (
            f"Hi {ctx.to_first}, {intro}. I'm applying for {ctx.role} at "
            f"{ctx.company} and would value a quick word about a referral. "
            f"Thanks, {ctx.me_first}",
            f"Hi {ctx.to_first}, I'm applying for {ctx.role} at {ctx.company} "
            f"and would value a quick word about a referral. Thanks, {ctx.me_first}",
            f"Hi {ctx.to_first}, I'm applying for a role at {ctx.company} and "
            f"would value a quick word. Thanks, {ctx.me_first}",
        ):
            if len(cand) <= NOTE_LIMIT:
                note = cand
                break

    title = (f"{ctx.band} ({ctx.descriptor})" if ctx.band and ctx.descriptor
             else ctx.band or ctx.descriptor or "software engineer")
    blurb = [" ".join([f"{ctx.me_full} is {_article(title)} {title}"]
                      + ([f"at {ctx.employer}"] if ctx.employer else [])
                      + ([f"in {ctx.city}"] if ctx.city else [])) + "."]
    if e1:
        m = _measured(e1)
        blurb.append(third_person(ctx.me_first, e1["headline"])
                     + (f" ({m[0]})" if m else "") + ".")
    if e2:
        blurb.append(third_person(ctx.me_first, e2["headline"], also=True) + ".")

    return {
        "note": note,
        "message": message,
        "subject": f"Referral request: {ctx.role} at {ctx.company}",
        "blurb": " ".join(blurb),
    }


def follow_up(d: dict[str, Any], live: bool | None) -> str:
    """One short nudge. Says "still open" only when the watcher says so."""
    if live is False:
        return ("(The posting has closed - don't send a nudge. Mark this closed, "
                "or ask whether the team has other openings.)")
    first = str(d.get("to") or "there").split()[0]
    me = str(d.get("me") or "").split()[0] if d.get("me") else ""
    status = "It's still open" if live else "Here's the link again"
    return (f"Hi {first}, just bumping this in case it got buried. {status}:\n"
            f"{d.get('url')}\n\nTotally fine if now isn't a good time. Thanks!\n{me}").rstrip()


# ── checks: one gate for the template and the model alike ────────────────────

_PLACEHOLDER = re.compile(
    r"\[[^\]\n]{1,40}\]|\{[^}\n]{1,40}\}|<[^>\n]{1,40}>|\bTBD\b|\bXXX?\b")

EASY_OUTS = ("no worries", "no pressure", "totally fine", "completely understand",
             "totally understand", "no problem if", "if you'd rather not",
             "if it's not a fit", "all good if")

# Implies a shared past that a cold or alumni contact does not have.
FAMILIARITY = ("great to reconnect", "as we discussed", "we worked together",
               "long time no", "good to catch up", "as promised",
               "following up on our", "nice chatting", "great chatting")

# Implies research into the recipient that nobody did.
FAKE_RESEARCH = ("your post", "your article", "your talk", "your blog",
                 "your recent", "your podcast", "congratulations on",
                 "congrats on", "i admire", "big fan of your")

CLICHES = ("hope this message finds you", "hope this email finds you",
           "hope this finds you", "passionate", "dream job", "rockstar", "ninja",
           "synergy", "results-driven", "proven track record", "i am writing to",
           "to whom it may concern", "esteemed", "leverage my")

_PRONOUNS = re.compile(r"\b(he|him|his|she|her|hers)\b", re.I)

# Words that upgrade a claim without changing a number. The first live model
# rewrite turned "architected ... as second-in-command to the principal
# architect" into "led the development of", and every other check passed it.
# Each of these must appear in a fact before a draft may use it.
INFLATION = ("led", "lead", "leading", "headed", "spearheaded", "owned", "managed",
             "directed", "founded", "co-founded", "oversaw", "drove", "pioneered",
             "responsible for", "in charge of", "single-handedly", "expert",
             "large-scale", "massive", "world-class", "cutting-edge",
             "state-of-the-art", "mission-critical", "industry-leading",
             "award-winning", "senior engineer", "tech lead")


def claims_grounded(text: str, ctx: "Context") -> tuple[bool, str]:
    t = _URL.sub(" ", text)
    for s in (ctx.role, ctx.company):
        if s:
            t = re.sub(re.escape(s), " ", t, flags=re.I)
    # The person's own title and approved descriptor are on record too: a
    # "Lead Android Engineer" saying so claims nothing new.
    blob = " . ".join(ctx.facts + [ctx.context, ctx.band, ctx.descriptor]).lower()
    for w in INFLATION:
        pat = rf"(?<![\w-]){re.escape(w)}(?![\w-])"
        if re.search(pat, t, re.I) and not re.search(pat, blob):
            return False, f"'{w}' - no fact says that"
    return True, "no claim stronger than the facts"


def first_person_voice(msg: str, ctx: "Context") -> tuple[bool, str]:
    """The message is from the person, so it says "I". The model's warm
    rewrite said "<name> is applying ... would you be open to referring
    <name>?" - about them, not as them - and nothing else caught it."""
    me = ctx.me_first
    body = "\n".join(l for l in msg.splitlines() if l.strip() not in (me, ctx.me_full))
    body = re.sub(rf"\b(I'm|I am|this is|it's)\s+{re.escape(me)}\b", " ", body, flags=re.I)
    if me and re.search(rf"\b{re.escape(me)}\b", body):
        return False, f"talks about {me} in the third person"
    if not re.search(r"\bI('m|'d|'ve| )", body):
        return False, "never says I"
    return True, "first person"


def skills_backed(text: str, ctx: Context, index: SkillIndex) -> tuple[bool, str]:
    """No technology named that the store does not back.

    The role title and company are removed first - naming MongoDB the employer
    is not claiming MongoDB the skill. A term the store's own facts use
    verbatim ("Confluence") is allowed; banned and incidental never are.
    """
    t = _URL.sub(" ", text)
    for s in (ctx.role, ctx.company):
        if s:
            t = re.sub(re.escape(s), " ", t, flags=re.I)
    in_facts = index.detect(" . ".join(ctx.facts))
    for canon in sorted(index.detect(t)):
        tier = index.tier(canon)
        if tier in (BANNED, INCIDENTAL):
            return False, f"names {canon} ({tier})"
        claim = index.claim(canon)
        if not (claim and claim.emittable) and canon not in in_facts:
            return False, f"names {canon}, which the store does not back"
    return True, "every technology named is backed"


def check(parts: dict[str, Any], ctx: Context, index: SkillIndex) -> list[dict[str, Any]]:
    """Every check a draft must pass. `fail` blocks; `warn` is shown, not blocking."""
    note = _squash(parts.get("note"))
    msg = _squash(parts.get("message"))
    subj = _squash(parts.get("subject"))
    blurb = _squash(parts.get("blurb"))
    everything = "\n".join([note, msg, subj, blurb])
    low = everything.lower()
    role_l = _squash(ctx.role).lower()
    out: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, level: str = "fail") -> None:
        out.append({"check": name, "ok": bool(ok), "detail": detail, "level": level})

    def words(s: str) -> int:
        return len(_URL.sub("", s).split())

    if ctx.how == "warm":
        add("note_length", len(note) <= NOTE_LIMIT,
            f"{len(note)}/{NOTE_LIMIT} chars" if note else "no note (already connected)")
    else:
        add("note_length", 0 < len(note) <= NOTE_LIMIT, f"{len(note)}/{NOTE_LIMIT} chars")
    add("message_length", 0 < words(msg) <= MESSAGE_WORDS, f"{words(msg)}/{MESSAGE_WORDS} words")
    add("blurb_length", 0 < words(blurb) <= BLURB_WORDS, f"{words(blurb)}/{BLURB_WORDS} words")
    add("greets_recipient", ctx.to_first.lower() in msg[:60].lower(),
        f"opens with {ctx.to_first}")
    co = ctx.company.lower()
    add("names_company",
        co in msg.lower() and co in subj.lower() and (not note or co in note.lower()),
        ctx.company + ("" if not note or co in note.lower() else " - missing from the note"))
    ok, why = first_person_voice(msg, ctx)
    add("first_person", ok, why)
    add("names_role", role_l in msg.lower() and role_l in subj.lower(), ctx.role)
    add("has_link", bool(ctx.url) and ctx.url in msg, ctx.url or "the posting has no link")
    add("easy_out", any(p in msg.lower() for p in EASY_OUTS), "the reader can say no")
    ok, why = numbers_grounded(everything, ctx.facts + [ctx.role, ctx.company, ctx.context,
                                                        ctx.band, ctx.descriptor])
    add("numbers_grounded", ok, why)
    ok, why = skills_backed(everything, ctx, index)
    add("skills_backed", ok, why)
    ok, why = claims_grounded(everything, ctx)
    add("claims_grounded", ok, why)
    ph = _PLACEHOLDER.findall(_URL.sub(" ", everything))
    add("no_placeholders", not ph, ", ".join(ph) if ph else "none left")
    # Shared-past phrasing is only invented when nothing says there is a past.
    strangers = ctx.how in ("cold", "alumni") and not ctx.context
    fam = [p for p in FAMILIARITY if p in low] if strangers else []
    fake = [p for p in FAKE_RESEARCH if p in low and p not in ctx.context.lower()]
    add("no_invented_familiarity", not (fam or fake),
        ", ".join(fam + fake) or "claims nothing about the recipient")
    cl = [p for p in CLICHES if p in low]
    add("no_cliches", not cl, ", ".join(cl) or "none")
    pr = _PRONOUNS.findall(blurb)
    add("blurb_uses_name", not pr and ctx.me_first.lower() in blurb.lower(),
        f"pronoun {pr[0]!r}" if pr else "by name")

    add("company_name_confirmed", ctx.name_confirmed,
        "confirmed" if ctx.name_confirmed else
        f"'{ctx.company}' was guessed - add it to data/company-names.yaml", "warn")
    add("company_identity", not ctx.identity_doubt,
        "no open escalation" if not ctx.identity_doubt else
        "this board has an open identity escalation - resolve it before sending", "warn")
    add("posting_open", ctx.live is not False,
        {True: "live on the last poll", None: "not tracked by the watcher",
         False: "closed on the last poll"}[ctx.live], "warn")
    add("one_ask_per_company", not ctx.open_ask_elsewhere,
        ctx.open_ask_elsewhere or "no other open ask here", "warn")
    return out


def passed(checks: list[dict[str, Any]]) -> bool:
    return all(c["ok"] for c in checks if c["level"] == "fail")


# ── the one job a model gets ─────────────────────────────────────────────────
#
# The first version let a model rewrite the whole draft. On one posting it
# passed every check then in place and was still worse than the template: it
# wrote the warm message about the person instead of as them ("would you be
# open to referring <name>?"), upgraded "architected" to "led the development
# of", and cut the company from the note. Paraphrasing the facts
# is the failure the store exists to end - the conclusion assembly reached for
# resume bullets, reached again one module later.
#
# So the model gets one sentence: the personal opener, written from the
# --context line the person gives. That is the only part a template cannot write.
# No context, no model call. Facts, the ask, the link and the blurb stay in
# the store's own words.

OPENER_SYSTEM = """You write the first sentence of a short message the candidate
sends to someone they already know, before asking them for a job referral.

You get the RELATIONSHIP and a CONTEXT line the candidate wrote about how they
know the recipient. Write ONE warm, plain sentence in the candidate's own voice
(first person) that reconnects, using only what CONTEXT says.

Rules - a sentence that breaks one is thrown away:
- Use only CONTEXT. Invent no shared memory, event or detail.
- Do not mention the job, the company, or the candidate's skills or work; the
  next sentence does that.
- No numbers unless CONTEXT has them.
- No flattery, no "hope this finds you well".
- Do not greet; the message already opens with "Hi {FIRST},".
- Under 25 words.

Return {"opener": "..."}"""


def strip_greeting(s: str) -> str:
    """The message already opens "Hi Sam,". The model's opener did too, and
    the draft read "Hi Sam, / Hey Sam, it was great ...". Cutting a leading
    greeting is safer than rejecting an otherwise good sentence."""
    t = re.sub(r"^(?:hi|hey|hello|dear)\b[^,.!\n]{0,30}[,.!]\s*", "", s, flags=re.I)
    return t[:1].upper() + t[1:] if t else t


def opener_ok(s: str, ctx: Context) -> tuple[bool, str]:
    """The opener's own gate, stricter than the draft's: it may carry nothing
    but CONTEXT, so it is grounded against CONTEXT alone."""
    if not s:
        return False, "empty"
    if len(s.split()) > 30:
        return False, "too long"
    if len(re.findall(r"[.!?](?:\s|$)", s)) > 2:
        return False, "more than one sentence"
    low = s.lower()
    # Echoing CONTEXT is not inventing: "before you moved to Globex" is fine
    # when the person's own line says so. The role is never the opener's job.
    ctx_low = ctx.context.lower()
    if (ctx.company.lower() in low and ctx.company.lower() not in ctx_low) \
            or _squash(ctx.role).lower() in low:
        return False, "mentions the job - the next sentence does that"
    ok, why = numbers_grounded(s, [ctx.context])
    if not ok:
        return False, why
    for group in (CLICHES, FAKE_RESEARCH):
        hit = [p for p in group if p in low and p not in ctx.context.lower()]
        if hit:
            return False, f"'{hit[0]}'"
    if ctx.me_first and re.search(rf"\b{re.escape(ctx.me_first)}\b", s):
        return False, "talks about the candidate in the third person"
    if _PLACEHOLDER.search(s):
        return False, "placeholder left in"
    return True, ""


def write_opener(llm: LLM, ctx: Context) -> tuple[str | None, str, str]:
    """(sentence, attribution, why rejected). Called only when there is context."""
    # The recipient's name stays on this machine: the model sees {FIRST}, and
    # nothing of the rest of their name.
    context = (re.sub(rf"\b{re.escape(ctx.to_first)}\b", "{FIRST}", ctx.context, flags=re.I)
               if ctx.to_first else ctx.context)
    for part in ctx.to_rest.split():
        if len(part.strip(".")) > 1:
            context = re.sub(rf"\b{re.escape(part)}\b", "", context, flags=re.I)
    context = _squash(context)
    ans = llm.ask_json(OPENER_SYSTEM, f"RELATIONSHIP: {ctx.how}\nCONTEXT: {context}",
                       max_tokens=600, temperature=0.4)
    if not ans.ok or not ans.data:
        errs = "; ".join(f"{f['provider']}: {f['error'][:70]}" for f in ans.failures[:2])
        return None, ans.attribution, "no model answered" + (f" ({errs})" if errs else "")
    s = _squash(re.sub(r"\{\s*first\s*\}", ctx.to_first,
                       str(ans.data.get("opener") or ""), flags=re.I))
    s = strip_greeting(s)
    ok, why = opener_ok(s, ctx)
    return (s, ans.attribution, "") if ok else (None, ans.attribution, f"{why}: {s!r}")


# ── the ledger ───────────────────────────────────────────────────────────────

def ledger_path(prof: Profile) -> Path:
    return prof.var / "outreach.json"


def load_ledger(prof: Profile) -> dict[str, dict[str, Any]]:
    return _read_json(ledger_path(prof), {}) or {}


def save_ledger(prof: Profile, ledger: dict[str, dict[str, Any]]) -> None:
    _write_json_atomic(ledger_path(prof), ledger)


@dataclass
class Draft:
    id: str
    posting_id: str
    board: str
    company: str
    role: str
    url: str
    to: str
    how: str
    context: str
    me: str
    variant: str
    note: str | None
    message: str
    subject: str
    blurb: str
    facts_used: list[str]
    checks: list[dict[str, Any]]
    by: str
    rejected: str = ""
    status: str = "drafted"
    created: str = field(default_factory=utcnow)
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def draft(prof: Profile, posting_ref: str, to: str, how: str, context: str = "",
          llm: LLM | None = None, use_model: bool = True, force: bool = False,
          employer: str = "") -> Draft:
    """`employer`, for "ex-colleague": the employer they share - one from
    identity.employment. Default: the current one, else the most recent."""
    to = _squash(to)
    if not to:
        raise OutreachError("a referral request goes to a person - pass their name")
    if how not in RELATIONSHIPS:
        raise OutreachError(f"relationship must be one of {', '.join(RELATIONSHIPS)}")

    posting = find_posting(prof, posting_ref)
    pid = str(posting.get("id"))
    variant = variant_for(prof, pid)
    if variant is None:
        raise OutreachError(
            f"no resume has been assembled for posting {pid}. Run "
            f"`jobsearch assemble --posting {pid}` first - a referral request "
            f"should not promise a resume that does not exist yet.")

    board = str(posting.get("board") or "")
    company, confirmed = company_name(prof, board, posting)
    role = _squash(posting.get("title"))
    draft_id = f"{_slug(company)}-{_slug(pid)[:10]}-{_slug(to)}"

    ledger = load_ledger(prof)
    prior = ledger.get(draft_id)
    if prior and prior.get("status") != "drafted" and not force:
        raise OutreachError(
            f"{draft_id} is already '{prior['status']}'. Redrafting would overwrite "
            f"the record of what was sent - pass --force if that is really wanted.")

    store = load_store(prof)
    index = load_index(prof)
    identity = store.get("identity") or {}
    me_full = str(identity.get("name") or "")
    jobs = employers_of(store)
    now = next((j for j in jobs if str(j.get("end") or "present").lower() == "present"), None)
    shared = next((j for j in jobs if _squash(j.get("employer")).lower() == _squash(employer).lower()),
                  None) if employer else (jobs[0] if jobs else None)
    if how == "ex-colleague" and shared is None:
        raise OutreachError(f"ex-colleague needs a shared employer: {employer!r} is not in "
                            f"identity.employment" if employer else
                            "ex-colleague needs a shared employer, and identity.employment is empty")
    entries = pick_entries(store, index, variant, posting)
    other = [d for d in ledger.values()
             if d.get("company") == company and d.get("id") != draft_id
             and d.get("status") in ("sent", "followed-up")]

    ctx = Context(
        me_full=me_full, me_first=me_full.split()[0] if me_full else "",
        to_first=to.split()[0], to_rest=" ".join(to.split()[1:]), how=how, company=company, role=role,
        url=str(posting.get("url") or ""), descriptor=variant.title_descriptor,
        band=str((identity.get("title") or {}).get("official") or ""),
        college=_college(store), context=_squash(context),
        facts=facts_for(entries), name_confirmed=confirmed,
        identity_doubt=identity_in_doubt(prof, board),
        live=posting_live(prof, board, pid),
        open_ask_elsewhere=(f"already asked {other[0]['to']} at {company} "
                            f"({other[0]['status']})" if other else ""),
        employer=_squash(now.get("employer")) if now else "",
        city=str(identity.get("location") or "").split(",")[0].strip(),
        shared=_squash(shared.get("employer")) if shared else "",
        shared_current=bool(shared) and shared is now,
    )

    personal, by, rejected = None, "No AI involved", ""
    if use_model and ctx.context:
        llm = llm or LLM(prof, purpose="outreach")
        if llm.available:
            personal, by_model, why = write_opener(llm, ctx)
            if personal:
                by = f"store facts; opener by {by_model}"
            else:
                rejected = f"{by_model}: {why}"

    parts = template(ctx, entries, personal)
    results = check(parts, ctx, index)
    if personal and not passed(results):
        # The opener passed its own gate but broke the whole draft. The
        # template alone always passes, so fall back to it rather than ship.
        rejected = f"{by}: " + "; ".join(
            f"{c['check']}: {c['detail']}" for c in results
            if not c["ok"] and c["level"] == "fail")
        parts, by = template(ctx, entries), "No AI involved"
        results = check(parts, ctx, index)

    d = Draft(
        id=draft_id, posting_id=pid, board=board, company=company, role=role,
        url=ctx.url, to=to, how=how, context=ctx.context, me=me_full,
        variant=variant.id, note=parts.get("note"), message=parts["message"],
        subject=parts["subject"], blurb=parts["blurb"], facts_used=ctx.facts,
        checks=results, by=by, rejected=rejected,
        history=(prior or {}).get("history", []) + [{"status": "drafted", "at": utcnow()}],
    )
    ledger[draft_id] = d.to_dict()
    save_ledger(prof, ledger)
    write_markdown(prof, d.to_dict())
    return d


def resolve_id(ledger: dict[str, Any], ref: str) -> str:
    hits = [k for k in ledger if k == ref] or [k for k in ledger if k.startswith(ref)]
    if len(hits) != 1:
        raise OutreachError(f"{'no' if not hits else len(hits)} draft(s) match {ref!r}")
    return hits[0]


def mark(prof: Profile, draft_ref: str, status: str, note: str = "") -> dict[str, Any]:
    """The person records what they did. The agent never moves a draft to `sent`."""
    if status not in STATUSES:
        raise OutreachError(f"status must be one of {', '.join(STATUSES)}")
    ledger = load_ledger(prof)
    d = ledger[resolve_id(ledger, draft_ref)]
    d["status"] = status
    d.setdefault("history", []).append(
        {"status": status, "at": utcnow(), **({"note": note} if note else {})})
    save_ledger(prof, ledger)
    write_markdown(prof, d)
    return d


def _days_since(iso: str | None, now: datetime) -> int | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t).days


def due(d: dict[str, Any], now: datetime | None = None) -> str | None:
    """What this ask needs from the person today, if anything."""
    now = now or datetime.now(timezone.utc)
    if d.get("status") == "drafted":
        return "review and send"
    days = _days_since((d.get("history") or [{}])[-1].get("at"), now)
    if days is None:
        return None
    if d.get("status") == "sent" and days >= FOLLOW_UP_AFTER_DAYS:
        return f"follow up ({days} days, no reply)"
    if d.get("status") == "followed-up" and days >= GIVE_UP_AFTER_DAYS:
        return f"close it ({days} days since the nudge)"
    return None


def write_markdown(prof: Profile, d: dict[str, Any]) -> Path:
    """A copy-paste file per draft, next to the ledger."""
    out = prof.var / "outreach" / f"{d['id']}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    note = d.get("note")
    lines = [
        f"# {d['role']} at {d['company']} -> {d['to']} ({d['how']})",
        "",
        f"status **{d['status']}** · written by {d['by']} · resume variant `{d['variant']}`",
        f"posting: {d['url']}",
        "",
    ]
    if note:
        lines += [f"## Connection note ({len(note)}/{NOTE_LIMIT} chars)", "", note, ""]
    lines += ["## Message", "", d["message"], "",
              "## Email subject", "", d["subject"], "",
              "## Blurb for their referral form", "", d["blurb"], "",
              "## Checks", ""]
    for c in d.get("checks") or []:
        tag = "ok  " if c["ok"] else ("FAIL" if c["level"] == "fail" else "warn")
        lines.append(f"- `{tag}` {c['check']} - {c['detail']}")
    if d.get("rejected"):
        lines += ["", f"_Model version rejected: {d['rejected']}_"]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
