"""Rules learned from what Groq read - each approved by hand, on what it
would change rather than on its wording.

Two kinds, neither of them code:
  phrase   exact wording that means an outcome: "submitted an application on
           your behalf" = received. It joins that outcome's strong patterns.
  sender   an address or domain that names the employer:
           globex@myworkday.com = Globex.

Before a proposal is shown, it is replayed: a phrase against the last 60 days
of mail, a sender against the ledger. The card lists exactly what would
change. Approving writes the rule to the profile's mail-learned.yaml, applies it to
those past records, and every email after is read with it. A rejected
proposal is remembered and never proposed again.

Refused before any proposal exists:
  - a phrase not in the email it came from; under 4 words or over 15; with a
    [placeholder], the company's or the role's name in it; one the rules
    already read as that outcome; or one whose replay changes nothing
  - a sender shared across companies: a platform's no-reply, a person at a job
    board or agency (a marketplace's recruiter, a staffing agency's), a free
    mail domain, or an address already seen writing for another company
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .profile import Profile
from . import applications as A
from . import mail_rules as R
from .busy import editing

TENANT_PLATFORMS = {"myworkday.com", "successfactors.eu", "successfactors.com"}
FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com", "yahoo.co.in",
             "rediffmail.com", "icloud.com", "proton.me", "protonmail.com"}
GENERIC = re.compile(r"^(?:no-?reply|do-?not-?reply|notifications?|mailer|info|jobs?|careers?|talent|"
                     r"recruit\w*|hr|team|hello|support|hiring)\b")
# What a sender-fact can overwrite: a guess. Never a name from the sender
# listing, a subject, an organisation's own name, or a hand correction.
GUESSED = {"", "display", "body", "tenant"}

HEADER = """\
# Rules learned from emails the built-in rules were unsure of. Each was
# suggested by Groq, replayed against past mail, and approved by hand in the
# app (Applications > Rules waiting for you). Edit or delete freely: the file
# is re-read when it changes. tests/test_mail_learned.py checks every entry
# still does what it was approved to do.
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(s: Any) -> str:
    s = str(s or "").translate(str.maketrans("‘’“”", "''\"\""))
    return re.sub(r"\s+", " ", s).strip(" .\"'").lower()


# ── storage ──────────────────────────────────────────────────────────────────

def proposals_path(prof: Profile) -> Path:
    return prof.var / "mail" / "proposals.json"


def learned_path(prof: Profile) -> Path:
    return prof.file("mail-learned.yaml")


def load_proposals(prof: Profile) -> dict[str, dict[str, Any]]:
    p = proposals_path(prof)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save_proposals(prof: Profile, props: dict[str, dict[str, Any]]) -> None:
    p = proposals_path(prof)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(props, indent=1, ensure_ascii=False), encoding="utf-8")


def _key(kind: str, value: str, means: str = "") -> str:
    return hashlib.sha1(f"{kind}|{_norm(value)}|{means}".encode(), usedforsecurity=False).hexdigest()[:10]


def pending(prof: Profile) -> list[dict[str, Any]]:
    return [p for p in load_proposals(prof).values() if p["status"] == "pending"]


# ── phrases ──────────────────────────────────────────────────────────────────

def check_phrase(phrase: str, means: str, texts: list[str], company: str, role: str,
                 already: str) -> str | None:
    """Why this phrase can't be proposed, or None. `already` is what the
    rules make of the email it came from."""
    words = phrase.split()
    if means not in A.STATUS_OF or means in ("viewed",):
        return f"“{means}” isn't an outcome"
    if not 4 <= len(words) <= 15:
        return "a phrase must be 4 to 15 words"
    if "[" in phrase:
        return "it contains a removed detail"
    if not any(_norm(phrase) in _norm(t) for t in texts):
        return "it isn't in the email"
    for name in (company, role):
        tokens = [t for t in re.findall(r"[a-z0-9]{3,}", _norm(name))]
        if tokens and any(re.search(rf"\b{re.escape(t)}\b", _norm(phrase)) for t in tokens):
            return "it names the company or role, so it wouldn't carry to other emails"
    if already == means:
        return "the rules already read that email this way"
    return None


def phrase_changes(mails: list[Any], data_dir: str, means: str, phrase: str,
                   ledger: dict[str, Any]) -> list[dict[str, Any]]:
    """Each past email the phrase would read differently. Pure - `mails`
    carry their bodies in memory only."""
    at = {e["message_id"]: a for a in ledger["apps"].values() for e in a["events"]}
    out = []
    for m in mails:
        before = R.classify(m.from_addr, m.subject, m.body, data_dir)
        after = R.classify(m.from_addr, m.subject, m.body, data_dir, extra=((means, phrase),))
        if (before.kind, before.confidence) == (after.kind, after.confidence):
            continue
        a = at.get(m.message_id)
        out.append({"message_id": m.message_id, "date": m.date[:10], "subject": m.subject[:100],
                    "app_id": a["id"] if a else "", "company": (a or {}).get("company") or m.domain,
                    "from": before.kind, "from_confidence": before.confidence,
                    "to": after.kind, "to_confidence": after.confidence, "evidence": after.evidence[:180]})
    return out


def replay_phrase(prof: Profile, means: str, phrase: str, days: int = 60) -> tuple[list[dict[str, Any]], int]:
    """Read the last `days` of mail again (read-only; bodies stay in memory)
    and list what the phrase would change. Returns (changes, emails read)."""
    from .mailbox import Inbox
    data_dir = prof
    with Inbox(prof) as box:
        heads = box.headers(box.new_uids({}, days))
        wanted = [m for m in heads if R.is_candidate(m.from_addr, m.subject, data_dir, me=box.user)]
        bodies = box.bodies([m.uid for m in wanted]) if wanted else {}
    for m in wanted:
        m.body = bodies.get(m.uid, "")
    return phrase_changes(wanted, data_dir, means, phrase, A.load(prof)), len(wanted)


# ── senders ──────────────────────────────────────────────────────────────────

def sender_key(address: str, company: str, data_dir: str) -> str | None:
    """The address or domain that can stand for this company, or None when
    the sender speaks for more than one."""
    local, _, domain = address.lower().partition("@")
    if domain in TENANT_PLATFORMS:                 # <tenant>@myworkday.com
        return None if GENERIC.match(local) else address.lower()
    s = R.senders(data_dir)
    if domain in FREE_MAIL or R._domain_in(domain, s["platforms"]) or R._domain_in(domain, s["feeds"]):
        return None
    # The company's own domain: its name and the domain agree (globex.com,
    # globexmail.com). An agency's domain (staffing-agency.example) does not.
    first = next(iter(re.findall(r"[a-z0-9]{4,}", _norm(company))), "")
    labels = [l for l in domain.split(".")[:-1] if len(l) >= 4]
    if first and any(l.startswith(first) or first.startswith(l) for l in labels):
        return domain
    return None


def _from(e: dict[str, Any], key: str) -> bool:
    sender = e.get("sender", "").lower()
    if "@" in key:
        return sender == key
    domain = sender.rsplit("@", 1)[-1]
    return domain == key or domain.endswith("." + key)


def sender_conflict(ledger: dict[str, Any], key: str, company: str) -> str | None:
    for a in ledger["apps"].values():
        if a.get("ignored") or a.get("company_source") in GUESSED:
            continue
        if any(_from(e, key) for e in a["events"]) and not A._same_company(a["company"], company):
            return f"{key} also wrote about {a['company']}"
    return None


def sender_changes(ledger: dict[str, Any], key: str, company: str) -> list[dict[str, Any]]:
    return [{"app_id": a["id"], "from": a.get("company") or "", "to": company,
             "subject": a["events"][0]["subject"][:100], "date": a["first_seen"]}
            for a in ledger["apps"].values()
            if not a.get("ignored") and a.get("company_source") in GUESSED
            and any(_from(e, key) for e in a["events"])
            and not A._same_company(a.get("company") or "", company)]


# ── proposing ────────────────────────────────────────────────────────────────

def _add(prof: Profile, prop: dict[str, Any]) -> bool:
    with editing(prof.var):
        props = load_proposals(prof)
        if prop["id"] in props:                    # pending, approved or rejected: asked once
            return False
        props[prop["id"]] = prop
        _save_proposals(prof, props)
    return True


def propose_from(prof: Profile, app: dict[str, Any], emails: list[tuple[dict[str, Any], str, str]],
                 data: dict[str, Any], suggestion: dict[str, Any], payload: dict[str, Any],
                 data_dir: str, say=print) -> int:
    """Turn one grounded answer into at most one phrase and one sender
    proposal. Returns how many were added."""
    made = 0
    origin = {"app_id": app["id"], "company": app.get("company") or "",
              "subject": emails[0][0]["subject"][:100], "date": emails[0][0]["date"][:10]}
    company = (suggestion.get("company") or {}).get("value") or app.get("company") or ""
    role = (suggestion.get("role") or {}).get("value") or app.get("role") or ""

    phrase, means = R.flat(str(data.get("phrase") or "")).strip(" ."), str(data.get("phrase_means") or "")
    if phrase:
        texts = [f"{e['subject']} {e['text']}" for e in payload["emails"]]
        # What the rules make of the email the phrase is in.
        # The payload may carry fewer emails than were read; pair what it has.
        holder = next(((e, body) for (e, _, body), t in zip(emails, texts, strict=False)
                       if _norm(phrase) in _norm(t)), None)
        already = R.classify(holder[0]["sender"], holder[0]["subject"], holder[1], data_dir).kind if holder else ""
        why = check_phrase(phrase, means, texts, company, role, already)
        pid = _key("phrase", phrase, means)
        if why:
            say(f"  phrase not proposed ({why}): {phrase}")
        elif pid not in load_proposals(prof):
            changes, read = replay_phrase(prof, means, phrase)
            if not changes:
                say(f"  phrase not proposed (changes nothing in {read} emails): {phrase}")
            else:
                example = next((t for t in texts if _norm(phrase) in _norm(t)), phrase)
                made += _add(prof, {"id": pid, "type": "phrase", "status": "pending", "phrase": phrase,
                                    "means": means, "example": _sentence(example, phrase),
                                    "learned_from": origin, "changes": changes, "replayed": read,
                                    "proposed_at": _now()})

    if suggestion.get("company") and emails:
        e, name, body = emails[0]
        current, source = R.company(e["sender"], name, e["subject"], data_dir, body)
        key = sender_key(e["sender"], company, data_dir)
        ledger = A.load(prof)
        # Only where the rules fail: no name, a bare display name, or a wrong one.
        if key and (source in ("", "display") or not A._same_company(current, company)):
            conflict = sender_conflict(ledger, key, company)
            if conflict:
                say(f"  sender not proposed ({conflict})")
            else:
                made += _add(prof, {"id": _key("sender", key), "type": "sender", "status": "pending",
                                    "sender": key, "company": company,
                                    "example": suggestion["company"]["quote"], "learned_from": origin,
                                    "changes": sender_changes(ledger, key, company), "proposed_at": _now()})
    return made


def _sentence(text: str, phrase: str) -> str:
    """The sentence around the phrase - the rule's test case."""
    t = R.flat(text)
    i = _norm(t).find(_norm(phrase))
    if i < 0:
        return phrase
    left, right = R._sentence_bounds(t, i, i + len(phrase))
    return t[left:right].strip()[:240]


# ── deciding ─────────────────────────────────────────────────────────────────

def _write_learned(prof: Profile, section: str, entry: dict[str, Any]) -> None:
    p = learned_path(prof)
    doc = (yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None) or {}
    doc.setdefault("phrases", [])
    doc.setdefault("senders", [])
    doc[section].append(entry)
    p.write_text(HEADER + yaml.safe_dump({"phrases": doc["phrases"], "senders": doc["senders"]},
                                         sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")


def approve(prof: Profile, pid: str) -> dict[str, Any]:
    with editing(prof.var):
        props = load_proposals(prof)
        p = props.get(pid)
        if not p or p["status"] != "pending":
            raise ValueError("no such proposal waiting")
        ledger = A.load(prof)
        if p["type"] == "phrase":
            _write_learned(prof, "phrases", {
                "phrase": p["phrase"], "means": p["means"], "example": p["example"],
                "learned_from": p["learned_from"], "approved_at": _now(),
                "replay": f"changed {len(p['changes'])} of {p['replayed']} emails"})
            for c in p["changes"]:
                a = ledger["apps"].get(c["app_id"])
                for e in (a or {}).get("events", []):
                    if e["message_id"] == c["message_id"] and c["to"] in A.STATUS_OF:
                        e.update(kind=c["to"], status=A.STATUS_OF[c["to"]], confidence=c["to_confidence"],
                                 evidence=c["evidence"], reread=f"learned rule {pid}")
                if a:
                    a["status"] = A.status_of(a["events"], a.get("confirmed_at", ""))
                    a["look"] = A.look_reasons(a)
        else:
            _write_learned(prof, "senders", {
                "sender": p["sender"], "company": p["company"], "example": p["example"],
                "learned_from": p["learned_from"], "approved_at": _now()})
            for c in p["changes"]:
                a = ledger["apps"].get(c["app_id"])
                if a and a.get("company_source") in GUESSED:
                    if a.get("company"):
                        a.setdefault("aliases", []).append(a["company"])
                    a["company"], a["company_source"] = p["company"], "learned"
                    a["look"] = A.look_reasons(a)
        A.save(prof, ledger)
        p.update(status="approved", decided_at=_now())
        _save_proposals(prof, props)
    return p


def reject(prof: Profile, pid: str) -> dict[str, Any]:
    with editing(prof.var):
        props = load_proposals(prof)
        p = props.get(pid)
        if not p or p["status"] != "pending":
            raise ValueError("no such proposal waiting")
        p.update(status="rejected", decided_at=_now())
        _save_proposals(prof, props)
    return p
