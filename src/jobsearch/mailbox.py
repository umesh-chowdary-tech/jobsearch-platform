"""Reading a job-search inbox. Read-only by construction, not by intention.

  - All Mail is opened with EXAMINE (select readonly=True): the server itself
    refuses any change to flags or folders.
  - Every fetch is BODY.PEEK, which never sets \\Seen. The person's unread
    mail stays unread.
  - The only IMAP commands issued are LOGIN, LIST, SELECT (read-only), UID
    SEARCH, UID FETCH and LOGOUT. A test reads this file and fails on any other
    - STORE, COPY, MOVE, EXPUNGE, APPEND - and on any mail-sending import.

Credentials come from .env (GMAIL_USER, GMAIL_APP_PASSWORD - an app password,
16 letters, revocable on its own) and are never logged or returned. An inbox
is one person's: MAIL_PROFILE names the profile it belongs to, and no other
profile reads it. This is the local, single-inbox reader; the hosted service
will take forwarded mail instead (docs/ROADMAP.md, Phase 3).

Incremental: `var/<id>/mail/state.json` remembers the last UID read. The caller
saves it only after the results are persisted, so a crash re-reads mail
rather than losing it; the ledger's message-id set makes re-reading harmless.
"""
from __future__ import annotations

import email
import imaplib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any

from .profile import Profile
from .llm import load_dotenv

HOST = "imap.gmail.com"
BATCH = 200


class MailError(Exception):
    """Something a human must fix: missing or rejected credentials, no network."""


@dataclass
class Mail:
    uid: int
    message_id: str
    thread: str
    date: str
    from_addr: str
    from_name: str
    subject: str
    body: str = ""

    @property
    def domain(self) -> str:
        return self.from_addr.rsplit("@", 1)[-1]


def credentials(prof: Profile) -> tuple[str, str] | None:
    """The login for this profile's inbox - only for the profile MAIL_PROFILE
    names, so a second profile can never read the first one's mail."""
    load_dotenv(prof.env)
    if os.environ.get("MAIL_PROFILE", "").strip() != prof.id:
        return None
    user = os.environ.get("GMAIL_USER", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    return (user, pw) if user and pw else None


def configured(prof: Profile) -> bool:
    return credentials(prof) is not None


def _dec(s: str | None) -> str:
    if not s:
        return ""
    try:
        return str(make_header(decode_header(s)))
    except Exception:  # noqa: BLE001 - a bad header is not worth a failed sync
        return s


def text_of(msg: email.message.Message) -> str:
    """Plain text if there is any, else the HTML with its tags removed."""
    plain: list[str] = []
    html: list[str] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if str(part.get("Content-Disposition", "")).startswith("attachment"):
            continue
        try:
            payload = part.get_payload(decode=True)
            s = payload.decode(part.get_content_charset() or "utf-8", errors="replace") if payload else ""
        except Exception:  # noqa: BLE001
            s = ""
        if part.get_content_type() == "text/plain":
            plain.append(s)
        elif part.get_content_type() == "text/html":
            html.append(s)
    if any(p.strip() for p in plain):
        t = "\n".join(plain)
    else:
        t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", "\n".join(html), flags=re.S | re.I)
        # Keep link targets: LinkedIn's event type lives only in its URLs.
        t = re.sub(r"<a\b[^>]*href=\"([^\"]+)\"[^>]*>", r" \1 ", t, flags=re.I)
        t = re.sub(r"<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", t, flags=re.I)
        t = unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t\u00a0\u200b]+", " ", t)   # also no-break and zero-width spaces
    return re.sub(r"\n\s*\n+", "\n", t).strip()


# ── state ────────────────────────────────────────────────────────────────────

def state_path(prof: Profile) -> Path:
    return prof.var / "mail" / "state.json"


def load_state(prof: Profile) -> dict[str, Any]:
    p = state_path(prof)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(prof: Profile, state: dict[str, Any]) -> None:
    p = state_path(prof)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix="state.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, p)


# ── the mailbox ──────────────────────────────────────────────────────────────

class Inbox:
    """`with Inbox(prof) as box:` - All Mail, opened read-only."""

    def __init__(self, prof: Profile, timeout: float = 30.0) -> None:
        creds = credentials(prof)
        if not creds:
            raise MailError("GMAIL_USER / GMAIL_APP_PASSWORD are not set in .env, "
                            "or MAIL_PROFILE does not name this profile")
        self._creds = creds
        self.timeout = timeout
        self.m: imaplib.IMAP4_SSL | None = None
        self.uidvalidity = ""

    @property
    def user(self) -> str:
        return self._creds[0]

    def __enter__(self) -> "Inbox":
        try:
            self.m = imaplib.IMAP4_SSL(HOST, 993, timeout=self.timeout)
            self.m.login(*self._creds)
        except imaplib.IMAP4.error as exc:
            # The server's own words, e.g. [AUTHENTICATIONFAILED] - never the password.
            raise MailError(f"Gmail refused the login: {exc}") from None
        except OSError as exc:
            raise MailError(f"could not reach {HOST}: {type(exc).__name__}") from None
        # All Mail by its \All flag - the folder's name is localised.
        _, boxes = self.m.list()
        name = "[Gmail]/All Mail"
        for b in boxes or []:
            if b and rb"\All" in b:
                m = re.search(rb'"([^"]+)"\s*$', b)
                if m:
                    name = m.group(1).decode()
        typ, _ = self.m.select(f'"{name}"', readonly=True)
        if typ != "OK":
            raise MailError("could not open All Mail read-only")
        _, data = self.m.response("UIDVALIDITY")
        self.uidvalidity = (data[0].decode() if data and data[0] else "")
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.m is not None:
            try:
                self.m.logout()
            except Exception:  # noqa: BLE001
                pass

    def new_uids(self, state: dict[str, Any], first_days: int) -> list[int]:
        """UIDs after the last one read; on the first run (or if Gmail reset
        its numbering), everything from the last `first_days` days."""
        assert self.m is not None
        last = int(state.get("last_uid") or 0)
        if last and state.get("uidvalidity") == self.uidvalidity:
            _, data = self.m.uid("SEARCH", None, f"UID {last + 1}:*")
        else:
            _, data = self.m.uid("SEARCH", None, "X-GM-RAW", f'"newer_than:{int(first_days)}d"')
            last = 0
        # "UID n:*" always returns the highest UID, even when it is below n.
        return sorted(u for u in (int(x) for x in (data[0] or b"").split()) if u > last)

    def headers(self, uids: list[int]) -> list[Mail]:
        assert self.m is not None
        out: list[Mail] = []
        for i in range(0, len(uids), BATCH):
            batch = ",".join(str(u) for u in uids[i:i + BATCH])
            _, got = self.m.uid(
                "FETCH", batch,
                "(X-GM-THRID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
            for part in got or []:
                if not isinstance(part, tuple):
                    continue
                meta, raw = part
                msg = email.message_from_bytes(raw)
                uid = re.search(rb"UID (\d+)", meta)
                thread = re.search(rb"X-GM-THRID (\d+)", meta)
                name, addr = parseaddr(_dec(msg.get("From")))
                try:
                    when = parsedate_to_datetime(msg.get("Date")).isoformat()
                except Exception:  # noqa: BLE001
                    when = ""
                out.append(Mail(uid=int(uid.group(1)) if uid else 0,
                                message_id=(msg.get("Message-ID") or "").strip(),
                                thread=thread.group(1).decode() if thread else "",
                                date=when, from_addr=addr.lower(), from_name=name,
                                subject=" ".join(_dec(msg.get("Subject")).split())))
        return out

    def bodies(self, uids: list[int]) -> dict[int, str]:
        assert self.m is not None
        out: dict[int, str] = {}
        for i in range(0, len(uids), 50):
            batch = ",".join(str(u) for u in uids[i:i + 50])
            _, got = self.m.uid("FETCH", batch, "(BODY.PEEK[])")
            for part in got or []:
                if not isinstance(part, tuple):
                    continue
                meta, raw = part
                uid = re.search(rb"UID (\d+)", meta)
                if uid:
                    out[int(uid.group(1))] = text_of(email.message_from_bytes(raw))[:8000]
        return out

    def by_message_id(self, message_id: str) -> Mail | None:
        """One message, found by its Message-ID - the email a flag is about,
        read again to show the model. A SEARCH and two PEEKs, like the rest."""
        assert self.m is not None
        mid = (message_id or "").strip().strip("<>")
        if not mid or re.search(r'["\\\s]', mid):       # it goes inside a quoted IMAP string
            return None
        _, data = self.m.uid("SEARCH", None, "X-GM-RAW", f'"rfc822msgid:{mid}"')
        uids = [int(x) for x in (data[0] or b"").split()]
        heads = self.headers(uids[:1]) if uids else []
        if not heads:
            return None
        heads[0].body = self.bodies([heads[0].uid]).get(heads[0].uid, "")
        return heads[0]
