"""How well a resume matches a posting, read the way screening software reads it.

There is no single "ATS score". Three things decide whether a resume reaches a
person, and they reward the same thing:

- a recruiter's keyword search over parsed resumes, which every ATS has;
- the ranking or grading some ATSes now do on their own (Workday, Oracle). These
  read skills out of the body text: Workday skipped our Skills section entirely
  and inferred skills from the bullets (docs/assembly.md, "What Workday found");
- the resume checkers recruiters and candidates run, which report the share of
  a posting's keywords a resume contains.

So the match is: the skills the posting names, weighted by WHERE it names them,
found on the resume, credited by WHERE they appear. It is our model of those
mechanisms, not any one vendor's number, and it is reported as such.

Why this is safe to optimize, when docs/assembly.md rejected an "ATS score": the
danger was an optimizer discovering that adding Kubernetes raises the score.
Nothing here can add a word. The assembler only CHOOSES among store entries,
whose facts print verbatim, and backed skills. A keyword the person cannot back stays
in the denominator, lowers every candidate resume equally, and is reported as a
gap. It never reaches the page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .skills import CORE, NOT_A_SKILL, SCOPED, WORKING, SkillIndex
from .triage import split_requirements

# Where the posting names a skill. The title is what a recruiter types into the
# search box. Required outranks preferred for the reason triage splits them: a
# wishlist should not count as much as the ask.
IN_TITLE, IN_REQUIRED, IN_PREFERRED, IN_BODY = 3.0, 2.0, 1.0, 1.0

# The role words of the title ("Full Stack Developer") weigh as much as a skill
# named in it. The resume answers them with its descriptor.
TITLE_ROLE = 3.0

# Where the resume names it. Experience is what parsers and graders read as
# evidence. A Skills line alone is a claim with nothing under it - and the one
# section Workday ignored. Only the order matters to the choices the assembler
# makes; the exact values are a judgement, and test_ats checks the choices
# survive moving them.
CREDIT = {"experience": 1.0, "projects": 0.8, "summary": 0.7, "other": 0.6,
          "skills": 0.5}

# A skill named in a second and third workstream is evidence of depth, and
# keyword rankers count repeats - with saturation, so stuffing earns nothing.
DEPTH_PER_MENTION, DEPTH_MAX_EXTRA = 0.1, 2

CLAIMABLE = (CORE, WORKING, SCOPED)

_LEVEL = {"i", "ii", "iii", "iv", "v", "1", "2", "3", "4", "senior", "sr", "junior",
          "jr", "lead", "staff", "principal", "associate", "intern", "trainee",
          "level", "grade", "mid", "entry", "head", "chief", "manager"}
_FILLER = {"and", "or", "with", "for", "the", "a", "an", "of", "in", "at", "to", "on"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z0-9.#+]*", (text or "").lower())


@dataclass
class PostingKeywords:
    """What the posting asks for, and how much each ask weighs."""

    weights: dict[str, float]          # canonical skill -> weight
    where: dict[str, str]              # canonical skill -> title/required/preferred/body
    claimable: set[str]                # the ones skills.yaml lets onto the Skills line
    role_words: set[str]               # title words naming the role, not a skill or a level
    text: str = ""                     # everything the posting says, lowercased
    # What may earn credit: claimable skills, plus table-stakes practice words
    # (Agile, Code Review...) when a fact really says them. Not an incidental
    # skill a fact happens to name - a bullet may say "stored uploads in Azure
    # Blob Storage", which is true and allowed, but rewarding the optimizer for
    # it would be claiming Azure by the back door.
    creditable: set[str] = field(default_factory=set)

    @property
    def total(self) -> float:
        return sum(self.weights.values()) + (TITLE_ROLE if self.role_words else 0.0)


def role_words(title: str, index: SkillIndex) -> set[str]:
    """"Software Engineer II - Java Full Stack Developer + React + AWS" ->
    {software, engineer, full, stack, developer}. Skills are scored as skills
    and levels are not something a descriptor can answer."""
    return {w for w in _words(title)
            if w not in _LEVEL and w not in _FILLER and index.canonical(w) is None}


def posting_keywords(posting: dict[str, Any], index: SkillIndex) -> PostingKeywords:
    title = str(posting.get("title") or "")
    body = str(posting.get("description") or "")
    reqs = str(posting.get("requirements") or "")
    # The stored requirements section when triage found one; otherwise the
    # whole description, read as triage reads it - all required unless a
    # nice-to-have heading says otherwise.
    required, preferred = split_requirements(reqs or body)

    weights: dict[str, float] = {}
    where: dict[str, str] = {}

    def note(skills: Iterable[str], w: float, label: str) -> None:
        for s in skills:
            canon = index.canonical(s) or s
            if index.tier(canon) == NOT_A_SKILL:
                continue
            if w > weights.get(canon, 0.0):
                weights[canon], where[canon] = w, label

    note(index.detect(body), IN_BODY, "body")
    # Triage read the full description; the stored one is an excerpt. What it
    # matched is still something the posting asked for.
    note(posting.get("matched") or {}, IN_BODY, "body")
    note(posting.get("nice_gaps") or [], IN_PREFERRED, "preferred")
    note(index.detect(preferred), IN_PREFERRED, "preferred")
    note(posting.get("required_gaps") or [], IN_REQUIRED, "required")
    note(index.detect(required), IN_REQUIRED, "required")
    note(index.detect(title), IN_TITLE, "title")

    claimable = {k for k in weights if index.tier(k) in CLAIMABLE}
    return PostingKeywords(weights, where, claimable, role_words(title, index),
                           f"{title}\n{reqs}\n{body}".lower(),
                           claimable | (weights.keys() & index.baseline))


def keywords_for(skills: Iterable[str], index: SkillIndex) -> PostingKeywords:
    """A bare set of skills, each weighted as a requirement - for callers with
    no posting to hand."""
    weights = {index.canonical(s) or s: IN_REQUIRED for s in skills}
    claimable = {k for k in weights if index.tier(k) in CLAIMABLE}
    return PostingKeywords(weights, {k: "required" for k in weights}, claimable, set(),
                           creditable=claimable | (weights.keys() & index.baseline))


# ── the resume side ──────────────────────────────────────────────────────────

def entry_text(e: dict[str, Any]) -> str:
    """What the page prints for an entry: its heading, headline and bullets.
    Not its `tech` tags - those reach the page only through the Skills line."""
    from .render import bullets
    from .resume_layout import heading
    return "\n".join([heading(e), str(e.get("headline") or ""), *bullets(e)])


def _section(e: dict[str, Any]) -> str:
    kind = e.get("type")
    return {"work": "experience", "project": "projects"}.get(str(kind), "other")


@dataclass
class Match:
    match: float                       # weighted share of the posting's asks on the page
    ceiling: float                     # the best an honest resume could reach
    depth: float                       # repeat mentions, saturating; breaks ties only
    title: float                       # descriptor vs the title's role words, 0..1
    found: dict[str, str] = field(default_factory=dict)   # skill -> best section
    missing: list[str] = field(default_factory=list)      # the store has it; this page does not
    unclaimable: list[str] = field(default_factory=list)  # asked for; no honest page can have it

    @property
    def key(self) -> tuple[float, float]:
        """What "better" means. Coverage first; depth only when coverage ties."""
        return (round(self.match, 6), round(self.depth, 6))

    def to_dict(self) -> dict[str, Any]:
        return {"match": round(self.match, 3), "ceiling": round(self.ceiling, 3),
                "title": round(self.title, 2), "missing": self.missing,
                "skills_only": sorted(k for k, s in self.found.items() if s == "skills"),
                "unclaimable": self.unclaimable}


def title_credit(descriptor: str, kw: PostingKeywords) -> float:
    if not kw.role_words:
        return 0.0
    mine = {w for w in _words(descriptor) if w not in _FILLER}
    return len(mine & kw.role_words) / len(kw.role_words)


def score_terms(kw: PostingKeywords, sections: list[tuple[str, set[str]]],
                descriptor: str, reachable: set[str] | None = None,
                best_title: float = 1.0) -> Match:
    """`sections` is (section name, skills its text names), one per entry,
    plus one each for the summary and the Skills line. `reachable` is what
    the store could put on a page at all; without it, the backed skills."""
    reachable = kw.claimable if reachable is None else reachable
    best: dict[str, float] = {}
    where: dict[str, str] = {}
    mentions: dict[str, int] = {}
    for name, terms in sections:
        credit = CREDIT[name]
        for t in terms & kw.creditable:
            if credit > best.get(t, 0.0):
                best[t], where[t] = credit, name
            if name in ("experience", "projects"):
                mentions[t] = mentions.get(t, 0) + 1

    total = kw.total or 1.0
    title = title_credit(descriptor, kw)
    covered = sum(kw.weights[t] * c for t, c in best.items()) + TITLE_ROLE * title
    depth = sum(kw.weights[t] * DEPTH_PER_MENTION * min(n - 1, DEPTH_MAX_EXTRA)
                for t, n in mentions.items() if n > 1)
    ceiling = (sum(w for k, w in kw.weights.items() if k in reachable)
               + (TITLE_ROLE * best_title if kw.role_words else 0.0))
    by_weight = lambda ks: sorted(ks, key=lambda k: (-kw.weights[k], k))
    return Match(
        match=covered / total,
        ceiling=ceiling / total,
        depth=depth / total,
        title=title,
        found=where,
        missing=by_weight(reachable - best.keys()),
        unclaimable=by_weight(set(kw.weights) - reachable),
    )


class Scorer:
    """Scores candidate resumes for one posting. Detection is cached per entry,
    because the assembler scores hundreds of candidate selections."""

    def __init__(self, kw: PostingKeywords, index: SkillIndex) -> None:
        self.kw, self.index = kw, index
        self._cache: dict[str, set[str]] = {}
        self.reachable: set[str] | None = None
        self.best_title = 1.0

    def reach(self, entries: Iterable[dict[str, Any]], approved: list[str]) -> None:
        """Sets the ceiling: every posting keyword some entry's text names or
        its `tech` backs, and the best title an approved descriptor gets."""
        found: set[str] = set()
        for e in entries:
            found |= self.terms(e)
            found |= {c.skill for c in self.index.emittable_for(e.get("tech") or [])}
        self.reachable = found & self.kw.creditable
        self.best_title = max((title_credit(d, self.kw) for d in approved), default=0.0)

    def terms(self, e: dict[str, Any]) -> set[str]:
        key = str(e.get("id"))
        if key not in self._cache:
            self._cache[key] = self.index.detect(entry_text(e))
        return self._cache[key]

    def score(self, entries: list[dict[str, Any]], skills: Iterable[str],
              descriptor: str, summary: str | None = None) -> Match:
        from .resume_layout import practice_items, stack_items
        skills = list(skills)
        canon = lambda xs: {self.index.canonical(s) or s for s in xs}
        sections = []
        jobs: dict[str, list[dict[str, Any]]] = {}
        for e in entries:
            # The stack line prints under the entry, so it is body text.
            sections.append((_section(e), self.terms(e) | canon(stack_items(e, skills, self.index))))
            if e.get("type") == "work":
                jobs.setdefault(str(e.get("where") or "-"), []).append(e)
        # So is the Practices line under each employer.
        for block in jobs.values():
            sections.append(("experience", canon(practice_items(block, skills, self.index))))
        if summary:
            sections.append(("summary", self.index.detect(summary)))
        sections.append(("skills", {self.index.canonical(s) or s for s in skills}))
        return score_terms(self.kw, sections, descriptor, self.reachable, self.best_title)


def best_descriptor(approved: list[str], kw: PostingKeywords) -> str:
    """The approved descriptor whose words the title uses most. On a tie, the
    one the rest of the posting uses most. Never a coined one."""
    if not approved:
        return ""                      # nothing approved: the official title stands alone

    def in_text(d: str) -> int:
        return sum(len(re.findall(rf"(?<![\w]){re.escape(w)}(?![\w])", kw.text))
                   for w in _words(d) if w not in _FILLER)

    return max(approved, key=lambda d: (title_credit(d, kw), in_text(d),
                                        -approved.index(d)))
