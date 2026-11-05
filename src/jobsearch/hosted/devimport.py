"""Your own SwitchKaro data, on your own machine, for `hosted --dev`.

The service's Settings page downloads everything kept for one person as a zip
(app._export: `profile/...` and `data/...`). This unpacks it into the dev
service's layout, as the account dev sign-in gives that email - so signing in
locally with the same email opens the same jobs, decisions and resumes.

Only one person's zip, imported by that person: no one's machine holds anyone
else's data, and no one needs the database's password. Nothing here talks to
the service or the database; the dev service refuses to start with one set.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path, PurePosixPath

from ..profile import Profile
from . import accounts
from .accounts import CONSENT_VERSION
from .auth import dev_sub

PARTS = {"profile": "dir", "data": "var"}
MAX_FILES = 5000
MAX_BYTES = 200 * 1024 * 1024        # unpacked; a real export is a few MB


class DevImportError(Exception):
    """A message for a human."""


def _plan(z: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, str, PurePosixPath]]:
    """(entry, part, path inside it) for each file, or refuse the whole zip:
    nothing outside profile/ and data/, nothing that climbs out of them."""
    out, total = [], 0
    for info in z.infolist():
        if info.is_dir():
            continue
        name = info.filename
        p = PurePosixPath(name)
        if "\\" in name or p.is_absolute() or ".." in p.parts or ":" in name or len(p.parts) < 2:
            raise DevImportError(f"not a SwitchKaro export: {name!r}")
        if p.parts[0] not in PARTS:
            raise DevImportError(f"not a SwitchKaro export: {name!r} is outside profile/ and data/")
        total += info.file_size
        out.append((info, p.parts[0], PurePosixPath(*p.parts[1:])))
    if not out:
        raise DevImportError("the zip is empty")
    if len(out) > MAX_FILES or total > MAX_BYTES:
        raise DevImportError("the zip is far bigger than an export")
    if not any(part == "profile" for _, part, _ in out):
        raise DevImportError("not a SwitchKaro export: it has no profile/")
    return out


def import_export(root: Path, zip_path: Path, email: str, replace: bool = False) -> Profile:
    """Unpack `zip_path` as `email`'s dev account. Refuses to overwrite one
    already there unless `replace`."""
    email = email.strip().lower()
    if "@" not in email:
        raise DevImportError("give the email you sign in to SwitchKaro with")
    sub = dev_sub(email)
    prof = Profile(root, accounts.profile_id(sub))
    try:
        z = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DevImportError(f"can't read {zip_path}: {exc}") from exc
    with z:
        plan = _plan(z)
        targets = {"dir": prof.dir, "var": prof.var}
        if any(t.exists() and any(t.iterdir()) for t in targets.values()):
            if not replace:
                raise DevImportError(f"{email} already has data here: pass --replace to swap it for this zip")
            for t in targets.values():
                shutil.rmtree(t, ignore_errors=True)
        for info, part, rel in plan:
            dest = targets[PARTS[part]].joinpath(*rel.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, dest.open("wb") as fh:
                shutil.copyfileobj(src, fh)
    prof.shared.mkdir(parents=True, exist_ok=True)
    # They agreed to the notice on the service, where this data came from.
    accounts.join(prof.shared, sub, email, "", CONSENT_VERSION, admin=True)
    return prof
