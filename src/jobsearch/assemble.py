"""Module 4 — Assembly.

The best-matching resume for each posting, reused when an existing one is just
as good. See docs/assembly.md.

**Facts are emitted verbatim from the store.** The whole reason the store exists
is that reworded claims drift into invented ones across resume variants - a
user count becomes a bigger, rounder one, and unmeasured percentages appear
from nowhere. An assembler that paraphrases reintroduces exactly that failure, one
generation at a time.

So selection, ordering and emphasis are deterministic. A model gets exactly one
job here - a summary line - and every sentence it writes is checked back against
the facts it was given before it is allowed onto the page.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

import yaml

from .ats import Match, PostingKeywords, Scorer, best_descriptor, posting_keywords
from .grounding import numbers_grounded
from .llm import LLM
from .models import utcnow
from .profile import Profile
from .skills import CORE, SCOPED, WORKING, SkillIndex


def emittable(entry: dict[str, Any]) -> bool:
    """Rule 5 of master-store.yaml, enforced in one place."""
    return entry.get("confidence") != "low" and entry.get("status") != "parked"


@dataclass
class Variant:
    id: str
    angle: str
    secondary_angles: list[str] = field(default_factory=list)
    emits: list[str] = field(default_factory=list)
    surfaces: list[str] = field(default_factory=list)
    title_descriptor: str = ""
    summary: str | None = None
    summary_by: str | None = None
    created: str = field(default_factory=utcnow)
    sent_to: list[dict[str, Any]] = field(default_factory=list)
    parse_check: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Variant":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── selection ────────────────────────────────────────────────────────────────

def entry_relevance(entry: dict[str, Any], index: SkillIndex, wanted: set[str],
                    angle: str) -> int:
    """How much this entry speaks to the posting. Higher sorts earlier."""
    tech = {index.canonical(t) or t for t in (entry.get("tech") or [])}
    score = 3 * len(tech & wanted)
    angles = set(entry.get("angles") or [])
    if angle in angles:
        score += 4
    if "all" in angles:
        score += 1
    if entry.get("type") == "work":
        score += 2                       # employment outranks side projects
    if any(m.get("measured") for m in (entry.get("metrics") or [])):
        score += 1                       # a measured claim is worth the space
    if entry.get("evidence", "").startswith("http"):
        score += 1                       # linkable beats unlinkable
    if entry.get("priority") == "low":
        # An explicit "cut this first" from the store. When a person drops an
        # entry by hand for length, recording that as data means the assembler
        # repeats the judgement instead of rediscovering it.
        score -= 50                      # decisive: it appears only if nothing else can
    return score


def substance(e: dict[str, Any]) -> tuple:
    """What decides between two entries that match the posting equally well:
    the person's own "cut this first" mark, then a measured number, then evidence a
    reader can open, then recency. Never the posting - that is the match's job."""
    from .render import _start_year
    return (e.get("priority") != "low",
            any(m.get("measured") for m in (e.get("metrics") or [])),
            str(e.get("evidence", "")).startswith("http"),
            _start_year(e))


# Projects per job. Past two, a project prints only when it adds something the
# posting asks for that nothing chosen has - the Java a Java posting wants.
MAX_PROJECTS = 2
# Pieces under one project.
MAX_PIECES = 3


def select_entries(store: dict[str, Any], index: SkillIndex,
                   wanted: "PostingKeywords | set[str]", max_work: int = 6,
                   max_projects: int = 3, scorer: Scorer | None = None
                   ) -> list[dict[str, Any]]:
    """The entries that make the best-matching page, one project at a time.

    Work is chosen as projects: an entry with the pieces that are `part_of` it
    (resume_layout.projects). Printed one by one, six workstreams of one job
    read as six small things, and a parser took the first one for the job.

    Each slot goes to the project (or side project) that raises the ATS match
    most, given what is already chosen (ats.py). A job shows up to
    MAX_PROJECTS; another prints only if it raises the match. "Cut this first"
    loses every tie, so a low-priority entry fills a slot only when it adds
    something nothing else can: the only Spring Boot on record prints for a
    Spring Boot posting and not for a Python one.

    `max_work` caps the projects across all jobs, `max_projects` the side
    projects. Only store entries are chosen, and they print verbatim.
    """
    if not isinstance(wanted, PostingKeywords):
        from .ats import keywords_for
        wanted = keywords_for(wanted, index)
    scorer = scorer or Scorer(wanted, index)
    entries = [e for e in (store.get("entries") or []) if emittable(e)]
    work = [e for e in entries if e.get("type") == "work"]
    side = [e for e in entries if e.get("type") == "project"]
    rest = [e for e in entries if e.get("type") in ("education", "certification",
                                                    "award", "profile")]

    def page_of(chosen: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        return [e for g in chosen for e in g] + rest

    def score(page: list[dict[str, Any]]) -> Match:
        skills = surfaced_skills(page, index, wanted.claimable, weights=wanted.weights)
        return scorer.score(page, skills, "")

    # Each job's projects, with their best pieces (by what they add, then
    # the same tie-breaks as everything else).
    from .resume_layout import projects
    by_job: dict[str, list[dict[str, Any]]] = {}
    for e in work:
        by_job.setdefault(str(e.get("where") or "-"), []).append(e)
    groups: list[list[dict[str, Any]]] = []
    for items in by_job.values():
        for g in projects(items):
            lead = g["entry"]
            pieces = sorted(g["pieces"], key=lambda p: (score([lead, p] + rest).key, substance(p)),
                            reverse=True)[:MAX_PIECES]
            groups.append([lead, *pieces])
    groups += [[e] for e in side]

    def job(g: list[dict[str, Any]]) -> str | None:
        return str(g[0].get("where") or "-") if g[0].get("type") == "work" else None

    # The newest role is always included, whatever its keyword overlap.
    #
    # On a TypeScript/React posting the newest, most senior workstream scored
    # 6th of 8 on vocabulary alone and fell outside max_work - so the resume
    # silently dropped the current and most senior work. A reader sees that gap
    # immediately, and no keyword argument survives it. The match decides the
    # tail, never the top.
    from .render import chronological
    picked: list[list[dict[str, Any]]] = []
    if work and max_work > 0:
        newest = chronological(work)[0]
        picked = [next(g for g in groups if any(e is newest for e in g))]
    pool = [g for g in groups if not any(g is p for p in picked)]

    def room(g: list[dict[str, Any]]) -> bool:
        if job(g) is None:
            return sum(job(p) is None for p in picked) < max_projects
        return sum(job(p) is not None for p in picked) < max_work

    def extra(g: list[dict[str, Any]]) -> bool:
        """Past a job's MAX_PROJECTS, or marked "cut first": in only for what
        it adds. A cut-first entry never takes one of the regular slots - on
        a Java posting the old Java entry joined the two main projects, it did
        not push one of them off."""
        if job(g) is None:
            return False
        at_job = sum(job(p) == job(g) for p in picked)
        return at_job >= MAX_PROJECTS or g[0].get("priority") == "low"

    while True:
        now = score(page_of(picked)).match
        regular, extras = [], []
        for g in pool:
            if not room(g):
                continue
            m = score(page_of(picked + [g]))
            if not extra(g):
                regular.append((m.key, substance(g[0]), g))
            elif m.match > now + 1e-9:
                extras.append((m.key, substance(g[0]), g))
        open_ = regular or extras
        if not open_:
            break
        best = max(open_, key=lambda t: (t[0], t[1]))[2]
        picked.append(best)
        pool = [g for g in pool if g is not best]

    # Work first, then side projects, each in the order chosen: the summary is
    # written from the first few, so they should be the ones that matched.
    return ([e for g in picked if job(g) for e in g]
            + [e for g in picked if job(g) is None for e in g] + rest)


def surfaced_skills(entries: Iterable[dict[str, Any]], index: SkillIndex,
                    wanted: set[str], limit: int = 19,
                    weights: dict[str, float] | None = None) -> list[str]:
    """Backed skills only, posting-relevant first.

    Nothing here is chosen by a model. `incidental` and `banned` cannot appear
    because they are not in `matched` and the tier check rejects them - the
    assembler has no way to name a skill the store does not back.
    """
    seen: dict[str, str] = {}
    for e in entries:
        for t in (e.get("tech") or []):
            canon = index.canonical(t) or t
            tier = index.tier(canon)
            if tier in (CORE, WORKING, SCOPED) and canon not in seen:
                seen[canon] = tier
    # WORKING outranks SCOPED: a scoped skill is real but narrower, so it
    # should not push a full claim off the line. Redshift (query-level only)
    # was beating REST on a posting about APIs.
    order = {CORE: 0, WORKING: 1, SCOPED: 2}

    # Skills the posting names come first. Beyond those, prefer ones from the
    # same categories the posting is asking about.
    #
    # Sorting the tail by tier then alphabetically is arbitrary, and it showed:
    # on a Node/React posting the cap kept Redshift and Python while cutting
    # Express.js, REST and Jest - one of which answers the posting's explicit
    # ask for automated testing.
    near = {index.category.get(w) for w in wanted if index.category.get(w)}
    # Among the skills the posting names, the ones it names in its title come
    # first: a recruiter scanning the line looks for those words. Then the
    # pinned ones (skills.yaml `always`), so the cap never cuts them.
    weights = weights or {}
    pinned = set(getattr(index, "always", []) or [])
    ranked = sorted(
        seen.items(),
        key=lambda kv: (
            0 if kv[0] in wanted else 1 if kv[0] in pinned else 2,
            -weights.get(kv[0], 0.0),
            0 if index.category.get(kv[0]) in near else 1,
            order.get(kv[1], 9),
            kv[0],
        ),
    )
    # The cap keeps the technologies focused. Ways of working (Agile, Code
    # Review, the coding assistants) sit outside it: counted in, seven of them
    # pushed TypeScript and Docker off a full-stack resume.
    from .resume_layout import _is_method
    tech = [k for k, _ in ranked if not _is_method(k, index)][:limit]
    keep = set(tech) | {k for k, _ in ranked if _is_method(k, index)}
    return [k for k, _ in ranked if k in keep]


# ── the library ──────────────────────────────────────────────────────────────

def library_path(prof: Profile) -> Path:
    return prof.var / "resume-variants.json"


def load_library(prof: Profile) -> list[Variant]:
    p = library_path(prof)
    if not p.exists():
        return []
    try:
        loaded = [Variant.from_dict(d) for d in json.loads(p.read_text(encoding="utf-8"))]
    except (json.JSONDecodeError, OSError, TypeError):
        return []
    # Ids name the files in var/resumes. A new variant used to take its
    # angle as its id, so a second "ai-platform" overwrote the first one's
    # PDF - one employer's resume was replaced by another's. Later duplicates
    # get a suffix; their files are gone, and a rebuild says so.
    taken: set[str] = set()
    for v in loaded:
        v.id = unique_id(v.id, taken)
        taken.add(v.id)
    return loaded


def save_library(prof: Profile, variants: list[Variant]) -> Path:
    p = library_path(prof)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([v.to_dict() for v in variants], indent=2), encoding="utf-8")
    return p


def unique_id(base: str, taken: Iterable[str]) -> str:
    taken = set(taken)
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def materialize(store: dict[str, Any], emits: Iterable[str]) -> list[dict[str, Any]]:
    """A stored variant's entries as they read in the store today."""
    by_id = {e.get("id"): e for e in (store.get("entries") or []) if emittable(e)}
    return [by_id[i] for i in emits if i in by_id]


def name_for(entries: list[dict[str, Any]], scorer: Scorer, m: Match) -> tuple[str, str]:
    """(id, angle) for a new variant, from what it matched.

    The id names the files, so it says what the resume leads with:
    "java-react-aws", not an archetype guessed before anything was chosen.
    The angle is the store tag the matching entries share, for outreach.
    """
    kw = scorer.kw
    found = set(m.found)
    angles: Counter[str] = Counter()
    for e in entries:
        if e.get("type") not in ("work", "project"):
            continue
        hits = scorer.terms(e) & kw.weights.keys()
        for a in e.get("angles") or []:
            if a != "all":
                angles[a] += sum(kw.weights[h] for h in hits)

    def first_seen(skill: str) -> int:
        # Whole words: "git" inside "GitLab" named a frontend resume "git-...".
        m = re.search(rf"(?<![\w]){re.escape(skill.lower())}(?![\w])", kw.text)
        return m.start() if m else len(kw.text)

    top = sorted(found, key=lambda k: (-kw.weights[k], first_seen(k), k))[:3]
    slug = "-".join(re.sub(r"[^a-z0-9]+", "-", k.lower()).strip("-") for k in top)
    angle = max(sorted(angles), key=lambda a: angles[a]) if angles else "general"
    return slug or angle, angle


def pct(m: Match) -> str:
    return f"{m.match:.0%}"


# ── the one job a model gets ─────────────────────────────────────────────────

SUMMARY_SYSTEM = """You write the two-line summary at the top of a resume.

You are given ROLES, each with its own facts. You may only restate those facts.
You may not add a number, a technology, a scale, an adjective of achievement, or
anything not present. If a fact has no number, your sentence has no number.

Never mix facts from different roles into one claim. "Second-in-command" belongs
to the role it is listed under and to no other.

Two sentences, under 50 words. Lead with the most senior thing. Plain and
specific - no "passionate", no "proven track record", no "results-driven".

Given a role "Built the company's payments API (facts: second engineer on the
team; moved it to event-driven settlement)" and a role "Maintains the admin
dashboard (facts: rebuilt the reports page)":

GOOD: "Built the company's payments API as the team's second engineer, moving
it to event-driven settlement. Also maintains the admin dashboard, where they
rebuilt the reports page."

BAD: "Built event-driven settlement and the reports page as the team's second
engineer." - that conflates two roles."""


def write_summary(llm: LLM, roles: list[str], applying_for: str
                  ) -> tuple[str | None, str]:
    """Optional. Returns (summary, attribution). Verified before use."""
    if not llm.available:
        return None, "No AI involved"
    ans = llm.ask_json(
        SUMMARY_SYSTEM,
        f"APPLYING FOR: {applying_for}\n\n" + "\n\n".join(roles)
        + '\n\nReturn {"summary": "..."}',
        max_tokens=1400,
    )
    if not ans.ok or not ans.data:
        return None, ans.attribution
    text = str(ans.data.get("summary") or "").strip()
    return (text or None), ans.attribution


def summary_is_grounded(summary: str, facts: list[str]) -> tuple[bool, str]:
    """Every number in the summary must appear in the facts, with its unit.

    This is the guardrail that matters. A model asked for a resume summary will
    reach for "improved performance by 40%" because that is what resume
    summaries sound like - and that is precisely the invented-percentage
    failure the store was built to end. A prompt saying "no new numbers" is a
    hope; checking the numbers is not.

    Digits alone were not enough: "3 years of experience" passed, because
    "under 3 months" is a fact. The check is outreach's, now shared.
    """
    if not summary:
        return False, "empty"
    ok, why = numbers_grounded(summary, facts)
    if not ok:
        return False, why
    if len(summary.split()) > 70:
        return False, "summary is too long"
    return True, "grounded"


# ── building ─────────────────────────────────────────────────────────────────

def build(prof: Profile, store: dict[str, Any], index: SkillIndex,
          posting: dict[str, Any], llm: LLM | None = None,
          use_model: bool = True) -> tuple[Variant, list[dict[str, Any]], str]:
    """Returns (variant, ordered entries, decision).

    The best page for this posting is built fresh, then every variant already
    in the library is scored against the same posting by the same measure. An
    existing one is reused only if nothing fresh beats it; anything better
    becomes a new variant.

    A variant that has been sent is never edited. Reuse used to rewrite its
    entries and grow its skills in place, so the record of what went to one
    employer stopped being what went to them.
    """
    kw = posting_keywords(posting, index)
    scorer = Scorer(kw, index)
    approved = list(((store.get("identity") or {}).get("title") or {})
                    .get("approved_descriptors") or [])

    scorer.reach((e for e in (store.get("entries") or []) if emittable(e)), approved)

    entries = select_entries(store, index, kw, scorer=scorer)
    surfaces = surfaced_skills(entries, index, kw.claimable, weights=kw.weights)
    descriptor = best_descriptor(approved, kw)
    fresh = scorer.score(entries, surfaces, descriptor)

    # Compared without summaries: the fresh page has none until a model writes
    # one, and that happens only if it is kept.
    variants = load_library(prof)
    rival: tuple[Variant, list[dict[str, Any]], Match] | None = None
    # A pin is the person's call, not the posting's, so the match cannot see it: a
    # stored variant without Claude Code ties a fresh one with it on any posting
    # that never mentions it, and would be reused. Missing a pin rules it out.
    pinned = set(index.always) & set(surfaces)
    for v in variants:
        if not pinned <= set(v.surfaces):
            continue
        own = materialize(store, v.emits)
        m = scorer.score(own, v.surfaces, v.title_descriptor)
        if rival is None or m.key > rival[2].key:
            rival = (v, own, m)

    if rival and fresh.key <= rival[2].key:
        variant, entries = rival[0], rival[1]
        decision = (f"reuse {variant.id}: matches {pct(rival[2])} of the posting; "
                    f"a fresh selection does no better ({pct(fresh)})")
    else:
        vid, angle = name_for(entries, scorer, fresh)
        variant = Variant(
            id=unique_id(vid, (v.id for v in variants)),
            angle=angle,
            emits=[e["id"] for e in entries],
            surfaces=surfaces,
            title_descriptor=descriptor,
        )
        variants.append(variant)
        decision = f"new variant: matches {pct(fresh)} of the posting" + (
            f", where the best existing one ({rival[0].id}) matches {pct(rival[2])}"
            if rival else "")

    if use_model and not variant.summary:
        # Grouped by role, not flattened. A flat list of facts from four
        # different jobs let the model attribute "second-in-command" to the
        # wrong one - the same structure-losing mistake as the truncated
        # profile digest in pass 3, one module later.
        roles: list[str] = []
        facts: list[str] = []
        for e in entries[:4]:
            if e.get("type") not in ("work", "project"):
                continue
            own = [str(f) for f in (e.get("facts") or [])[:3]]
            own += [m["claim"] for m in (e.get("metrics") or []) if m.get("measured")]
            header = (f"ROLE: {e.get('headline')} "
                      f"({e.get('where')}, {e.get('period') or 'n/a'})")
            roles.append(header + "\n" + "\n".join(f"  - {f}" for f in own))
            facts.extend(own)
        summary, by = write_summary(llm or LLM(prof, purpose="resume"), roles,
                                    posting.get("title", "software engineer"))
        if summary:
            ok, why = summary_is_grounded(summary, facts)
            if ok:
                variant.summary, variant.summary_by = summary, by
            else:
                decision += f"; summary rejected ({why})"

    variant.sent_to.append({
        "posting_id": posting.get("id"),
        "company": posting.get("company"),
        "role": posting.get("title"),
        "date": utcnow()[:10],
        "coverage": posting.get("coverage"),
        "ats": scorer.score(entries, variant.surfaces, variant.title_descriptor,
                            variant.summary).to_dict(),
    })
    save_library(prof, variants)
    return variant, entries, decision


def load_store(prof: Profile) -> dict[str, Any]:
    return yaml.safe_load(prof.file("master-store.yaml").read_text(encoding="utf-8")) or {}
