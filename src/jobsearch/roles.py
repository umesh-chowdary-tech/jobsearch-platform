"""What kind of job a posting is, from its title alone - offline, once, for
everyone (the pool stores it).

A role family is a coarse label: "backend", "data", "management". It drives
the role filter on the Jobs screen, the offline match score (scoring.py) and
the cheap skip before any description is fetched. It never rejects anything
on its own that the person's own title rules would keep: a title nothing here
recognises is "other", and "other" is left to those rules.

First match wins, so the order matters: "Data Engineer" is data before it is
software, "Engineering Manager, Data" is management before it is data, and
"Sales Engineer" is solutions before it is software.
"""
from __future__ import annotations

import re
from typing import Iterable

# (family, phrases). Phrases match whole words, case-insensitively.
RULES: list[tuple[str, list[str]]] = [
    ("management", ["engineering manager", "manager, engineering", "manager - engineering",
                    "software development manager", "manager, software", "head of engineering",
                    "director of engineering", "director, engineering", "engineering director",
                    "vp engineering", "vp of engineering", "vice president of engineering",
                    "vp, engineering", "cto", "chief technology officer"]),
    ("security", ["security", "appsec", "infosec", "penetration", "pentest", "soc analyst", "threat"]),
    ("qa", ["qa", "quality assurance", "quality engineer", "sdet", "test engineer", "engineer in test",
            "test automation", "automation tester", "tester", "testing", "performance test"]),
    ("solutions", ["sales engineer", "solutions engineer", "solution engineer", "solutions architect",
                   "solution architect", "presales", "pre-sales", "field engineer", "devrel",
                   "developer relations", "developer advocate", "developer evangelist",
                   "implementation engineer", "integration engineer"]),
    ("support", ["support engineer", "technical support", "customer engineer", "support specialist",
                 "application support", "production support"]),
    ("ml", ["machine learning", "ml", "ai", "artificial intelligence", "applied scientist",
            "research scientist", "deep learning", "nlp", "computer vision", "llm", "genai",
            "generative ai", "mlops"]),
    ("data", ["data engineer", "data scientist", "data science", "data analyst", "analytics engineer",
              "business intelligence", "bi developer", "bi engineer", "etl", "data platform",
              "data architect", "database", "dba", "decision scientist", "analytics"]),
    ("mobile", ["android", "ios", "mobile", "flutter", "react native"]),
    ("frontend", ["frontend", "front-end", "front end", "ui engineer", "ui developer",
                  "web developer", "react developer", "angular developer"]),
    ("fullstack", ["full stack", "full-stack", "fullstack"]),
    ("devops", ["devops", "site reliability", "sre", "infrastructure", "platform engineer",
                "cloud engineer", "release engineer", "build engineer", "reliability engineer",
                "kubernetes", "network engineer", "systems administrator", "sysadmin"]),
    ("embedded", ["embedded", "firmware", "hardware", "fpga", "asic", "vlsi", "silicon",
                  "verification engineer", "rtl", "electronics"]),
    ("backend", ["backend", "back-end", "back end", "server-side", "api engineer", "api developer",
                 "distributed systems", "java developer", "python developer", "golang", "go developer",
                 "node.js developer", "node developer", ".net developer", "php developer",
                 "ruby developer", "scala developer"]),
    ("design", ["designer", "ux", "user experience", "user researcher", "design lead"]),
    ("product", ["product manager", "product owner", "product lead", "head of product"]),
    ("program", ["program manager", "project manager", "programme manager", "scrum master",
                 "delivery manager"]),
    ("non-tech", ["sales", "marketing", "recruiter", "recruiting", "talent", "account executive",
                  "account manager", "customer success", "business development", "finance", "financial",
                  "accountant", "accounting", "legal", "counsel", "paralegal", "people partner",
                  "human resources", "hr", "payroll", "collections", "operations", "technical writer",
                  "content", "communications", "executive assistant", "administrative", "procurement",
                  "supply chain", "logistics", "customer experience", "customer service", "office manager",
                  "partnerships", "compliance", "audit", "tax", "treasury", "facilities"]),
    # Last: what is engineering but names no narrower craft.
    ("software", ["software", "engineer", "developer", "sde", "swe", "programmer",
                  "member of technical staff", "technical staff", "technologist", "architect",
                  "scientist"]),
]

FAMILIES = [family for family, _ in RULES] + ["other"]
# Families that are not engineering work. A posting in one of these is skipped
# before its description is fetched, unless the person's own title rules keep it.
NOT_ENGINEERING = {"non-tech", "design", "product", "program"}
# Setup's "roles to skip" (onboard.SKIP) and the families each one rules out.
SKIP_FAMILIES = {"qa": {"qa"}, "support": {"support"}, "data": {"data"}, "devrel": {"solutions"},
                 "mobile": {"mobile"}, "frontend": {"frontend"}, "devops": {"devops"},
                 "management": {"management"}}


def _pattern(phrase: str) -> re.Pattern[str]:
    # Whole words, with punctuation inside a phrase kept literal. "ai" must
    # not match "maintain", nor "hr" "three".
    return re.compile(r"(?<![a-z0-9])" + re.escape(phrase.lower()) + r"(?![a-z0-9])")


_COMPILED = [(family, [_pattern(p) for p in phrases]) for family, phrases in RULES]


def role_of(title: str) -> str:
    """The family a job title names, or "other"."""
    t = " ".join(str(title or "").lower().split())
    if not t:
        return "other"
    for family, patterns in _COMPILED:
        if any(p.search(t) for p in patterns):
            return family
    return "other"


def roles_of(titles: Iterable[str]) -> list[str]:
    """The families of a person's own job titles, most common first - what
    they have done, which the match score leans towards."""
    counts: dict[str, int] = {}
    for title in titles:
        r = role_of(title)
        if r not in ("other", "non-tech"):
            counts[r] = counts.get(r, 0) + 1
    return sorted(counts, key=lambda r: -counts[r])


def skipped_families(profile: dict) -> set[str]:
    """The families a person ruled out in setup: a skip group counts when every
    title phrase it stands for is in their reject_function."""
    from .onboard import SKIP
    rejected = {str(p).lower() for p in profile.get("reject_function") or []}
    out: set[str] = set()
    for key, phrases in SKIP.items():
        if phrases and all(p.lower() in rejected for p in phrases):
            out |= SKIP_FAMILIES.get(key, set())
    return out
