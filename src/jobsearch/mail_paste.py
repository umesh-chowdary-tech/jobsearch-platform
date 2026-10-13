"""Job emails a person pastes in, and applications they add by hand: the way
into the application ledger (applications.py) for anyone whose inbox isn't
read - everyone on the hosted service.

A pasted email goes through the same rules as a synced one (mail_rules.py):
what kind of email it is, which company, which role. None of it goes to a
model. When the rules can't tell what it says, or which company it's from,
the person says - instead of a guess. The ledger keeps what it keeps for any
email: the subject line, the sender, and the sentence that decided it (180
characters). The rest of the text is never kept.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from . import applications as apps
from . import mail_rules as rules
from .busy import editing
from .mailbox import Mail
from .models import utcnow
from .profile import Profile

MAX_TEXT = 20_000

# What a person can say an email is, when the rules can't.
KINDS = {"received": "They got my application", "interview": "An interview or a test",
         "rejected": "A rejection", "offer": "An offer", "on_hold": "It's on hold",
         "cancelled": "They withdrew the role", "inbound": "A recruiter reached out"}
# An application added by hand: where it stands, as the email kind that says so.
KIND_OF = {"applied": "received", "interviewing": "interview", "offer": "offer", "rejected": "rejected",
           "on_hold": "on_hold", "cancelled": "cancelled", "approached": "inbound"}


class PasteError(ValueError):
    pass


def headers(text: str) -> dict[str, str]:
    """From, Subject and Date, as a forwarded email or a mail client's copy
    starts: "Name: value" lines near the top."""
    out: dict[str, str] = {}
    for line in text.splitlines()[:15]:
        name, sep, value = line.partition(":")
        key = name.strip().lower()
        if sep and key in ("from", "subject", "date", "sent"):
            out.setdefault("date" if key == "sent" else key, value.strip())
    return out


def _when(value: str) -> str:
    """An ISO time from a date the person typed (2026-10-12) or a mail
    client's ("Mon, 12 Oct 2026 10:15:00 +0530", "... at 10:15"); "" if
    neither."""
    s = (value or "").strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.fromisoformat(s).replace(hour=12, tzinfo=timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            return ""
    for v in (s, s.replace(" at ", " ")):
        try:
            d = parsedate_to_datetime(v)
        except (TypeError, ValueError, IndexError):
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).isoformat(timespec="seconds")
    return ""


def _save(prof: Profile, mail: Mail, v: rules.Verdict, f: rules.Facts) -> dict[str, Any] | None:
    """Record one email the way a sync does: the ledger, its links to tracked
    jobs, then the jobs' stages. None if it was already recorded."""
    with editing(prof.var):
        ledger = apps.load(prof)
        ch = apps.record(ledger, mail, v, f)
        if ch is None:
            return None
        apps.link_postings(prof, ledger)
        apps.save(prof, ledger)
        apps.sync_pipeline(prof, ledger)
        return ledger["apps"].get(ch.get("app", ""))


def paste(prof: Profile, text: str, subject: str = "", sender: str = "", date: str = "",
          company: str = "", role: str = "", kind: str = "") -> dict[str, Any]:
    """One pasted email. Returns {"app": ...} when it was recorded, or
    {"ask": "kind" | "company", ...} when the person has to say."""
    text = (text or "").strip()
    if not text:
        raise PasteError("paste the email's text first")
    if len(text) > MAX_TEXT:
        raise PasteError(f"that's longer than an email - {MAX_TEXT:,} characters at most")
    if kind and kind not in KINDS:
        raise PasteError(f"kind must be one of {', '.join(KINDS)}")
    h = headers(text)
    subject = (subject or h.get("subject", "")).strip()[:300]
    name, addr = parseaddr(sender or h.get("from", ""))
    when = _when(date) or _when(h.get("date", "")) or utcnow()
    digest = hashlib.sha256("\n".join((addr.lower(), subject, text)).encode("utf-8")).hexdigest()[:24]
    mail = Mail(uid=0, message_id=f"paste:{digest}", thread="", date=when, from_addr=addr.lower(),
                from_name=name, subject=subject, body=text)

    v = rules.classify(mail.from_addr, subject, text, prof)
    f = rules.facts(mail.from_addr, name, subject, text, prof)
    if company.strip():
        f.company, f.company_source = company.strip()[:120], "manual"
    if role.strip():
        f.role = role.strip()[:160]
    if kind:
        v = rules.Verdict(kind, "high", "you said so")
    found = {"subject": subject, "company": f.company, "role": f.role, "date": when[:10]}
    if v.kind not in apps.LEDGER_KINDS:
        return {"ask": "kind", **found}
    if not f.company:
        return {"ask": "company", **found}
    app = _save(prof, mail, v, f)
    if app is None:
        return {"duplicate": True, **found}
    return {"app": app, "evidence": v.evidence}


def add(prof: Profile, company: str, role: str = "", date: str = "", status: str = "applied") -> dict[str, Any]:
    """An application made somewhere SwitchKaro didn't see."""
    company = (company or "").strip()[:120]
    if not company:
        raise PasteError("name the company")
    if status not in KIND_OF:
        raise PasteError(f"status must be one of {', '.join(KIND_OF)}")
    mail = Mail(uid=0, message_id=f"added:{uuid.uuid4().hex[:16]}", thread="", date=_when(date) or utcnow(),
                from_addr="", from_name="", subject="added by you", body="")
    f = rules.Facts(company=company, company_source="manual", role=(role or "").strip()[:160])
    app = _save(prof, mail, rules.Verdict(KIND_OF[status], "high", "added by you"), f)
    if app is None:                          # a fresh id is never seen before
        raise PasteError("couldn't add it - try again")
    return {"app": app}
