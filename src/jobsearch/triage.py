"""Module 3 — triage.

Ranks new postings against the store, explains itself, and stops. It never
decides. Passes 1 and 2 are deterministic and free; pass 3 (a model reading the
shortlist) is not built yet.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from .models import utcnow
from .pool import SHRINK_DAYS, posted_day
from .skills import CORE, INCIDENTAL, SCOPED, UNBACKED, WORKING, SkillIndex

REJECT = "reject"     # failed a hard filter: wrong function, place or level
NO_MATCH = "no_match" # an engineering role, but nothing in the store backs it
FLAG = "flag"         # applyable, with a caveat worth reading
KEEP = "keep"


# ── requirement sections ─────────────────────────────────────────────────────

_NICE_HEADINGS = re.compile(
    r"(nice[- ]to[- ]have|bonus|good to have|preferred qualifications|"
    r"plus(?:es)?\b|desirable|it'?s a plus|extra credit|"
    r"what would (?:be|make) )",
    re.I,
)


def split_requirements(text: str) -> tuple[str, str]:
    """Split a description into (required, nice-to-have).

    Crude on purpose. A skill named in Requirements is a different signal from
    one named under Bonus Points, and counting them together lets wishlists
    drown the gap ledger. When no heading is found, everything is required -
    the conservative reading.
    """
    if not text:
        return "", ""
    m = _NICE_HEADINGS.search(text)
    if not m:
        return text, ""
    return text[: m.start()], text[m.start():]


# ── pass 1: hard filters ─────────────────────────────────────────────────────

_YEARS = re.compile(
    # (?<!\d)...(?!\d) so "100 years" is not read as 10.
    r"(?<!\d)(\d{1,2})(?!\d)\s*(?:\+|plus)?\s*(?:[-–]|to)?\s*(?:\d{1,2})?\s*\+?\s*"
    # The "experience" qualifier is a LOOKAHEAD, not consumed. Consuming it made
    # "8+ years overall, 3+ years with Python experience" match only the 8,
    # because the first match ate the text the second one needed.
    # 80, not 45: Amazon's "3+ years of non-internship professional software
    # development experience" puts it 52 characters on, and 185 Amazon
    # postings read as "no years stated".
    r"(?:years?|yrs?)\b(?=[^.]{0,80}?(?:experience|exp\b))",
    re.I,
)


# The label first: "Experience: 10 + Years", "Exp - 8 to 12 yrs". _YEARS wants
# "experience" after the number, so a "Lead Agentic AI Engineer ...
# Experience: 10 + Years" posting read as "no years stated", passed, and was
# ranked a strong fit for three years of experience.
_YEARS_LABELLED = re.compile(
    r"\b(?:experience|exp)\b\s*(?:required|needed|level)?\s*[:\-–]?\s*"
    r"(?<!\d)(\d{1,2})(?!\d)\s*(?:\+|plus)?\s*(?:(?:[-–]|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b",
    re.I,
)


def required_years(text: str) -> int | None:
    """The smallest stated minimum. Postings often list several; the lowest is
    the one that decides whether it is worth applying."""
    mins = []
    for m in [*_YEARS.finditer(text or ""), *_YEARS_LABELLED.finditer(text or "")]:
        lo = int(m.group(1))
        if 0 < lo <= 30:
            mins.append(lo)
    return min(mins) if mins else None


def _any_in(text: str, terms: Iterable[str]) -> str | None:
    low = (text or "").lower()
    for t in terms:
        if t.lower() in low:
            return t
    return None


# How much of title + description the location check reads for a "remote".
LOCATION_WINDOW = 2000

# How much description a stored result keeps. At least LOCATION_WINDOW, or a
# recheck of the stored record would be blind to a "remote" that pass 1 saw.
EXCERPT_CHARS = LOCATION_WINDOW
# Results written before 2026-09-24 kept this much, and no `excerpt_chars`.
_OLD_EXCERPT_CHARS = 1600


# Where a posting says what it requires. Most put it after the pitch: Amazon's
# "Basic qualifications" come 5,000 characters in, so an excerpt of the first
# 2,000 kept the team blurb and dropped the years and degrees - and the model
# ranked an Applied Scientist role (PhD or Master's) strong for a B.Tech.
_REQUIREMENTS = re.compile(
    r"(?:basic|minimum|required|preferred)\s+qualifications|qualifications\s*(?:&|and)\s*(?:skills|experience)|"
    r"\bqualifications\b\s*[:\n]|\brequirements\b\s*[:\n]|what you(?:'ll)?\s+need|what we(?:'re)?\s+looking for|"
    r"who you are|you (?:should )?have\b\s*[:\n]|must[- ]haves?\b|skills (?:and|&) experience|"
    r"experience required|required skills",
    re.I,
)
REQUIREMENTS_CHARS = 2500


def requirements_excerpt(text: str) -> str:
    """The requirements section, from its first heading - or "" if none is
    named. Stored beside the head of the description, never instead of it."""
    m = _REQUIREMENTS.search(text or "")
    return (text[m.start():m.start() + REQUIREMENTS_CHARS]).strip() if m else ""


def _place_in(location: str, places: Iterable[str]) -> str | None:
    """_any_in, but whole words only. "India" is a substring of "Indiana", and
    "Chicago, Illinois; Indiana" was passing as an acceptable location."""
    for p in places:
        if re.search(rf"(?<!\w){re.escape(p)}(?!\w)", location or "", re.I):
            return p
    return None


# Words a location field uses to say HOW a role is worked, not WHERE.
_PLACELESS = frozenset({"remote", "hybrid", "distributed", "anywhere", "global",
                        "globally", "worldwide", "fully", "full", "time", "first",
                        "friendly", "flexible"})
# Connectives only count BETWEEN two words: in "NYC or Remote" OR joins two
# options, in "Remote - OR" it is Oregon. IN and Indiana likewise.
_CONNECTIVES = re.compile(r"(?<=\w)(?:\s+(?:or|and|in|from|the|within))+\s+(?=\w)", re.I)
_WORDS = re.compile(r"[^\W\d_]+")


def _places_named(location: str, remote_terms: Iterable[str]) -> list[str]:
    """The words of a location field left once the remote words, filler and
    connectives are gone - that is, the places it names.

    An allowlist, for the reason require_title_signal is one: the places a
    remote role can be tied to are unbounded ("Remote - CA", "Remote, KSA",
    "Remote - Houston", fifty US states and their abbreviations), while the
    places that suit are a short list. Anything left over counts as a place.
    """
    filler = _PLACELESS | {w for t in remote_terms for w in _WORDS.findall(t.lower())}
    return [w for w in _WORDS.findall(_CONNECTIVES.sub(" ", location or "").lower())
            if w not in filler]


def location_verdict(location: str, text: str, profile: dict) -> tuple[bool, str]:
    loc = profile.get("location") or {}
    remote_terms = loc.get("remote_terms") or []

    # A named Indian place settles it, remote or not: "Remote - India" suits, and
    # so does "Remote, India; Remote, Poland".
    hit = _place_in(location, loc.get("preferred") or [])
    if hit:
        return True, f"preferred location ({hit})"
    hit = _place_in(location, loc.get("acceptable") or [])
    if hit:
        return True, f"acceptable location ({hit})"

    # Remote counts only when it is not tied to a place. "Remote, Austria",
    # "Remote - CA" and "Remote, Canada; Remote, United States" all sat on the
    # dashboard as keeps once: remote from there, not from where the person is.
    elsewhere = _places_named(location, remote_terms)
    if _any_in(location, remote_terms):
        if not elsewhere:
            return True, "remote"
        return False, ("remote, but tied to a place the profile does not list"
                       f" - location is {location}")

    # The description is read only when the location field names no place at
    # all (blank, "Hybrid"). "San Francisco" with "fully remote" in the body
    # means remote within the US, as nearly every posting phrased that way does.
    if not elsewhere and _any_in(text[:LOCATION_WINDOW], remote_terms):
        return True, "remote (from the description)"

    hit = _place_in(location, loc.get("reject_if_only") or [])
    if hit:
        return False, f"location is {location or 'unstated'} - outside India, not remote"
    return False, f"location {location or 'unstated'} does not match the profile"


def seniority_verdict(title: str, text: str, profile: dict) -> tuple[str, str]:
    return _seniority_by_title(title, profile) or _seniority_by_years(
        required_years(text), profile
    )


def _seniority_by_title(title: str, profile: dict) -> tuple[str, str] | None:
    sen = profile.get("seniority") or {}
    low_title = (title or "").lower()

    # Whole words. As a substring, "lead engineer" missed "Lead AIML Engineer",
    # "Lead Software Engineer" and "Senior Lead ..." - 20 of them were ranked
    # strong for three years of experience.
    hit = _place_in(low_title, [t.strip() for t in sen.get("reject_titles") or []])
    if hit:
        return REJECT, f"title says '{hit.strip()}' - above the current level"

    hit = _place_in(low_title, [t.strip() for t in sen.get("flag_titles") or []])
    if hit:
        return FLAG, f"title says '{hit.strip()}' - below the current level"
    return None


def _seniority_by_years(years: int | None, profile: dict) -> tuple[str, str]:
    cap = int((profile.get("seniority") or {}).get("max_years_required", 99))
    if years is not None and years > cap:
        return REJECT, f"asks for {years}+ years, profile caps at {cap}"
    return KEEP, f"{years}+ years asked" if years else "no years stated"


def function_verdict(title: str, profile: dict) -> tuple[bool, str]:
    """Blocklist first, then an allowlist on the title.

    The blocklist catches "Sales Engineer" - a title that carries an
    engineering word but is not an engineering job. The allowlist catches
    everything the blocklist could never enumerate.
    """
    hit = _any_in(title, profile.get("reject_function") or [])
    if hit:
        return False, f"'{hit.strip()}' role - not software engineering"

    signals = profile.get("require_title_signal") or []
    if signals and not _any_in(title, signals):
        return False, "title carries no engineering signal"
    return True, ""


# ── pass 2: coverage ─────────────────────────────────────────────────────────

@dataclass
class Coverage:
    named: set[str] = field(default_factory=set)
    matched: dict[str, str] = field(default_factory=dict)   # canonical -> tier
    absent: set[str] = field(default_factory=set)
    shallow: set[str] = field(default_factory=set)
    score: int = 0

    @property
    def fraction(self) -> str:
        return f"{len(self.matched)}/{len(self.named)}" if self.named else "0/0"


def score_posting(index: SkillIndex, text: str, profile: dict) -> Coverage:
    weights = ((profile.get("scoring") or {}).get("weights") or {})
    cov = Coverage()
    cov.named = index.detect(text)

    for canon in cov.named:
        if canon in index.baseline:
            continue          # table stakes: never a gap, never a differentiator
        tier = index.tier(canon)
        if tier in (CORE, WORKING, SCOPED):
            cov.matched[canon] = tier
            cov.score += int(weights.get(tier, 0))
            if tier == SCOPED:
                cov.shallow.add(canon)
        elif tier == INCIDENTAL:
            # Scores zero by design. If Azure in a posting earned points, the
            # whole skills layer would have been pointless.
            cov.shallow.add(canon)
        elif tier == UNBACKED:
            cov.absent.add(canon)
        # BANNED and NOT_A_SKILL contribute nothing and are not gaps to chase.
    return cov


# ── a triaged posting ────────────────────────────────────────────────────────

@dataclass
class Triaged:
    posting: dict[str, Any]
    verdict: str
    reasons: list[str] = field(default_factory=list)
    coverage: Coverage | None = None
    required_gaps: set[str] = field(default_factory=set)
    nice_gaps: set[str] = field(default_factory=set)
    # The offline match score (scoring.py), set after triage.
    match: int = 0
    match_parts: dict[str, int] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.posting.get("title", "")

    @property
    def company(self) -> str:
        return self.posting.get("company", "")

    @property
    def score(self) -> int:
        return self.coverage.score if self.coverage else 0


def rejected_unread(profile: dict, posting: dict[str, Any]) -> Triaged | None:
    """The rejection triage_posting would give without the description: the
    title's function, the title's level, or a location field that names a
    place - the location check reads the description only when the field names
    none. None when the description could still matter. So a description is
    fetched only for a posting that could pass, and the verdict is the same."""
    title, location = posting.get("title", ""), posting.get("location", "")
    ok, why = function_verdict(title, profile)
    if not ok:
        return Triaged(posting, REJECT, [why])
    remote = (profile.get("location") or {}).get("remote_terms") or []
    ok, why = location_verdict(location, "", profile)
    if not ok and _places_named(location, remote):
        return Triaged(posting, REJECT, [why])
    level = _seniority_by_title(title, profile)
    if level and level[0] == REJECT:
        # The same reasons, in the same order, as triage_posting's: the last
        # one is what the rejection tally counts.
        return Triaged(posting, REJECT, [why, level[1]] if ok else [level[1]])
    return None


def triage_posting(index: SkillIndex, profile: dict, posting: dict[str, Any]) -> Triaged:
    title = posting.get("title", "")
    location = posting.get("location", "")
    text = f"{title}\n{posting.get('description', '')}"

    ok, why = function_verdict(title, profile)
    if not ok:
        return Triaged(posting, REJECT, [why])

    ok, why = location_verdict(location, text, profile)
    if not ok:
        return Triaged(posting, REJECT, [why])
    reasons = [why]

    verdict, why = seniority_verdict(title, text, profile)
    reasons.append(why)
    if verdict == REJECT:
        return Triaged(posting, REJECT, reasons)

    cov = score_posting(index, text, profile)

    required_text, nice_text = split_requirements(text)
    req_named = index.detect(required_text)
    nice_named = index.detect(nice_text)
    gaps = cov.absent | cov.shallow
    t = Triaged(posting, verdict, reasons, cov)
    t.required_gaps = gaps & req_named
    t.nice_gaps = (gaps & nice_named) - t.required_gaps

    # A posting that names no technology at all is not an engineering role,
    # whatever its title says. This catches "Cluster Head - Last Mile" and
    # "Senior Talent Partner" without needing every such title enumerated.
    if not cov.named:
        t.verdict = REJECT
        t.reasons.append("names no technology - not an engineering role")
        return t

    # Names technology, but nothing the store backs. Not applyable, but it is
    # the single most informative case for the gap ledger, so it is kept apart
    # from a hard-filter rejection rather than lumped in with it.
    if not cov.matched:
        t.verdict = NO_MATCH
        t.reasons.append("no backed skill named")
    return t


# ── the gap ledger ───────────────────────────────────────────────────────────

def update_gap_ledger(var_dir: Path, index: SkillIndex, results: Iterable[Triaged]) -> Path:
    """Two lists, because 'never touched it' and 'touched it, cannot claim it'
    imply very different amounts of a weekend."""
    path = Path(var_dir) / "skill-gaps.json"
    ledger: dict[str, Any] = {"absent": {}, "shallow": {}}
    if path.exists():
        try:
            ledger = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    ledger.setdefault("absent", {})
    ledger.setdefault("shallow", {})

    for t in results:
        # Hard-filter rejections are excluded: the wrong city or the wrong
        # level tells you nothing about which skills to learn. NO_MATCH is
        # included, because it is exactly the 'not qualified for this' signal.
        if t.verdict == REJECT or not t.coverage:
            continue
        for skill in t.required_gaps | t.nice_gaps:
            bucket = "shallow" if skill in t.coverage.shallow else "absent"
            row = ledger[bucket].setdefault(
                skill, {"required_count": 0, "nice_to_have_count": 0,
                        "first_seen": t.posting.get("first_seen"), "last_seen": None}
            )
            if skill in t.required_gaps:
                row["required_count"] += 1
            else:
                row["nice_to_have_count"] += 1
            row["last_seen"] = t.posting.get("first_seen")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")
    return path


def write_results(var_dir: Path, results: Iterable[Triaged], fresh_tally: bool = False) -> Path:
    """Persist triaged postings for the dashboard.

    Merged by posting id, not replaced: `triage --board lever:meesho` looks at
    five boards' worth of nothing and would otherwise wipe what the queue run
    found. Same lesson as boards.json in Module 1. `fresh_tally` starts the
    rejection counts again - for a re-read of everything after the profile
    changed, which would otherwise count every posting twice.
    """
    path = Path(var_dir) / "triage-results.json"
    blob: dict[str, Any] = {"postings": {}, "reject_reasons": {}, "last_run": None}
    if path.exists():
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    blob.setdefault("postings", {})
    blob.setdefault("reject_reasons", {})
    if fresh_tally:
        blob["reject_reasons"] = {}
    now = utcnow()

    for t in results:
        pid = t.posting.get("id")
        if not pid:
            continue
        if t.verdict == REJECT:
            # Rejections are kept as a tally only. Storing thousands of them
            # would bury the handful that matter, and the reason is the only
            # part worth reading - it is how the filters get tuned.
            reason = t.reasons[-1] if t.reasons else "unknown"
            key = reason.split(" - ")[0][:60]
            blob["reject_reasons"][key] = blob["reject_reasons"].get(key, 0) + 1
            kept = blob["postings"].get(pid)
            if kept is not None:
                # Read before and kept then: the person may have acted on it,
                # and outreach refers to it by id. Marked, as the recheck does.
                kept.update({"verdict": REJECT, "reasons": t.reasons, "triaged_at": now,
                             "match": 0, "match_parts": {}})
            continue
        cov = t.coverage
        description = t.posting.get("description") or ""
        previous = blob["postings"].get(pid) or {}
        record = {
            "id": pid,
            # When the watcher first saw it, kept across re-triage: a recheck
            # reads the board again and has no stamp of its own.
            "first_seen": t.posting.get("first_seen") or previous.get("first_seen"),
            # What the board says; the pool's age window counts from it.
            "posted_at": (posted_day(t.posting.get("posted_at")) or posted_day(t.posting.get("updated_at"))
                          or previous.get("posted_at")),
            "title": t.title,
            "company": t.company,
            "location": t.posting.get("location", ""),
            "url": t.posting.get("url", ""),
            "board": t.posting.get("board", ""),
            # A bounded excerpt, so pass 3 can read the posting without
            # re-fetching it and the dashboard has something to show. Full
            # descriptions for thousands of postings would be a cache, and a
            # cache is a thing that goes stale. It covers LOCATION_WINDOW so a
            # recheck sees every "remote" pass 1 saw.
            "description": description[:EXCERPT_CHARS],
            "excerpt_chars": EXCERPT_CHARS,
            # What the posting requires, wherever it sits - the excerpt above
            # is the head, and requirements are usually past it.
            "requirements": requirements_excerpt(description),
            # From the whole description, which is not kept. Read from the
            # excerpt instead, "8+ years overall" could stand in for the
            # "3+ years with Python" that was cut off after it.
            "years_required": required_years(f"{t.title}\n{description}"),
            "triaged_at": now,
            "verdict": t.verdict,
            "score": t.score,
            "role": t.posting.get("role") or "",
            "match": t.match,
            "match_parts": t.match_parts,
            "coverage": cov.fraction if cov else "0/0",
            "matched": cov.matched if cov else {},
            "required_gaps": sorted(t.required_gaps),
            "nice_gaps": sorted(t.nice_gaps),
            "reasons": t.reasons,
        }
        # What later steps added. Re-triaging re-derives the verdict; it has
        # no business discarding the model's judgement or the watcher's word
        # that the posting has closed.
        for key in ("ai", "closed_at"):
            if key in previous:
                record[key] = previous[key]
        blob["postings"][pid] = record

    blob["last_run"] = now
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(blob, indent=2, sort_keys=True), encoding="utf-8")
    return path


# ── recheck: pass 1 again, over what is already stored ──────────────────────
#
# A profile change only reaches postings triaged after it. `triage --all` is
# not the fix: it re-reads only the watcher's queue, re-counts the gap ledger
# and the reject tallies, and rewrites whole records.

def _years_pass1_read(rec: dict[str, Any]) -> tuple[bool, int | None]:
    """(known, years): the minimum pass 1 read from the WHOLE description."""
    if "years_required" in rec:
        return True, rec["years_required"]
    # Written before years_required existed. Pass 1's own reason is the only
    # record of what it read.
    for reason in rec.get("reasons") or []:
        m = re.fullmatch(r"(\d+)\+ years asked", reason)
        if m:
            return True, int(m.group(1))
        if reason == "no years stated":
            return True, None
    return False, None      # the title decided seniority; years were never read


def _excerpt_holds_location_window(rec: dict[str, Any]) -> bool:
    kept = int(rec.get("excerpt_chars") or _OLD_EXCERPT_CHARS)
    # Either the cap covered the window, or the description was shorter than
    # the cap and is here whole. Exactly `kept` long means it may have been cut.
    return kept >= LOCATION_WINDOW or len(rec.get("description") or "") < kept


def _description_could_pass(rec: dict[str, Any], profile: dict) -> bool:
    """Whether any description could have made this location pass.

    Asked of location_verdict itself rather than restating its rules: when it
    says no even to a description reading "remote", the text cut from the
    excerpt cannot have mattered.
    """
    terms = (profile.get("location") or {}).get("remote_terms") or []
    title = rec.get("title", "")
    return any(location_verdict(rec.get("location", ""), f"{title}\n{t}", profile)[0]
               for t in terms)


def recheck_record(rec: dict[str, Any], profile: dict) -> tuple[str, list[str], str | None]:
    """(verdict, reasons, unsure) for a stored result under the current profile.

    Pass 1 only, from what the record kept. Pass 2 is not re-run: scoring an
    excerpt would under-count every posting and call that a change. The one
    pass-2 outcome a stored record can carry - nothing backed - is read from
    its `matched`. A change to skills.yaml needs a re-triage.

    The excerpt can prove that a check passes, but not always that it fails.
    When it fails on text that may have been cut off, the verdict stays as it
    was and `unsure` says why. A recheck that rejected a posting because it had
    stored too little would be the bug it exists to fix.
    """
    title = rec.get("title", "")
    was_verdict, was_reasons = rec.get("verdict", KEEP), list(rec.get("reasons") or [])

    ok, why = function_verdict(title, profile)
    if not ok:
        return REJECT, [why], None

    text = f"{title}\n{rec.get('description') or ''}"
    ok, where = location_verdict(rec.get("location", ""), text, profile)
    if not ok:
        if not _excerpt_holds_location_window(rec) and _description_could_pass(rec, profile):
            return was_verdict, was_reasons, (
                f"{where}, but the stored excerpt is shorter than what pass 1 read")
        return REJECT, [where], None

    decided = _seniority_by_title(title, profile)
    if decided is None:
        known, years = _years_pass1_read(rec)
        if not known:
            return was_verdict, was_reasons, (
                "years were never recorded, and the excerpt may not hold them")
        decided = _seniority_by_years(years, profile)
    verdict, why = decided
    reasons = [where, why]
    if verdict == REJECT:
        return REJECT, reasons, None
    if not rec.get("matched"):
        return NO_MATCH, reasons + ["no backed skill named"], None
    return verdict, reasons, None


@dataclass
class RecheckReport:
    checked: int = 0
    changed: list[tuple[dict, str, str, list[str]]] = field(default_factory=list)
    unsure: list[tuple[dict, str]] = field(default_factory=list)
    closed: list[dict] = field(default_factory=list)
    reopened: list[dict] = field(default_factory=list)
    unknown: list[dict] = field(default_factory=list)   # the watcher cannot say
    written: bool = False


def recheck_results(
    var_dir: Path,
    profile: dict,
    live: Callable[[str, str, str | None], bool | None],
    write: bool = False,
) -> RecheckReport:
    """Re-apply pass 1 to every stored result, and mark the ones that closed.

    `live(board, id, triaged_at)` answers from the watcher's state on disk, so
    nothing here touches the network. A changed record keeps every field but
    `verdict`, `reasons` and `closed_at` - its `ai` judgement included. A
    rejected one stays in the file, marked, because outreach may still refer
    to it by id. The gap ledger and the reject tallies are never read or
    written. Nothing is written unless `write`.
    """
    path = Path(var_dir) / "triage-results.json"
    report = RecheckReport()
    if not path.exists():
        return report
    # No fallback to an empty blob here: writing that back would wipe every
    # stored verdict. A file that does not parse stops the recheck instead.
    blob = json.loads(path.read_text(encoding="utf-8"))
    now = utcnow()
    patches: dict[str, dict[str, Any]] = {}

    for pid, rec in (blob.get("postings") or {}).items():
        report.checked += 1
        patch: dict[str, Any] = {}

        verdict, reasons, unsure = recheck_record(rec, profile)
        if unsure:
            report.unsure.append((rec, unsure))
        was = rec.get("verdict", KEEP)
        if verdict != was:
            report.changed.append((rec, was, verdict, reasons))
            patch.update(verdict=verdict, reasons=reasons,
                         recheck={"at": now, "was": was})

        state = live(rec.get("board", ""), pid, rec.get("triaged_at"))
        if state is None:
            report.unknown.append(rec)
        elif state is False and not rec.get("closed_at"):
            report.closed.append(rec)
            patch["closed_at"] = now
        elif state is True and rec.get("closed_at"):
            report.reopened.append(rec)
            patch["closed_at"] = None

        if patch:
            patches[pid] = patch

    if write and patches:
        for pid, patch in patches.items():
            rec = blob["postings"][pid]
            for key, value in patch.items():
                if value is None:
                    rec.pop(key, None)
                else:
                    rec[key] = value
        blob["last_recheck"] = now
        _write_json_atomic(path, blob)
        report.written = True
    return report


# ── closed on the board: the watcher's word, copied every cycle ─────────────
#
# The watcher knew within one poll that a posting had left its board, but only
# `triage --recheck --write` copied that onto the stored result, and every list
# hides by `closed_at`. One posting was gone from its board for two days and
# still on Today. This changes `closed_at` and nothing else, so unlike the recheck it
# is safe to run unattended: it cannot move a verdict.

def sync_closed(
    var_dir: Path,
    live: Callable[[str, str, str | None], bool | None],
) -> tuple[list[str], list[str]]:
    """Mark the stored postings the last poll no longer lists, and unmark the
    ones it lists again. Returns (closed, reopened) ids. A posting the watcher
    cannot speak for is left as it is, and nothing is written when nothing
    changed."""
    path = Path(var_dir) / "triage-results.json"
    if not path.exists():
        return [], []
    # As in the recheck: a file that does not parse stops here, never becomes
    # an empty blob written back over every verdict.
    blob = json.loads(path.read_text(encoding="utf-8"))
    now = utcnow()
    closed: list[str] = []
    reopened: list[str] = []
    for pid, rec in (blob.get("postings") or {}).items():
        state = live(rec.get("board", ""), pid, rec.get("triaged_at"))
        if state is False and not rec.get("closed_at"):
            rec["closed_at"] = now
            closed.append(pid)
        elif state is True and rec.get("closed_at"):
            rec.pop("closed_at")
            reopened.append(pid)
    if closed or reopened:
        _write_json_atomic(path, blob)
    return closed, reopened


def mark_closed(var_dir: Path, pid: str) -> bool:
    """One posting its board has just said is gone (pool.check_live)."""
    path = Path(var_dir) / "triage-results.json"
    if not path.exists():
        return False
    blob = json.loads(path.read_text(encoding="utf-8"))
    rec = (blob.get("postings") or {}).get(str(pid))
    if rec is None or rec.get("closed_at"):
        return False
    rec["closed_at"] = utcnow()
    _write_json_atomic(path, blob)
    return True


def sync_pool(var_dir: Path, posted: Callable[[str, str], str | None],
              now: datetime | None = None, days: int = SHRINK_DAYS) -> dict[str, int]:
    """Two chores against the shared pool, in one write. A record made before
    the pool kept dates takes its posting date from the pool. A posting closed
    more than `days` ago keeps its verdict and the model's judgement, and
    loses its description excerpt - the pool drops its own copy then too."""
    path = Path(var_dir) / "triage-results.json"
    if not path.exists():
        return {"dated": 0, "shrunk": 0}
    blob = json.loads(path.read_text(encoding="utf-8"))
    cut = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat(timespec="seconds")
    dated = shrunk = 0
    for pid, rec in (blob.get("postings") or {}).items():
        if not rec.get("posted_at"):
            day = posted(str(rec.get("board") or ""), pid)
            if day:
                rec["posted_at"] = day
                dated += 1
        if rec.get("closed_at") and rec["closed_at"] <= cut and ("description" in rec or "requirements" in rec):
            rec.pop("description", None)
            rec.pop("requirements", None)
            shrunk += 1
    if dated or shrunk:
        _write_json_atomic(path, blob)
    return {"dated": dated, "shrunk": shrunk}


def _write_json_atomic(path: Path, blob: Any) -> None:
    """Temp file in the same directory, then replace: a crash mid-write must
    not leave a truncated results file for the next run to read as empty."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(blob, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def top_gaps(var_dir: Path, limit: int = 15) -> list[tuple[str, str, int, int]]:
    """(bucket, skill, required_count, nice_count), most-required first."""
    path = Path(var_dir) / "skill-gaps.json"
    if not path.exists():
        return []
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    rows = [
        (bucket, skill, row.get("required_count", 0), row.get("nice_to_have_count", 0))
        for bucket in ("absent", "shallow")
        for skill, row in (ledger.get(bucket) or {}).items()
    ]
    # Sort by required_count. A skill named in twenty "nice to have" sections and
    # zero "requirements" is noise.
    rows.sort(key=lambda r: (-r[2], -r[3], r[1]))
    return rows[:limit]


def load_profile(profile_dir: Path) -> dict[str, Any]:
    """The person's search-profile.yaml: the hard filters."""
    return yaml.safe_load((Path(profile_dir) / "search-profile.yaml").read_text(encoding="utf-8")) or {}


def angle_for(store: dict[str, Any], index: SkillIndex, matched: Iterable[str]) -> str | None:
    """Which archetype this posting leans on, from the store's `angles` tags."""
    wanted = {m.lower() for m in matched}
    counts: Counter[str] = Counter()
    for entry in (store or {}).get("entries", []):
        tech = {index.canonical(t) or t for t in (entry.get("tech") or [])}
        if {t.lower() for t in tech} & wanted:
            for a in entry.get("angles") or []:
                if a != "all":
                    counts[a] += 1
    return counts.most_common(1)[0][0] if counts else None
