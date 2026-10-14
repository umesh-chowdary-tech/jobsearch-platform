"""The skills layer: what a person can claim, and what a posting is asking for.

Two vocabularies, deliberately separate.

`skills.yaml` (the person's, in their profile) says what may go under Skills -
tiered by how confidently it can be defended. `skill-vocabulary.yaml` (shared,
in data/) says what a posting might name. A term
found in a posting that is absent from the first is a gap, which is the entire
point of counting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

import yaml

if TYPE_CHECKING:
    from .profile import Profile

CORE = "core"
WORKING = "working"
SCOPED = "scoped"
INCIDENTAL = "incidental"
BANNED = "banned"
NOT_A_SKILL = "not_a_skill"
UNBACKED = "unbacked"        # a real skill, just not one of this person's


@dataclass(frozen=True)
class Claim:
    """What the store says about one skill."""

    skill: str
    tier: str
    scope: str | None = None
    safe_phrasing: str | None = None

    @property
    def emittable(self) -> bool:
        return self.tier in (CORE, WORKING, SCOPED)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+#.]+", "", (s or "").lower())


class SkillIndex:
    """Canonical names, aliases, and what tier each one is claimable at."""

    def __init__(self, skills_doc: dict[str, Any], vocab_doc: dict[str, Any]) -> None:
        self.claims: dict[str, Claim] = {}
        self._ingest_claims(skills_doc)
        # Pinned: on every resume's Skills line when an emitted entry backs them.
        self.always: list[str] = [_strip_paren(s) for s in (skills_doc or {}).get("always") or []]

        # canonical -> set of surface forms to look for in posting text
        self.aliases: dict[str, set[str]] = {}
        self.category: dict[str, str] = {}
        # Table stakes. Detected, so a posting naming them parses correctly, but
        # never counted as a gap: "learn Git" is not a plan, and leaving these in
        # would crowd the real gaps off the top of the ledger.
        self.baseline: set[str] = set((vocab_doc or {}).get("baseline") or {})
        self.implies: dict[str, list[str]] = {
            k: list(v or []) for k, v in ((vocab_doc or {}).get("implies") or {}).items()
        }

        for category, entries in (vocab_doc or {}).items():
            if category == "implies":
                continue
            for canonical, forms in (entries or {}).items():
                names = {canonical, *(forms or [])}
                self.aliases.setdefault(canonical, set()).update(
                    n.lower() for n in names if n
                )
                self.category.setdefault(canonical, category)

        # Everything the store backs must also be detectable, or a posting
        # naming it would be scored as a gap against the person.
        for name in self.claims:
            self.aliases.setdefault(name, {name.lower()})
            self.category.setdefault(name, "other")

        self._norm_to_canonical = {}
        for canonical, forms in self.aliases.items():
            for f in forms:
                self._norm_to_canonical.setdefault(_norm(f), canonical)

    # ── loading skills.yaml ──────────────────────────────────────────────────

    def _ingest_claims(self, doc: dict[str, Any]) -> None:
        tiers = (doc or {}).get("tiers") or {}

        for item in tiers.get("core") or []:
            name = item["skill"] if isinstance(item, dict) else item
            self.claims[_strip_paren(name)] = Claim(_strip_paren(name), CORE)

        for _cat, items in (tiers.get("working") or {}).items():
            for item in items or []:
                if isinstance(item, dict):
                    # A scoped skill: real, but narrower than the bare word.
                    self.claims[item["skill"]] = Claim(
                        item["skill"], SCOPED,
                        scope=item.get("scope"),
                        safe_phrasing=item.get("safe_phrasing"),
                    )
                else:
                    self.claims[item] = Claim(item, WORKING)

        for item in tiers.get("incidental") or []:
            self.claims[item["skill"]] = Claim(
                item["skill"], INCIDENTAL, safe_phrasing=item.get("safe_phrasing")
            )

        for item in (doc or {}).get("not_skills") or []:
            self.claims[item["item"]] = Claim(item["item"], NOT_A_SKILL)

        for item in (doc or {}).get("banned") or []:
            self.claims[item["skill"]] = Claim(item["skill"], BANNED)

    # ── querying ─────────────────────────────────────────────────────────────

    def canonical(self, term: str) -> str | None:
        return self._norm_to_canonical.get(_norm(term))

    def tier(self, canonical: str) -> str:
        c = self.claims.get(canonical)
        return c.tier if c else UNBACKED

    def claim(self, canonical: str) -> Claim | None:
        return self.claims.get(canonical)

    # Terms that are also ordinary English. Matched case-sensitively against the
    # original text, because "we spark joy" is not a request for Apache Spark -
    # and a false positive here does not just mis-score one posting, it puts a
    # technology nobody asked for at the top of the gap ledger.
    AMBIGUOUS = {"Spark", "Swift", "Rust", "Flask", "Go", "React"}

    def detect(self, text: str) -> set[str]:
        """Canonical skills named anywhere in this text.

        Whole-word, case-insensitive except for `AMBIGUOUS`. Longer aliases are
        matched first so that "react native" does not also register as "react" -
        a posting wanting a mobile engineer is not asking for a web one.
        """
        if not text:
            return set()
        haystack = text.lower()
        found_ambiguous = {
            term for term in self.AMBIGUOUS
            if re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", text)
        }
        found: set[str] = set()
        consumed: list[tuple[int, int]] = []

        pairs = sorted(
            ((alias, canon) for canon, forms in self.aliases.items() for alias in forms),
            key=lambda p: -len(p[0]),
        )
        for alias, canon in pairs:
            if len(alias) < 3:
                continue
            # An ambiguous canonical only counts if it appeared capitalised, or
            # via an unambiguous alias ("apache spark", "pyspark", "golang").
            if canon in self.AMBIGUOUS and alias == canon.lower():
                if canon not in found_ambiguous:
                    continue
            for m in re.finditer(rf"(?<![\w]){re.escape(alias)}(?![\w])", haystack):
                span = m.span()
                # Skip an occurrence already covered by a longer alias, but keep
                # scanning: "React Native ... also React" is both, and stopping
                # at the first hit would lose the standalone mention.
                if any(s <= span[0] and span[1] <= e for s, e in consumed):
                    continue
                consumed.append(span)
                found.add(canon)
        for canon in list(found):
            found.update(self.implies.get(canon, []))
        return found

    def emittable_for(self, tech_tags: Iterable[str]) -> list[Claim]:
        """Given a store entry's `tech`, what may actually be listed."""
        out = []
        for t in tech_tags:
            canon = self.canonical(t) or t
            c = self.claims.get(canon)
            if c and c.emittable:
                out.append(c)
        return out


def _strip_paren(name: str) -> str:
    """'MCP (Model Context Protocol)' -> 'MCP'."""
    return re.sub(r"\s*\(.*\)\s*$", "", name).strip()


def load_index(prof: "Profile") -> SkillIndex:
    """The person's claims over the shared vocabulary. Re-read when either
    file changes: a long-running service sees a profile saved a moment ago."""
    skills, vocab = prof.file("skills.yaml"), prof.data / "skill-vocabulary.yaml"
    return _load_index(str(skills), str(vocab), _stamp(skills), _stamp(vocab))


def _stamp(p: Path) -> tuple[int, int]:
    try:
        st = p.stat()
    except OSError:
        return (0, 0)
    return (st.st_mtime_ns, st.st_size)


@lru_cache(maxsize=16)
def _load_index(skills_path: str, vocab_path: str, *_stamps: tuple[int, int]) -> SkillIndex:
    skills = yaml.safe_load(Path(skills_path).read_text(encoding="utf-8")) or {}
    vocab = yaml.safe_load(Path(vocab_path).read_text(encoding="utf-8")) or {}
    return SkillIndex(skills, vocab)
