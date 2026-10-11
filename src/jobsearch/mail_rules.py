"""What a job email says: which application, and what happened to it.

Pure functions over (sender, subject, body) - no mailbox, no network. Built
against a read-only survey of one job-search inbox (two months, 880
messages), and every trap it found is a test:

  - One employer's fraud warning ("...in return for a job offer...") sits in
    the footer of every email it sends; unread, all 13 looked like offers. The
    body is cut at the first boilerplate marker before anything is matched.
  - Receipts from several employers say "If you're not selected ..."; unread,
    they looked like rejections. A match inside a sentence that opens with
    "if" is a condition, not an outcome.
  - One rejection is titled "Thank You for Applying". The body decides, never
    the subject alone.
  - LinkedIn's body often says nothing, but its links name the event
    (email_jobs_application_rejected_01). Structure beats wording.
  - Naukri's "You applied for 19 jobs" lists ONE of them; the rest of the
    email is "similar jobs for you". Naukri is counted, never ledgered.

No model is involved here. The survey's whole sample was decidable by rules;
what is not is marked low-confidence for a human to look at - and mail_ai.py
asks a model to suggest an answer, quoting the email. Rules the person
approves from those suggestions (mail_learn.py) join these through their
profile's mail-learned.yaml.

`where` is a Profile - the senders list is shared (data/mail-senders.yaml),
the learned rules are the person's (profiles/<id>/mail-learned.yaml) - or, in
tests, one folder that holds both files.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# ── who is writing ───────────────────────────────────────────────────────────


def _shared_dir(where: Any) -> str:
    return str(where.data) if hasattr(where, "data") else str(where)


def _person_dir(where: Any) -> str | None:
    if hasattr(where, "dir"):
        return str(where.dir)
    return str(where) if where else None


def senders(where: Any) -> dict[str, Any]:
    return _senders(_shared_dir(where))


@lru_cache(maxsize=4)
def _senders(data_dir: str) -> dict[str, Any]:
    p = Path(data_dir) / "mail-senders.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {"employers": {k.lower(): v for k, v in (doc.get("employers") or {}).items()},
            "platforms": {d.lower() for d in doc.get("platforms") or []},
            "feeds": {d.lower() for d in doc.get("feeds") or []}}


def learned(where: Any) -> dict[str, Any]:
    """Rules approved by hand from the model's suggestions (mail_learn.py):
    `phrases` [(kind, phrase)] and `senders` {address or domain: company}.
    Re-read when the file changes - an approval takes effect on the next email."""
    d = _person_dir(where)
    if not d:
        return {"phrases": [], "senders": {}}
    p = Path(d) / "mail-learned.yaml"
    return _learned(str(p), p.stat().st_mtime_ns if p.exists() else 0)


@lru_cache(maxsize=4)
def _learned(path: str, mtime: int) -> dict[str, Any]:
    doc = (yaml.safe_load(Path(path).read_text(encoding="utf-8")) if mtime else None) or {}
    return {"phrases": [(r["means"], r["phrase"]) for r in doc.get("phrases") or []],
            "senders": {str(r["sender"]).lower(): r["company"] for r in doc.get("senders") or []}}


def _domain_in(domain: str, domains: set[str] | dict) -> str | None:
    """Exact, or a parent: mail.us2.cloud.oracle.com is under a listed parent."""
    hit = _domain_depth(domain, domains)
    return hit[1] if hit else None


def _domain_depth(domain: str, domains: set[str] | dict) -> tuple[int, str] | None:
    """(how many labels were stripped, the listed domain). Lower is more specific."""
    parts = domain.lower().split(".")
    for i in range(len(parts) - 1):
        d = ".".join(parts[i:])
        if d in domains:
            return i, d
    return None


NOISE = re.compile(
    r"verif|\botp\b|one-time|security code|6-digit|confirm your identity|password|account creation|"
    r"welcome to|new job|job alert|jobs at|match your|this job is a match|might interest|opportunities at|"
    r"latest .{1,20} jobs|shared some google|security alert|top companies|unlock|weekly recap|interview-ready|"
    r"get up to|new device|impressions|coworkers|conversations in|talent community|signing up|"
    r"recruiting experience|career preferences|competing to hire|dream job|hot tip|continue to apply|"
    r"recruiters are noticing|jobs applied by|feedback on your naukri|apply now to|is hiring|"
    r"\bhiring\b.*\bnow\b|see who else is applying|"
    # Not submitted yet: a marketplace's "your application is almost there"
    # wants a profile on it first, and names no employer.
    r"almost there|finish your application|complete your application|"
    # A marketplace's matches, not an application: "Congratulations on your
    # first matches: 53 roles awaiting your response".
    r"your (?:first |new )?matches\b|roles? (?:are )?awaiting your|roles? matching your", re.I)


def is_candidate(from_addr: str, subject: str, data_dir: Any, me: str = "") -> bool:
    """Worth reading the body of. Cheap - headers only."""
    addr = from_addr.lower()
    if me and addr == me.lower():
        return False                       # the person's own sent mail
    domain = addr.rsplit("@", 1)[-1]
    s = senders(data_dir)
    if _domain_in(domain, s["feeds"]):
        return False
    if domain.endswith("linkedin.com"):
        return bool(re.search(r"your application (?:was sent )?to", subject, re.I))
    if domain.endswith("naukri.com"):
        return bool(re.search(r"you applied for \d+ jobs?|status of your job application|applied by neo", subject, re.I))
    return not NOISE.search(subject)


# ── what happened ────────────────────────────────────────────────────────────

FOOTER = re.compile(
    r"this (?:is an? )?(?:system|automatically)[ -]generated|please do not reply|do not reply to this|"
    r"this email was intended|unsubscribe|equal opportunity|demand money|never ask(?:s)? (?:you )?for money|"
    r"please do not pay any money|report a problem|©|all rights reserved", re.I)

# (kind, strong, weak). The first kind with any match wins; strong -> high
# confidence, weak -> low (a human looks). Order matters: a rejection that
# says "thank you for applying" is a rejection.
RULES: list[tuple[str, str, str]] = [
    ("cancelled",
     r"no longer recruiting for this role|position has been (?:cancel+ed|withdrawn)|"
     r"role (?:has been|is) (?:cancel+ed|withdrawn)|requisition (?:has been )?(?:cancel+ed|closed)",
     r"no longer (?:open|available)"),
    ("rejected",
     # "other applicants": one bank's wording; "other candidates" was not enough.
     r"not (?:to )?mov(?:e|ing) forward with (?:your|you)|"
     r"mov(?:e|ing) forward with other (?:candidates|applicants|profiles|individuals)|"
     r"(?:proceed|pursue) (?:with )?other (?:candidates|applicants)|regret to inform|(?:was|been|were) not selected|"
     r"no longer being considered|other candidates whose|decided not to (?:proceed|move forward)|"
     r"not (?:be )?progressing (?:with )?your|unable to offer you",
     r"\bunfortunately\b"),
    ("offer", r"pleased to (?:offer|extend)|offer letter|extend (?:you )?an offer", r""),
    ("on_hold", r"(?:is|been|put) on hold|been paused|temporarily paused", r""),
    ("interview",
     r"schedule (?:an |your )?interview|interview (?:has been )?(?:successfully )?scheduled|"
     r"looking forward to your interview|through to the next round|next round|take-?home|"
     r"l1 discussion|ai interview|invite you (?:to|for) (?:an? )?interview|coding (?:test|challenge|assessment)|"
     # One employer's online assessment and its "next stage" acknowledgement
     # came out unsure: only the weak "assessment" matched.
     r"(?:begin|start|take) your (?:online )?assessment|move you (?:forward )?to the next (?:stage|step|round)",
     r"\bassessment\b|availability|next steps"),
    ("received",
     r"received your (?:job )?application|thank you for (?:your )?appl(?:ying|ication)|"
     r"thanks for (?:your )?appl(?:ying|ication)|has your application|application (?:has been )?received|"
     r"your application was sent|we have received|journey at .{1,40} begins|"
     # An agency applied: that receipt otherwise matched only the weak
     # "next steps", and read as an interview.
     r"submitted (?:an |your )?application on your behalf",
     r""),
    ("inbound",
     r"came across your profile|has referred you|wants you to join|job opportunity with|"
     r"would you be interested|expressed interest in your",
     r""),
    ("portal", r"(?:log|sign) ?in to (?:view|see|check)|has been updated", r""),
]

# The footer of mail someone signed up for: a newsletter, a marketplace's
# "matches". One read as an interview on "next steps". It only decides
# an email no strong pattern did - a rejection that carries a subscription
# footer is still a rejection.
MARKETING = re.compile(
    r"(?:you(?:'|’)?re|you are) receiving this (?:email |message )?because you (?:opted in|signed up|subscribed)|"
    r"you opted in to (?:receive )?(?:job )?(?:updates|alerts|emails|newsletters)|"
    r"(?:manage|update) your (?:subscription|email|notification) preferences", re.I)

LINKEDIN_EVENT = re.compile(r"email_jobs_application_([a-z]+)_\d+")
LINKEDIN_KIND = {"rejected": "rejected", "sent": "received", "viewed": "viewed"}


@dataclass
class Verdict:
    kind: str                      # received, rejected, cancelled, interview, offer, on_hold,
                                   # portal, inbound, viewed, naukri_*, unclear
    confidence: str = "high"       # high | low
    evidence: str = ""             # the sentence that decided it, trimmed
    extra: dict[str, Any] = field(default_factory=dict)


def head(body: str) -> str:
    """The body above its first boilerplate line."""
    m = FOOTER.search(body or "")
    return body[:m.start()] if m else (body or "")


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def flat(s: str) -> str:
    """All whitespace to single spaces. Plain-text mail wraps lines mid-sentence
    and headers fold long subjects; either one hid an "If you're not selected"
    from the sentence check, and a folded subject lost its role."""
    return re.sub(r"\s+", " ", s or "").strip()


def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    left = max((m.end() for m in _SENTENCE_END.finditer(text, 0, start)), default=0)
    right = next((m.start() for m in _SENTENCE_END.finditer(text, end)), len(text))
    return left, right


def _conditional(before_in_sentence: str) -> bool:
    """"If you're not selected, you will receive an email..." describes what
    might happen - it is not what happened. Any "if"/"should" clause ahead of
    the match, within its sentence, makes it a condition."""
    return bool(re.search(r"\b(?:if|should|in case|whether)\b", before_in_sentence, re.I))


def naukri(subject: str, body: str) -> Verdict | None:
    m = re.search(r"you applied for (\d+) jobs? on (\d{1,2} \w{3})", subject, re.I)
    if m:
        # Only the first listing is an application; "Similar jobs for you"
        # follows it and must never be read as more applications.
        first = re.search(r"Applied on [^\n|]+[\n|]+\s*([^\n|]+?)\s*[\n|]+\s*([^\n|]+?)\s*[\n|]", body or "")
        return Verdict("naukri_applied", "high", m.group(0),
                       {"count": int(m.group(1)), "day": m.group(2),
                        "example": f"{first.group(1)} · {first.group(2)}" if first else ""})
    if re.search(r"status of your job application has changed", subject, re.I):
        return Verdict("naukri_status", "high", "status changed - details only on Naukri")
    if re.search(r"applied by neo", subject, re.I):
        flat = re.sub(r"\s+", " ", (body or "").replace("|", " "))

        def num(label: str) -> int:
            n = re.search(re.escape(label) + r".{0,60}?(\d+)", flat)
            return int(n.group(1)) if n else 0
        return Verdict("naukri_neo", "high", "Neo daily update",
                       {"auto_applied": num("Neo applied on your behalf"),
                        "assisted": num("You applied with Neo's help"),
                        "recruiter_actions": num("Recruiters acted on your applications")})
    return None


def _literal(phrase: str) -> str:
    """A learned phrase as a pattern: exact words, any spacing, and either
    apostrophe - mail writes "We’re" where a model copies "We're"."""
    out = re.escape(flat(phrase).strip()).replace("\\ ", r"\s+")
    return re.sub(r"['‘’]", lambda m: "['‘’]", out)


def classify(from_addr: str, subject: str, body: str, data_dir: Any = None,
             extra: tuple[tuple[str, str], ...] = ()) -> Verdict:
    """`data_dir` adds the learned phrases; `extra` adds a proposed one, to
    replay past mail with it before anyone approves it."""
    domain = from_addr.lower().rsplit("@", 1)[-1]
    if domain.endswith("naukri.com"):
        return naukri(subject, body) or Verdict("unclear", "low")
    if domain.endswith("linkedin.com"):
        m = LINKEDIN_EVENT.search(body or "")
        if m:
            return Verdict(LINKEDIN_KIND.get(m.group(1), m.group(1)), "high", f"LinkedIn: {m.group(0)}")
    text = flat(subject) + ". " + flat(head(body))
    # A learned phrase joins its kind's strong patterns, so it keeps the kinds'
    # order (a rejection still beats a receipt) and the if-clause guard.
    added: dict[str, list[str]] = {}
    for kind, phrase in [*learned(data_dir)["phrases"], *extra]:
        added.setdefault(kind, []).append(_literal(phrase))
    # Every strong pattern, in order, before any weak one. With the weak ones
    # interleaved, "assessment" in a receipt's small print out-ranked "thank
    # you for applying", and eight plain receipts came out as interviews.
    for level, confidence in ((1, "high"), (2, "low")):
        if level == 2:
            m = MARKETING.search(flat(body))
            if m:
                return Verdict("marketing", "high", flat(body)[m.start():m.end() + 60][:180])
        for rule in RULES:
            kind, pattern = rule[0], rule[level]
            if level == 1 and added.get(kind):
                pattern = "|".join([f"(?:{pattern})", *added[kind]])
            if not pattern:
                continue
            for m in re.finditer(pattern, text, re.I):
                left, right = _sentence_bounds(text, m.start(), m.end())
                if _conditional(text[left:m.start()]):
                    continue
                return Verdict(kind, confidence, text[left:right][:180])
    return Verdict("unclear", "low")


# ── which application ────────────────────────────────────────────────────────

REQ_ID = re.compile(
    r"\b(R-\d{5,7}|STAFF\d{4,7}|JR-?\d{4,7}|REQ-?\d{4,8})\b"
    r"|\bJob (?:number|Number|ID|Id|No\.?)\s*[:#]?\s*(\d{5,10})\b", re.I)

_NAME_NOISE = re.compile(
    r"\b(?:careers?|recruit(?:ing|ment)(?: team)?|talent(?: acquisition)?(?: team| te)?|hiring team|"
    r"human resources|people services|global talent acquisition|team|hr|india|pvt\.? ltd\.?)\b.*$", re.I)


@dataclass
class Facts:
    company: str = ""
    company_source: str = ""       # sender | learned | subject | body | tenant | org | display | ""
    role: str = ""
    req_id: str = ""


# Some employers put the number after the role: "position of Software Engineer
# - II, 200012345 at Globex". Only trusted in that exact shape.
_TRAILING_ID = re.compile(r"position of [^.]{3,90}?,\s*(\d{6,10})\s+at\b", re.I)


def req_id(text: str) -> str:
    text = flat(text)
    m = REQ_ID.search(text)
    if m:
        return (m.group(1) or m.group(2) or "").upper()
    m = _TRAILING_ID.search(text)
    return m.group(1) if m else ""


_ROLE_PATTERNS = [
    r"Application Received for (?P<r>.+?) at ",
    r"Application for (?P<r>.+?) received",
    r"Your application to (?P<r>.+?) at ",
    r"application for (?P<r>.+?) at ",
    # "...interest in Initech || Senior Backend Engineer". Only after an
    # application phrase: a recruiter's "... | Work Location: Pune |
    # Node+React" is not a role.
    r"(?:interest in|applying to|application to) [^|]+?\|\|? (?P<r>[^|]+)$",
    r"for STAFF\d+ - (?P<r>.+)$",
    r"to [A-Z][\w&.' ]+? - (?P<r>.+)$",
    r"Job Application for (?P<r>.+)$",
    r"begins for (?P<r>.+)$",
    r"application for (?P<r>[^,!.]+?)(?: position| role)?$",
    r"application to (?:the )?(?P<r>.+?) role",
    r"to (?P<r>AI Engineer|[A-Z][\w /&+-]+?)\s+at ",
]
# Where a role name ends. Without it, "the role of AI / ML Engineer has been
# successfully scheduled" became the role.
_ROLE_END = r"(?=\s+(?:at|has|have|is|was|with|for|in)\b|[,.;:!]|$)"
_BODY_ROLE = [
    r"the (?:role|position) of (?P<r>[A-Z][\w /&()+.-]{2,80}?)" + _ROLE_END,
    # A comma is safe here - "position" marks the end: "the Platform Analyst,
    # Bangalore position" lost its role without it, and two receipts from one
    # company merged.
    r"(?:in|for|to) (?:the|our) (?P<r>[A-Z][\w /&()+.,-]{2,80}?) (?:position|role|opening)\b",
    # "Thank you for applying to the Senior Staff Software Engineer (SDET)."
    r"applying (?:to|for) the (?P<r>[A-Z][\w /&()+.-]{2,80}?)" + _ROLE_END,
    r"application for (?P<r>[A-Z][\w /&()+.-]{2,80}?)" + _ROLE_END,
    r"interview for (?:the )?(?P<r>[A-Z][\w /&()+.-]{2,80}?)" + _ROLE_END,
]


def _clean_role(r: str) -> str:
    """Without the requisition id: "R-123456 Software Engineer - AI Engineer"
    and "Software Engineer - II, 200012345" are roles with an id attached. And
    without a trailing "with <Company>" ("Software Engineer with Globex Corp")."""
    r = re.sub(r"\(?\s*\bjob (?:number|id|no\.?)\b.*$", "", r, flags=re.I)
    r = REQ_ID.sub("", r)
    r = re.sub(r",?\s*\d{6,10}\s*$", "", r)
    r = re.sub(r"\s+(?:with|at)\s+[A-Z][\w&.,' ]*$", "", r)
    return r.strip(" -!.,")


def role(subject: str, body: str) -> str:
    subject = flat(subject)
    for pat in _ROLE_PATTERNS:
        m = re.search(pat, subject)
        if m:
            return _clean_role(m.group("r"))
    text = flat(head(body))
    for pat in _BODY_ROLE:
        m = re.search(pat, text)
        if m:
            return _clean_role(m.group("r"))
    return ""


_SUBJECT_COMPANY = [
    # "AI Interview with InterviewBot(On Behalf of Globex)": the interviewing
    # platform is not the employer.
    r"[Oo]n [Bb]ehalf of (?P<c>[A-Z][\w&.' ]{1,40}?)\)?$",
    # "...application to AI Engineer at Globex": the role comes first, so the
    # company is what follows "at". Checked before the generic pattern, which
    # took "AI Engineer at Globex" for a company.
    r"(?:applying|application) to .+? at (?P<c>[A-Z][\w&.' ]{1,40}?)(?:[!.]|$| -| \|)",
    r"(?:applying|application) to (?:the .*? role at )?(?P<c>[A-Z][\w&.,' -]{1,40}?)(?:[!.]|$| -| \|)",
    r"your application was sent to (?P<c>[A-Z][\w&.' -]{1,40})$",
    r"Your application to .+? at (?P<c>[A-Z][\w&.' ]{1,40})",
    r"interest in (?P<c>[A-Z][\w&.' -]{1,30}?)(?:[!|.]|$| \||, )",
    r"journey at (?P<c>[A-Z][\w&.' ]{1,30}?) begins",
    r"Update on your (?P<c>[A-Z][\w&.' ]{1,30}?) Job Application",
    r"^(?P<c>[A-Z][\w&.' ]{1,30}?) has your application",
    r"with (?P<c>[A-Z][\w&.' ]{1,30}?) for ",
    r"[Oo]pportunity with \"?(?P<c>[A-Z][\w&' ]{1,40}?)(?:\"|\.|\(| Pvt| -|$)",
    r"at (?P<c>[A-Z][\w&.' ]{1,40}?)(?: Digital| Technologies| -|$)",
    r"on your (?P<c>[A-Z][\w&.' ]{1,30}?) (?:Interview|Application)",
]


# Workday's "<tenant>@myworkday.com" and Greenhouse's no-reply sender name no
# one; the receipt does: "a career with Initech", "applying to Globex!".
_BODY_COMPANY = [
    r"\b(?:applying|application) to (?P<c>[A-Z][\w&.']*(?: [A-Z][\w&.']*){0,3})[!.]",
    r"\ba career (?:with|at) (?P<c>[A-Z][\w&.']*(?: [A-Z][\w&.']*){0,3})(?= and\b|[,.!])",
]
# "applying to Senior Engineer." names a job, not an employer.
_JOB_WORDS = re.compile(r"\b(?:engineer|developer|manager|analyst|architect|scientist|lead|intern|"
                        r"specialist|consultant|designer|position|role|opening|team)\b", re.I)


def company(from_addr: str, from_name: str, subject: str, data_dir: Any,
            body: str = "") -> tuple[str, str]:
    domain = from_addr.lower().rsplit("@", 1)[-1]
    subject = flat(subject)
    s = senders(data_dir)
    # The most specific listing wins. Two employers wrote from
    # workflow.mail.us2.cloud.oracle.com - Oracle's hiring platform - and a
    # parent match on oracle.com made both of them "Oracle".
    taught = learned(data_dir)["senders"]
    if from_addr.lower() in taught:                      # a learned "<tenant>@myworkday.com"
        return str(taught[from_addr.lower()]), "learned"
    employers = {**s["employers"], **{k: v for k, v in taught.items() if "@" not in k}}
    emp, plat = _domain_depth(domain, employers), _domain_depth(domain, s["platforms"])
    if emp and not (plat and plat[0] < emp[0]):
        return str(employers[emp[1]]), ("learned" if emp[1] in taught else "sender")
    for pat in _SUBJECT_COMPANY:
        m = re.search(pat, subject)
        if m:
            return m.group("c").strip(" -.,"), "subject"
    raw = re.sub(r"^the recruiting team at\s+", "", from_name or "", flags=re.I)
    name = _NAME_NOISE.sub("", raw).strip(" -–|,")
    if name.endswith(".") and not re.search(r"\b(?:Co|Inc|Ltd|Corp|LLC|LLP)\.$", name):
        name = name[:-1].rstrip()
    # "Globex Corp Human Resources", "Initech Hiring Team": the organisation
    # words say this is a company. A recruiter's first name, or a staffing
    # agency's "Jane Doe", has none - a person, or a guess, and the
    # application is flagged for a look.
    if name and _NAME_NOISE.search(raw):
        return name, "org"
    # Before a bare name, what the email itself says. After an organisation's
    # name: one receipt says "Initech Technologies Pvt. Ltd.".
    text = flat(head(body))
    for pat in _BODY_COMPANY:
        m = re.search(pat, text)
        if m and not _JOB_WORDS.search(m.group("c")):
            c = re.sub(r"(?:\s+(?:Pvt|Private|Ltd|Limited|Inc|LLC|LLP)\b\.?)+$", "", m.group("c"))
            return c.strip(" -.,"), "body"
    if not name:
        return "", ""
    # A bare name, unless the address vouches for it: a Workday sender is
    # <tenant>@myworkday.com, and "Globex" writing as globex@ is the
    # company naming itself.
    if domain == "myworkday.com" and re.sub(r"[^a-z0-9]", "", name.lower()) == from_addr.lower().split("@")[0]:
        return name, "tenant"
    return name, "display"


def facts(from_addr: str, from_name: str, subject: str, body: str, data_dir: Any) -> Facts:
    c, src = company(from_addr, from_name, subject, data_dir, body)
    return Facts(company=c, company_source=src, role=role(subject, body),
                 req_id=req_id(f"{subject}\n{head(body)}"))
