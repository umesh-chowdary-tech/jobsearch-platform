"""Who may use the service, and which profile is theirs.

var/_shared/accounts.json - mirrored to Postgres with the rest of var/:

  users:   {sign-in id: {"email", "profile", "invite", "joined"}}
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


def join(shared: Path, sub: str, email: str, code: str) -> dict[str, Any]:
    """Use an invite. Giving the same code again is harmless; a code someone
    else used is refused."""
    code = _norm(code)
    with _lock:
        data = load(shared)
        if sub in data["users"]:
            return data["users"][sub]
        inv = data["invites"].get(code)
        if inv is None:
            raise AccountError("that invite code isn't right - check it and try again")
        if inv.get("used_by") and inv["used_by"] != sub:
            raise AccountError("that invite code has already been used")
        inv.update(used_by=sub, used_at=utcnow())
        row = {"email": email, "profile": profile_id(sub), "invite": code, "joined": utcnow()}
        data["users"][sub] = row
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
