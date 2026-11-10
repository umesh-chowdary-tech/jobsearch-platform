"""A person's resume style: how long, how many projects, what shows, in what order.

The person chooses; these are only starting values. A fresher's page and a
senior's page are not the same shape, so the defaults follow the years of
experience Setup recorded - and anything the person sets wins.

Kept in the profile (`resume-style.yaml`), so it goes with their data export.
The style never adds a word to the page: it decides how much of what the store
already holds is shown, and in what order.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

import yaml

from .profile import Profile

STYLE_FILE = "resume-style.yaml"

SECTIONS = ("summary", "skills", "experience", "projects", "education",
            "certifications", "achievements", "languages")
# A parser needs these to read the page: never hidden.
REQUIRED = ("skills", "experience")
PAGES = ("1", "2", "any")

# (lowest, highest) a person may choose.
RANGES = {"projects_per_job": (1, 4), "pieces_per_project": (0, 4), "project_bullets": (1, 5),
          "piece_bullets": (1, 3), "side_projects": (0, 4)}


@dataclass(frozen=True)
class Style:
    projects_per_job: int = 2
    pieces_per_project: int = 3
    project_bullets: int = 3
    piece_bullets: int = 2
    side_projects: int = 3
    pages: str = "any"
    sections: tuple[str, ...] = SECTIONS
    # Not chosen by the person: a project with no pieces keeps more bullets on
    # a job resume, and a third project may print when it adds something the
    # posting asks for. Page fitting turns `extras` off first.
    solo_bullets: int | None = None
    extras: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["sections"] = list(self.sections)
        d.pop("solo_bullets")
        d.pop("extras")
        return d

    def shows(self, section: str) -> bool:
        return section in self.sections


# What a job resume used before styles existed (phase 4 moves job resumes onto
# the person's style).
JOB = Style(solo_bullets=5)


def defaults(years: int | None) -> Style:
    """Starting values by experience. Under three years: one page, two
    projects a job. From three: two pages, three projects."""
    if (years or 0) < 3:
        return Style(projects_per_job=2, pieces_per_project=2, project_bullets=3, piece_bullets=2,
                     side_projects=2, pages="1")
    return Style(projects_per_job=3, pieces_per_project=3, project_bullets=3, piece_bullets=2,
                 side_projects=3, pages="2")


def _years(prof: Profile) -> int | None:
    try:
        sp = yaml.safe_load(prof.file("search-profile.yaml").read_text(encoding="utf-8")) or {}
        return int(((sp.get("seniority") or {}).get("years_experience")) or 0)
    except (OSError, yaml.YAMLError, ValueError, TypeError, AttributeError):
        return None


def _read(prof: Profile) -> dict[str, Any]:
    try:
        d = yaml.safe_load((prof.dir / STYLE_FILE).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return d if isinstance(d, dict) else {}


class StyleError(ValueError):
    pass


def _checked(base: Style, changes: dict[str, Any]) -> Style:
    out: dict[str, Any] = {}
    for k, v in changes.items():
        if k in RANGES:
            lo, hi = RANGES[k]
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise StyleError(f"{k} must be a number") from None
            if not lo <= n <= hi:
                raise StyleError(f"{k} must be from {lo} to {hi}")
            out[k] = n
        elif k == "pages":
            if str(v) not in PAGES:
                raise StyleError("pages is 1, 2 or any")
            out[k] = str(v)
        elif k == "sections":
            if not isinstance(v, list) or not all(isinstance(s, str) for s in v):
                raise StyleError("sections must be a list of section names")
            bad = [s for s in v if s not in SECTIONS]
            if bad:
                raise StyleError(f"unknown section: {', '.join(bad)}")
            missing = [s for s in REQUIRED if s not in v]
            if missing:
                raise StyleError(f"{' and '.join(missing)} always show - a parser needs them")
            out[k] = tuple(dict.fromkeys(v))
        else:
            raise StyleError(f"no such setting: {k}")
    return replace(base, **out)


def load(prof: Profile) -> Style:
    """The person's style: their own choices over the defaults for their years.
    A file someone broke by hand falls back to the defaults, setting by setting."""
    style = defaults(_years(prof))
    for k, v in _read(prof).items():
        try:
            style = _checked(style, {k: v})
        except StyleError:
            continue
    return style


def save(prof: Profile, changes: dict[str, Any]) -> Style:
    """Only what the person changed is written, so the rest follows their
    years if Setup changes them."""
    style = _checked(load(prof), changes)        # raises before anything is written
    mine = {**_read(prof), **{k: (list(v) if isinstance(v, (list, tuple)) else v)
                              for k, v in asdict(_checked(Style(), changes)).items() if k in changes}}
    prof.dir.mkdir(parents=True, exist_ok=True)
    (prof.dir / STYLE_FILE).write_text(yaml.safe_dump(mine, sort_keys=False), encoding="utf-8")
    return style


def view(prof: Profile) -> dict[str, Any]:
    chosen = set(_read(prof))
    return {"style": load(prof).to_dict(), "defaults": defaults(_years(prof)).to_dict(),
            "chosen": sorted(chosen & (set(RANGES) | {"pages", "sections"})),
            "sections": list(SECTIONS), "required": list(REQUIRED),
            "ranges": {k: list(v) for k, v in RANGES.items()}, "pages": list(PAGES)}


__all__ = ["JOB", "PAGES", "RANGES", "REQUIRED", "SECTIONS", "Style", "StyleError",
           "defaults", "load", "save", "view"]
