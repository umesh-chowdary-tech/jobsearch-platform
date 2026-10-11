"""Discovery: does this company have a machine-readable board, and is it theirs?

Two jobs. Finding a board is mechanical. Deciding the board actually belongs to
the company you think it does is the part that needs a human, sometimes.
"""
from __future__ import annotations

import re
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from .http import Fetcher
from .models import NOT_FOUND, BoardProbe, Escalation
from .providers import PROVIDERS, probe

# Corporate suffixes carry no identity. "Razorpay Software Private Limited" and
# "Razorpay" are the same company, and the board slug is usually the long form.
_NOISE = {
    "inc", "llc", "ltd", "limited", "private", "pvt", "corp", "corporation",
    "company", "co", "technologies", "technology", "tech", "software",
    "solutions", "labs", "systems", "india", "global", "group", "holdings",
    "the", "and",
}


def normalise(name: str) -> str:
    """Lowercase, strip punctuation and corporate noise, join what's left."""
    words = re.split(r"[^a-z0-9]+", (name or "").lower())
    kept = [w for w in words if w and w not in _NOISE]
    return "".join(kept)


def slug_candidates(company: str) -> list[str]:
    """Plausible slugs for a company with no known board.

    Deliberately small. Guessing is cheap per request but the exclusion list in
    the registry exists because a wide guess-space produces a lot of 404s and
    very little signal.
    """
    words = [w for w in re.split(r"[^a-z0-9]+", (company or "").lower()) if w]
    meaningful = [w for w in words if w not in _NOISE] or words
    out = ["".join(meaningful), "-".join(meaningful)]
    if len(meaningful) > 1:
        out.append(meaningful[0])
    seen: set[str] = set()
    return [s for s in out if s and not (s in seen or seen.add(s))]


def apply_hosts(p: BoardProbe) -> list[str]:
    hosts = Counter(
        urllib.parse.urlsplit(post.url).netloc
        for post in p.postings
        if post.url
    )
    return [h for h, _ in hosts.most_common(5) if h]


def check_identity(p: BoardProbe) -> Escalation | None:
    """Escalate on identity doubt only.

    A 404 is an answer, not a question. Escalating those would fill the queue
    with noise and train the human to stop reading it — at which point the
    human-in-the-loop is decorative.
    """
    if not p.found:
        return None

    expected = normalise(p.company)
    if not expected:
        return None

    board = normalise(p.board_name or "")
    hosts = apply_hosts(p)
    slug_norm = normalise(p.slug)

    # The slug matching the company is itself decent evidence — it is what we
    # searched for and the ATS accepted it.
    if expected and (expected in slug_norm or slug_norm in expected):
        slug_agrees = True
    else:
        slug_agrees = False

    if board:
        if expected in board or board in expected:
            return None
        return Escalation(
            kind="identity_mismatch",
            company=p.company,
            provider=p.provider,
            slug=p.slug,
            reason=(
                f'board is named "{p.board_name}" but we searched for '
                f'"{p.company}" — confirm this board belongs to them'
            ),
            evidence={
                "board_name": p.board_name,
                "apply_link_hosts": hosts,
                "sample_titles": [x.title for x in p.postings[:3]],
                "posting_count": len(p.postings),
            },
        )

    # No board name available (Lever, and Ashby much of the time). Fall back to
    # the apply links: if none of them mention the company or the ATS, we have
    # no evidence at all that this board is theirs.
    if not slug_agrees and hosts:
        ats_hosts = ("lever.co", "greenhouse.io", "ashbyhq.com", "myworkdayjobs.com", "zohorecruit.", "oraclecloud.com", "amazon.jobs", "atlassian.com", "careers.microsoft.com")
        related = any(
            expected in normalise(h) or any(a in h for a in ats_hosts) for h in hosts
        )
        if not related:
            return Escalation(
                kind="identity_unverifiable",
                company=p.company,
                provider=p.provider,
                slug=p.slug,
                reason=(
                    "no board name from this ATS, and no apply link mentions the "
                    "company or the ATS — cannot confirm ownership"
                ),
                evidence={
                    "apply_link_hosts": hosts,
                    "sample_titles": [x.title for x in p.postings[:3]],
                },
            )
    return None


@dataclass
class DiscoveryRun:
    probes: list[BoardProbe] = field(default_factory=list)
    escalations: list[Escalation] = field(default_factory=list)

    @property
    def found(self) -> list[BoardProbe]:
        return [p for p in self.probes if p.found]

    def summary(self) -> dict[str, int]:
        c = Counter(p.status for p in self.probes)
        return {
            **{k: c[k] for k in sorted(c)},
            "escalations": len(self.escalations),
        }


def verify_known(
    fetcher: Fetcher, known: Iterable[tuple[str, str, str]]
) -> DiscoveryRun:
    """Re-check boards we already believe in. `known` is (provider, slug, company)."""
    run = DiscoveryRun()
    for provider, slug, company in known:
        p = probe(fetcher, provider, slug, company)
        run.probes.append(p)
        esc = check_identity(p)
        if esc:
            run.escalations.append(esc)
    return run


def provider_slugs(provider: str, company: str) -> list[str]:
    """What to try at one provider. A Zoho board is a whole host, and Indian
    employers sit on the .in data centre as often as .com."""
    base = slug_candidates(company)
    if provider == "zoho":
        return [f"{s}.zohorecruit.{dc}" for s in base if re.fullmatch(r"[a-z0-9-]+", s)
                for dc in ("com", "in")]
    return base


def search_company(
    fetcher: Fetcher,
    company: str,
    providers: Iterable[str] = (*PROVIDERS, "zoho"),
    exclude: Iterable[tuple[str, str]] = (),
) -> DiscoveryRun:
    """Probe candidate slugs for a company with no known board.

    Stops at the first live board: a company on Greenhouse is not also on Lever,
    and continuing would be requests spent to learn nothing.
    """
    run = DiscoveryRun()
    skip = {(pr, sl) for pr, sl in exclude}
    for provider in providers:
        for slug in provider_slugs(provider, company):
            if (provider, slug) in skip:
                continue
            p = probe(fetcher, provider, slug, company)
            run.probes.append(p)
            if p.found:
                esc = check_identity(p)
                if esc:
                    run.escalations.append(esc)
                return run
            if p.status != NOT_FOUND:
                # A block or an error is not evidence of absence; stop guessing
                # at this provider rather than hammering it with more slugs.
                break
    return run
