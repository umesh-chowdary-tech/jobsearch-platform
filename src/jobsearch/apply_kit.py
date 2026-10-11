"""The Apply kit: everything an application form asks for, ready to copy.

Workday has no API for candidates, and every employer runs its own site, so
filling the form stays manual - the person's hands, the person's submit. What
this removes is the thinking: which skills to add in the form's own words, which of
its guesses to delete, the certification and education fields, and the
experience fields its resume parser got wrong on the first real application
(Job Title "Checker", start January 2023).

Built only from the store, the posting and the resume variant that was sent.
Nothing is added that the store does not back: a skill must be core, working
or scoped, and a suggestion the store bans is always on the remove list.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .assemble import emittable, load_store
from .outreach import OutreachError, company_name, find_posting, variant_for
from .profile import Profile
from .resume_layout import _month, contact_bits
from .skills import BANNED, CORE, INCIDENTAL, SCOPED, WORKING, SkillIndex, load_index

MAX_ADD = 20


def _read(p: Path) -> dict[str, Any]:
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


def _names(prof: Profile) -> dict[str, Any]:
    """Workday's names for skills (shared, data/ats-names.yaml), with the
    person's own ats-names.yaml over it: names they saw on a form, and which
    of Workday's suggestions to remove or keep for them."""
    shared, mine = _read(prof.data / "ats-names.yaml"), _read(prof.file("ats-names.yaml"))
    return {"workday": {**(shared.get("workday") or {}), **(mine.get("workday") or {})},
            "workday_remove": (shared.get("workday_remove") or []) + (mine.get("workday_remove") or []),
            "workday_keep": (shared.get("workday_keep") or []) + (mine.get("workday_keep") or [])}


def _mm_yyyy(period: str) -> str:
    """'2023-09' -> '09/2023', the format Workday's date boxes take."""
    m = re.match(r"((?:19|20)\d{2})-(\d{2})", str(period))
    return f"{m.group(2)}/{m.group(1)}" if m else str(period)


def _skill(canon: str, index: SkillIndex, names: dict, why: str) -> dict[str, Any]:
    row = (names.get("workday") or {}).get(canon) or {}
    claim = index.claim(canon)
    return {
        "skill": canon,
        "workday": row.get("name") or canon,
        "seen": str(row.get("seen")) if row.get("seen") else None,
        "why": why,
        # A scoped skill is real but narrower than the word - say how, so the
        # interview answer matches the claim.
        "scope": claim.scope if claim and claim.tier == SCOPED else None,
    }


def skills_to_add(prof: Profile, posting: dict[str, Any], index: SkillIndex,
                  names: dict, surfaces: list[str]) -> list[dict[str, Any]]:
    """What the posting asks for that the store backs, then the rest of the
    resume's skills. Never a skill the store does not back."""
    ok = (CORE, WORKING, SCOPED)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(canon: str, why: str) -> None:
        canon = index.canonical(canon) or canon
        if canon in seen or index.tier(canon) not in ok:
            return
        seen.add(canon)
        out.append(_skill(canon, index, names, why))

    for canon in sorted(posting.get("matched") or {}, key=lambda c: (index.tier(c) != CORE, c)):
        add(canon, "this posting asks for it")
    for canon in surfaces:
        add(canon, "on the resume you sent")
    return out[:MAX_ADD]


def suggestions_to_remove(index: SkillIndex, names: dict) -> list[dict[str, Any]]:
    """Every known bad Workday suggestion, plus every skill the store bans or
    only touched - whatever the form offers, these come off."""
    out = [{"workday": r["name"], "why": r.get("why", ""),
            "seen": str(r.get("seen")) if r.get("seen") else None}
           for r in names.get("workday_remove") or []]
    listed = {r["workday"].lower() for r in out}
    for c in sorted(index.claims.values(), key=lambda c: c.skill.lower()):
        if c.tier in (BANNED, INCIDENTAL) and c.skill.lower() not in listed:
            why = ("on the banned list" if c.tier == BANNED
                   else "only touched - never under Skills")
            out.append({"workday": c.skill, "why": why, "seen": None})
    return out


def _certifications(store: dict[str, Any]) -> list[dict[str, str]]:
    """One row per certificate. An entry can hold several courses as facts
    ("Python for Data Science (2023)"); a form wants one row each."""
    out = []
    for e in store.get("entries") or []:
        if e.get("type") != "certification" or not emittable(e):
            continue
        courses = [re.match(r"(.+?)\s*\(((?:19|20)\d{2})\)$", str(f)) for f in e.get("facts") or []]
        courses = [m for m in courses if m]
        if courses:
            out += [{"name": f"{e.get('where')} - {m.group(1)}", "issuer": str(e.get("where")),
                     "year": m.group(2)} for m in courses]
        else:
            out.append({"name": str(e.get("headline")), "issuer": str(e.get("where")),
                        "year": str(e.get("period") or "")})
    return out


def _education(store: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for e in store.get("entries") or []:
        if e.get("type") != "education":
            continue
        degree, _, field = str(e.get("headline") or "").partition(",")
        period = str(e.get("period") or "")
        dates = re.findall(r"(?:19|20)\d{2}-\d{2}", period)
        grade = next((str(f) for f in e.get("facts") or [] if "GPA" in str(f)), "")
        out.append({
            "school": str(e.get("where") or ""),
            "degree": degree.strip(),
            "field": field.strip(),
            "from": _mm_yyyy(dates[0]) if dates else "",
            "to": _mm_yyyy(dates[1]) if len(dates) > 1 else "",
            "grade": grade.replace("CGPA", "").strip(),
        })
    return out


def build_kit(prof: Profile, posting_ref: str) -> dict[str, Any]:
    posting = find_posting(prof, posting_ref)
    pid = str(posting.get("id"))
    store = load_store(prof)
    index = load_index(prof)
    names = _names(prof)
    identity = store.get("identity") or {}
    variant = variant_for(prof, pid)
    company, _ = company_name(prof, str(posting.get("board") or ""), posting)

    band = str((identity.get("title") or {}).get("official") or "")
    jobs = []
    for job in identity.get("employment") or []:
        jobs.append({
            "title": str(job.get("title") or band),
            "company": str(job.get("employer") or ""),
            "location": str(job.get("location") or ""),
            "from": _mm_yyyy(str(job.get("start") or "")),
            "from_words": _month(str(job.get("start") or "")),
            "current": str(job.get("end", "")).lower() == "present",
        })

    return {
        "posting_id": pid,
        "company": company,
        "role": str(posting.get("title") or "").strip(),
        "resume": variant.id if variant else None,
        "experience": jobs,
        "skills_add": skills_to_add(prof, posting, index, names,
                                    variant.surfaces if variant else []),
        "skills_keep": [{"workday": r["name"], "why": r.get("why", "")}
                        for r in names.get("workday_keep") or []],
        "skills_remove": suggestions_to_remove(index, names),
        "certifications": _certifications(store),
        "education": _education(store),
        "languages": [str(l) for l in identity.get("languages") or []],
        "links": [b for b in contact_bits(identity) if "/" in b],
        "not_backed": [g for g in posting.get("required_gaps") or []
                       if index.tier(index.canonical(g) or g) not in (CORE, WORKING, SCOPED)],
    }


__all__ = ["build_kit", "OutreachError"]
