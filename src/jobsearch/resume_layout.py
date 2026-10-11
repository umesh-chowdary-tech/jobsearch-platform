"""Resume layout: how the store's entries become a page someone wants to read.

The store holds one entry per unit of evidence. A resume is not shaped like
that. Emitting each entry as its own block produced six separate headings for
one employer - which reads as six jobs, not one role with depth, and was visibly
weaker than the resume it replaced.

So: work is grouped under its employer with one date range, each entry becomes
a named workstream beneath it, and skills are grouped by category instead of
one undifferentiated comma list.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .skills import SkillIndex

# Vocabulary categories -> the labels a reader expects on a resume.
SKILL_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("AI & Agents", ("ml_ai",)),
    ("Languages", ("languages",)),
    ("Frameworks & Libraries", ("frontend", "backend", "mobile")),
    ("Cloud & DevOps", ("cloud_devops",)),
    ("Databases & Data", ("data",)),
    ("Practices & Tools", ("methods", "practices", "tools", "baseline", "other")),
]


def _year(period: str | None) -> int:
    m = re.search(r"((?:19|20)\d{2})", str(period or ""))
    return int(m.group(1)) if m else 0


def _is_present(period: str | None) -> bool:
    return "present" in str(period or "").lower()


def _month(period: str | None) -> str:
    """'2023-09 to ~2024' -> 'Sep 2023'. '2026-present' -> '2026'."""
    p = str(period or "")
    m = re.search(r"((?:19|20)\d{2})-(\d{2})", p)
    if m:
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        idx = int(m.group(2))
        if 1 <= idx <= 12:
            return f"{months[idx - 1]} {m.group(1)}"
    y = _year(p)
    return str(y) if y else ""


def entry_period(e: dict[str, Any]) -> str:
    """A tidy date range for one entry."""
    p = str(e.get("period") or "")
    if not p:
        return ""
    if _is_present(p):
        start = _month(p)
        return f"{start} - Present" if start else "Present"
    # "2019-03 to 2023-06"
    m = re.findall(r"(?:19|20)\d{2}(?:-\d{2})?", p)
    if len(m) >= 2:
        return f"{_month(m[0])} - {_month(m[1])}"
    return _month(p) or p


def _employment(identity: dict[str, Any] | None, employer: str) -> dict[str, Any] | None:
    for row in (identity or {}).get("employment") or []:
        if str(row.get("employer", "")).strip().lower() == employer.strip().lower():
            return row
    return None


def position_title(identity: dict[str, Any] | None, descriptor: str | None = None) -> str:
    """"Software Engineer II (Backend AI Engineer)": the official title, then
    the descriptor, per the identity rule. On the position line, not only in
    the header - a parser fills Job Title from the experience section, and with
    no title there Workday took "Checker" from a workstream named "... Mismatch
    Checker"."""
    band = str(((identity or {}).get("title") or {}).get("official") or "").strip()
    if band and descriptor:
        return f"{band} ({descriptor})"
    return band or (descriptor or "")


def group_work(entries: list[dict[str, Any]],
               identity: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One block per employer, newest first: title, employer, one date range.

    Six headings for one employer in a row read as six jobs. So one employer, one
    date range, named workstreams underneath - and the workstreams carry NO date
    range of their own, because a parser treats every dated line as a job:
    Workday turned six workstreams into six positions and picked "Checker".

    The range comes from identity.employment when it is there. Rebuilt from the
    selected entries it said "2023 - Present" - the Sep 2023 entry had been cut
    for length - and Workday read that as January.
    """
    work = [e for e in entries if e.get("type") == "work"]
    blocks: dict[str, list[dict[str, Any]]] = {}
    for e in work:
        blocks.setdefault(str(e.get("where") or "-"), []).append(e)

    out = []
    for employer, items in blocks.items():
        items = sorted(items, key=lambda e: (-_year(e.get("period")),
                                             -int(_is_present(e.get("period")))))
        job = _employment(identity, employer)
        if job:
            start = f"{job.get('start')}"
            ongoing = str(job.get("end", "")).lower() == "present"
            span = (f"{_month(start)} - Present" if ongoing
                    else entry_period({"period": f"{start} to {job.get('end')}"}))
            first = _year(start)
            location = str(job.get("location") or "")
        else:
            starts = [_year(e.get("period")) for e in items if _year(e.get("period"))]
            ongoing = any(_is_present(e.get("period")) for e in items)
            first = min(starts) if starts else 0
            earliest = min((str(e.get("period") or "") for e in items
                            if _year(e.get("period")) == first), default="")
            span = f"{_month(earliest)} - Present" if ongoing else entry_period(
                {"period": earliest})
            location = ""
        out.append({"employer": employer, "period": span, "entries": items,
                    "location": location, "start_year": first,
                    "_sort": (first, 1 if ongoing else 0)})
    return sorted(out, key=lambda b: (-b["_sort"][1], -b["_sort"][0]))


def since(e: dict[str, Any], start_year: int) -> str:
    """"since 2025" for a workstream that began after the job did, or "".

    Not a range, and not on a heading line: the year is kept for a human reader
    without giving a parser another date range to mistake for a job.
    """
    y = _year(e.get("period"))
    return f"since {y}" if y and start_year and y > start_year else ""


def heading(e: dict[str, Any]) -> str:
    """The workstream name. Falls back to the headline when none is set."""
    return str(e.get("title") or e.get("headline") or "")


def lead_line(e: dict[str, Any], start_year: int = 0) -> str:
    """The headline, used as the one-line description under a named heading,
    with "(since 2025)" when the workstream began after the job did.

    Only when a short title exists - otherwise the heading IS the headline and
    printing it twice is padding.
    """
    line = str(e.get("headline") or "") if e.get("title") else ""
    when = since(e, start_year)
    if when:
        # " · since", not "(since": in italic, the parenthesis extracted from
        # the PDF as "( since 2025)" - harmless to a parser, but untidy text.
        line = f"{line} · {when}" if line else when.capitalize()
    return line


# How many skills a stack line names. One line, not a second Skills section.
STACK_CAP = 6


def stack_items(e: dict[str, Any], skills: Iterable[str],
                index: SkillIndex | None) -> list[str]:
    """The "Stack:" line under a workstream or project.

    The bullets say what was built, and rarely with what: across the whole
    store they name a handful of technologies. The rest lived in `tech` and
    reached the page only through the Skills line - the section Workday
    ignored, inferring skills from the body text instead. So a Java posting
    read a resume whose only Java was in a list.

    This moves no claim and makes none. It names, under the entry, the skills
    from the page's own Skills line that this entry's `tech` backs - so it can
    never show a skill the Skills line does not, nor one skills.yaml would
    not let through - in the Skills line's order, posting-relevant first.
    """
    if index is None or e.get("type") not in ("work", "project"):
        return []
    mine = {c.skill for c in index.emittable_for(e.get("tech") or [])}
    return [s for s in skills if (index.canonical(s) or s) in mine
            and not _is_method(s, index)][:STACK_CAP]


def _is_method(skill: str, index: SkillIndex) -> bool:
    return index.category.get(index.canonical(skill) or skill) == METHODS


METHODS = "methods"


def practice_items(entries: Iterable[dict[str, Any]], skills: Iterable[str],
                   index: SkillIndex | None) -> list[str]:
    """The "Practices:" line under an employer: Agile, Code Review, Debugging.

    Ways of working belong to the job, not to one workstream. Printed under
    each stack line they repeated six times and pushed the actual stack off it.
    Same rule as `stack_items` otherwise: only skills on the page's Skills line
    that an entry in this job backs.
    """
    if index is None:
        return []
    mine = {c.skill for e in entries for c in index.emittable_for(e.get("tech") or [])}
    return [s for s in skills if (index.canonical(s) or s) in mine and _is_method(s, index)]


def contact_bits(identity: dict[str, Any]) -> list[str]:
    """Email, phone, place and profile links, in the order a reader expects.

    The store keeps the GitHub *handle*. Printed bare, a handle in a header is
    just a word - neither a reader nor an ATS can tell it is a
    GitHub profile. The hand-written resumes always said github.com/...; the
    generated ones did not, until the PDF text check caught it.
    """
    gh = str(identity.get("github") or "").strip()
    if gh and "/" not in gh and "." not in gh:
        gh = f"github.com/{gh}"
    li = str(identity.get("linkedin") or "").strip()
    if li and "/" not in li and "." not in li:
        li = f"linkedin.com/in/{li}"
    return [str(x) for x in (identity.get("email"), identity.get("phone"),
                             identity.get("location"), li, gh) if x]


def group_skills(skills: Iterable[str], index: SkillIndex
                 ) -> list[tuple[str, list[str]]]:
    """Grouped by category, in reading order, empty groups dropped."""
    by_cat: dict[str, list[str]] = {}
    for s in skills:
        # By the canonical name: a skill printed in the posting's spelling
        # ("NodeJS", "AI-assisted development") was falling to the end.
        by_cat.setdefault(index.category.get(index.canonical(s) or s, "other"), []).append(s)
    out = []
    for label, cats in SKILL_GROUPS:
        items = [s for c in cats for s in by_cat.get(c, [])]
        if items:
            out.append((label, items))
    placed = {s for _, items in out for s in items}
    leftover = [s for s in skills if s not in placed]
    if leftover:
        out.append(("Other", leftover))
    return out
