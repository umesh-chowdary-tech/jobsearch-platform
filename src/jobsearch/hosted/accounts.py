"""Who may use the service, and which profile is theirs.

var/_shared/accounts.json - mirrored to Postgres with the rest of var/:

  users:   {sign-in id: {"email", "profile", "invite", "joined", "consent",
                         "last_seen" (a day), "suspended" (when, if paused)}}
  invites: {code: {"created", "note", "used_by", "used_at"}}

A person signs in; their first request finds no user row, so they give an
invite code once and get a profile. The profile id comes from their sign-in
id, never their email, so no folder name or log line names anyone.

The service is one process (docs/hosted.md), so a thread lock and an atomic
replace keep the file whole.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from ..models import utcnow

FILE = "accounts.json"
# The privacy notice and terms a person agrees to when they join
# (web/src/legal/). Change it whenever their text changes in a way that matters.
CONSENT_VERSION = "2026-10-13.5-draft"
# No 0/O, 1/I/L: a code read out loud or off a phone is typed right first time.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_lock = threading.Lock()


class AccountError(Exception):
    pass


def profile_id(sub: str) -> str:
    return "u-" + hashlib.sha256(sub.encode("utf-8")).hexdigest()[:16]


def _path(shared: Path) -> Path:
    return shared / FILE


def load(shared: Path) -> dict[str, Any]:
    try:
        data = json.loads(_path(shared).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return {"users": data.get("users") or {}, "invites": data.get("invites") or {}}


def _save(shared: Path, data: dict[str, Any]) -> None:
    shared.mkdir(parents=True, exist_ok=True)
    tmp = _path(shared).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _path(shared))


def user(shared: Path, sub: str) -> dict[str, Any] | None:
    return load(shared)["users"].get(sub)


def _norm(code: str) -> str:
    c = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
    return f"{c[:4]}-{c[4:]}" if len(c) == 8 else c


def join(shared: Path, sub: str, email: str, code: str, consent: str, admin: bool = False) -> dict[str, Any]:
    """Use an invite, agreeing to the privacy notice and terms of
    CONSENT_VERSION - which version, and when, is kept with the account (the
    DPDP Act asks for consent that can be shown). Giving the same code again
    is harmless; a code someone else used is refused. An admin may join
    without one: invites are made on the admin page, so a new service's first
    admin would otherwise have no way in."""
    if consent != CONSENT_VERSION:
        raise AccountError("agree to the privacy notice and terms to join")
    code = _norm(code)
    with _lock:
        data = load(shared)
        if sub in data["users"]:
            return data["users"][sub]
        if not (admin and not code):
            inv = data["invites"].get(code)
            if inv is None:
                raise AccountError("that invite code isn't right - check it and try again")
            if inv.get("used_by") and inv["used_by"] != sub:
                raise AccountError("that invite code has already been used")
            inv.update(used_by=sub, used_at=utcnow())
        row = {"email": email, "profile": profile_id(sub), "invite": code or None, "joined": utcnow(),
               "consent": {"version": consent, "at": utcnow()}}
        data["users"][sub] = row
        _save(shared, data)
        return row


def consent_current(row: dict[str, Any] | None) -> bool:
    return ((row or {}).get("consent") or {}).get("version") == CONSENT_VERSION


def agree(shared: Path, sub: str, consent: str) -> dict[str, Any]:
    """Agree to a changed notice: the new version and time replace the old."""
    if consent != CONSENT_VERSION:
        raise AccountError("agree to the privacy notice and terms to continue")
    with _lock:
        data = load(shared)
        row = data["users"].get(sub)
        if row is None:
            raise AccountError("join with an invite code first")
        row["consent"] = {"version": consent, "at": utcnow()}
        _save(shared, data)
        return row


def create_invites(shared: Path, n: int, note: str = "") -> list[str]:
    if not 1 <= n <= 100:
        raise AccountError("make between 1 and 100 codes at a time")
    with _lock:
        data = load(shared)
        made = []
        while len(made) < n:
            raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
            code = f"{raw[:4]}-{raw[4:]}"
            if code in data["invites"]:
                continue
            data["invites"][code] = {"created": utcnow(), "note": note.strip()[:200], "used_by": None, "used_at": None}
            made.append(code)
        _save(shared, data)
        return made


def seen(shared: Path, sub: str) -> None:
    """The day a person last used the service - the day only, for the admin
    page's count of active people. Written at most once a day each."""
    today = utcnow()[:10]
    with _lock:
        data = load(shared)
        row = data["users"].get(sub)
        if row is None or row.get("last_seen") == today:
            return
        row["last_seen"] = today
        _save(shared, data)


def by_profile(shared: Path, pid: str) -> tuple[str, dict[str, Any]]:
    """(sign-in id, row) of the person whose profile this is. The admin page
    names people by profile id; the sign-in id never leaves the service."""
    for sub, row in load(shared)["users"].items():
        if row.get("profile") == pid:
            return sub, row
    raise AccountError("no such person")


def suspend(shared: Path, pid: str, on: bool) -> dict[str, Any]:
    """Pause an account, or let it back in. A paused person can still
    download or delete their data; nothing else is done for them."""
    with _lock:
        data = load(shared)
        for row in data["users"].values():
            if row.get("profile") == pid:
                if on:
                    row["suspended"] = utcnow()
                else:
                    row.pop("suspended", None)
                _save(shared, data)
                return row
    raise AccountError("no such person")


def revoke(shared: Path, code: str) -> None:
    """Cancel an invite nobody has used."""
    code = _norm(code)
    with _lock:
        data = load(shared)
        inv = data["invites"].get(code)
        if inv is None:
            raise AccountError("no such invite code")
        if inv.get("used_by"):
            raise AccountError("that code has been used - it can't be cancelled")
        del data["invites"][code]
        _save(shared, data)


def remove(shared: Path, sub: str) -> None:
    """Forget a person. Their invite stays spent, with no link back to them."""
    with _lock:
        data = load(shared)
        row = data["users"].pop(sub, None)
        if row and row.get("invite") in data["invites"]:
            data["invites"][row["invite"]]["used_by"] = "deleted"
        _save(shared, data)


def admins() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("JOBSEARCH_ADMINS", "").split(",") if e.strip()}
