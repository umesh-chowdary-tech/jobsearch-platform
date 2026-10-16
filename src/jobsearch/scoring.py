"""The offline match score: passes 1 and 2 in one number from 0 to 100, free.

Pass 1 says whether a posting can be applied to at all; pass 2 counts which of
its skills the person can back. This weighs everything both passes saw - how
much of the stack is backed, what is missing, whether the role is the kind of
work they do, how well the place and the level fit, how new it is - so the
model (pass 3) reads the best of the list first, and nothing below the bar.

    skills    0-45   backed skills, weighted by tier, over the skills named
    gaps      0..-15 each required skill nothing backs costs 5
    role      0-15   the kind of work the person has done
    place     0-15   preferred city, remote, acceptable city
    level     0-15   years asked against years had
    fresh     0-10   how recently it was posted

A posting pass 1 rejected, or that names nothing the person backs, scores 0.
The parts are kept with the posting, so the screen can say why.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from .pool import _today, age_day
from .roles import role_of, roles_of
from .skills import CORE, SCOPED, WORKING
from .triage import FLAG, NO_MATCH, REJECT, Triaged, location_verdict, required_years

# What a posting must score for the model to read it, unless the profile's
# scoring.ai_min says otherwise.
AI_MIN = 40
TIER_POINTS = {CORE: 3, WORKING: 2, SCOPED: 1}


def person_roles(store: dict[str, Any]) -> list[str]:
    """The kinds of work a person's own titles name, most common first."""
    ident = store.get("identity") or {}
    title = ident.get("title") or {}
    titles: list[str] = []
    if isinstance(title, dict):
        titles += [str(title.get("official") or "")] + [str(t) for t in title.get("approved_descriptors") or []]
    elif title:
        titles.append(str(title))
    titles += [str(e.get("title") or "") for e in ident.get("employment") or [] if isinstance(e, dict)]
    return roles_of(t for t in titles if t)


def ai_min(profile: dict[str, Any]) -> int:
    try:
        return int((profile.get("scoring") or {}).get("ai_min", AI_MIN))
    except (TypeError, ValueError):
        return AI_MIN


def _skills(t: Triaged) -> int:
    cov = t.coverage
    if not cov:
        return 0
    considered = set(cov.matched) | cov.absent | (cov.shallow - set(cov.matched))
    if not considered:
        return 0
    got = sum(TIER_POINTS.get(tier, 0) for tier in cov.matched.values())
    return round(45 * got / (3 * len(considered)))


def _role(role: str, mine: list[str], skipped: set[str]) -> int:
    if role in skipped:
        return 0            # a kind of work they said to skip
    if role in mine[:2]:
        return 15
    if role in mine:
        return 12
    if role == "software":
        return 10           # engineering, no narrower craft named
    if role == "other":
        return 6
    return 4


def _place(t: Triaged, profile: dict[str, Any]) -> int:
    text = f"{t.title}\n{t.posting.get('description') or ''}"
    ok, why = location_verdict(t.posting.get("location", ""), text, profile)
    if not ok:
        return 0
    if why.startswith("preferred"):
        return 15
    if why == "remote":
        return 12
    if why.startswith("acceptable"):
        return 11
    return 9                # remote, read from the description


def _level(t: Triaged, profile: dict[str, Any]) -> int:
    if t.verdict == FLAG:
        return 4            # below the current level
    years = required_years(f"{t.title}\n{t.posting.get('description') or ''}")
    if years is None:
        return 10
    have = int((profile.get("seniority") or {}).get("years_experience") or 0)
    return 15 if years <= have else 8


def _fresh(posting: dict[str, Any], today: date) -> int:
    day = age_day(posting)
    if not day:
        return 5
    age = (today - date.fromisoformat(day)).days
    for limit, points in ((3, 10), (7, 8), (14, 6), (30, 4), (60, 2)):
        if age <= limit:
            return points
    return 0


def match(t: Triaged, profile: dict[str, Any], mine: Iterable[str], today: date | None = None,
          skipped: set[str] | None = None) -> tuple[int, dict[str, int]]:
    """(score, parts) for one triaged posting."""
    if t.verdict in (REJECT, NO_MATCH):
        return 0, {}
    mine = list(mine)
    parts = {
        "skills": _skills(t),
        "gaps": -min(15, 5 * len(t.required_gaps)),
        "role": _role(t.posting.get("role") or role_of(t.title), mine, skipped or set()),
        "place": _place(t, profile),
        "level": _level(t, profile),
        "fresh": _fresh(t.posting, today or _today()),
    }
    return max(0, min(100, sum(parts.values()))), parts
