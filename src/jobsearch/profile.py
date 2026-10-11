"""One person, and where their files live.

Three kinds of file, kept apart on purpose:

  data/                shared knowledge - job boards, company names, the skill
                       vocabulary, model caps. The same for everyone.
  profiles/<id>/       one person's facts and preferences: master-store.yaml,
                       skills.yaml, search-profile.yaml, and the inbox rules
                       they approved (mail-learned.yaml).
  var/<id>/            what the agent worked out for that person: triage
                       results, decisions, resumes, applications, outreach.
  var/_shared/         board polling and model usage. One watcher polls each
                       board once for every profile; each profile keeps its
                       own record of which postings it has triaged.

Real people's profiles never belong in the repository. Only the fictional
`sample-*` ones are committed (.gitignore); point JOBSEARCH_PROFILES (the
profiles) and JOBSEARCH_VAR (what the agent works out, an uploaded resume
included) at folders outside the checkout to keep a real person's data well
away from it.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

SHARED = "_shared"
# What a profile must have. mail-learned.yaml is optional: it appears when the
# person approves their first inbox rule.
REQUIRED = ("master-store.yaml", "skills.yaml", "search-profile.yaml")
# Lowercase, digits and hyphens: an id names folders, and "_shared" can never
# be one.
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class ProfileError(Exception):
    """A profile that can't be found or chosen - a message for a human."""


def profiles_dir(root: Path) -> Path:
    env = os.environ.get("JOBSEARCH_PROFILES")
    return Path(env) if env else Path(root) / "profiles"


def var_dir(root: Path) -> Path:
    """All machine state: var/ in the checkout, or JOBSEARCH_VAR."""
    env = os.environ.get("JOBSEARCH_VAR")
    return Path(env) if env else Path(root) / "var"


@dataclass(frozen=True)
class Profile:
    root: Path
    id: str

    def __post_init__(self) -> None:
        if not _ID.match(self.id or ""):
            raise ProfileError(f"{self.id!r} is not a profile id: use lowercase letters, digits and hyphens")
        object.__setattr__(self, "root", Path(self.root))

    @property
    def data(self) -> Path:
        """Shared knowledge."""
        return self.root / "data"

    @property
    def dir(self) -> Path:
        """This person's facts and preferences."""
        return profiles_dir(self.root) / self.id

    @property
    def var(self) -> Path:
        """What the agent worked out for this person."""
        return var_dir(self.root) / self.id

    @property
    def shared(self) -> Path:
        """Board polling, the robots cache and model usage - everyone's."""
        return shared_var(self.root)

    @property
    def env(self) -> Path:
        return self.root / ".env"

    def file(self, name: str) -> Path:
        return self.dir / name

    def exists(self) -> bool:
        return all(self.file(n).exists() for n in REQUIRED)


def shared_var(root: Path) -> Path:
    return var_dir(root) / SHARED


def list_profiles(root: Path) -> list[str]:
    """Every complete profile, by id."""
    d = profiles_dir(root)
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir()
                  if p.is_dir() and _ID.match(p.name) and all((p / n).exists() for n in REQUIRED))


def resolve(root: Path, wanted: str | None = None) -> Profile:
    """The profile asked for: `wanted`, else JOBSEARCH_PROFILE, else the only
    one there is. Never a guess between several."""
    have = list_profiles(root)
    name = wanted or os.environ.get("JOBSEARCH_PROFILE") or ""
    if name:
        if name not in have:
            raise ProfileError(f"no profile {name!r} in {profiles_dir(root)}"
                               + (f" - there is {', '.join(have)}" if have else ""))
        return Profile(Path(root), name)
    if len(have) == 1:
        return Profile(Path(root), have[0])
    if not have:
        raise ProfileError(f"no profiles in {profiles_dir(root)} - see profiles/README.md")
    raise ProfileError(f"{len(have)} profiles ({', '.join(have)}): pass --profile or set JOBSEARCH_PROFILE")
