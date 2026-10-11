"""Offline tests. Nothing here touches the network."""
from __future__ import annotations

import pytest

from jobsearch.discovery import (
    apply_hosts,
    check_identity,
    normalise,
    slug_candidates,
)
from jobsearch.models import NOT_FOUND, OK, BoardProbe, Posting
from jobsearch.providers import parse_ashby, parse_greenhouse, parse_lever


# ── name handling ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Razorpay Software Private Limited", "razorpay"),
        ("Razorpay", "razorpay"),
        ("Pure Storage", "purestorage"),
        ("Acme India", "acme"),
        ("The Hub", "hub"),
        ("", ""),
    ],
)
def test_normalise_strips_corporate_noise(raw, expected):
    assert normalise(raw) == expected


def test_razorpay_long_and_short_forms_match():
    # The registry slug is the long form; a human types the short one.
    assert normalise("Razorpay Software Private Limited") == normalise("Razorpay")


def test_slug_candidates_are_few_and_ordered():
    assert slug_candidates("Pure Storage") == ["purestorage", "pure-storage", "pure"]
    assert slug_candidates("Groww") == ["groww"]


def test_slug_candidates_deduplicate():
    assert len(slug_candidates("Zepto")) == len(set(slug_candidates("Zepto")))


# ── provider parsers ─────────────────────────────────────────────────────────

def test_parse_greenhouse():
    payload = {
        "jobs": [
            {
                "id": 4567,
                "title": "Backend Engineer",
                "location": {"name": "Hyderabad, India"},
                "absolute_url": "https://boards.greenhouse.io/druva/jobs/4567",
                "updated_at": "2026-09-20T10:00:00Z",
            }
        ]
    }
    (p,) = parse_greenhouse(payload)
    assert p.id == "4567"                       # coerced to str for stable diffing
    assert p.location == "Hyderabad, India"
    assert p.updated_at == "2026-09-20T10:00:00Z"


def test_parse_lever_reads_the_array_shape():
    payload = [
        {
            "id": "abc-123",
            "text": "Senior SDE",
            "categories": {"location": "Bengaluru"},
            "hostedUrl": "https://jobs.lever.co/cred/abc-123",
            "createdAt": 1758000000000,
        }
    ]
    (p,) = parse_lever(payload)
    assert (p.id, p.title, p.location) == ("abc-123", "Senior SDE", "Bengaluru")


def test_parse_ashby():
    payload = {"jobs": [{"id": "x1", "title": "ML Engineer", "location": "Remote",
                         "jobUrl": "https://jobs.ashbyhq.com/sarvam/x1"}]}
    (p,) = parse_ashby(payload)
    assert p.id == "x1" and p.location == "Remote"


@pytest.mark.parametrize("parser", [parse_greenhouse, parse_lever, parse_ashby])
def test_parsers_survive_empty_and_malformed(parser):
    assert parser(None) == []
    assert parser({}) == []


def test_missing_fields_do_not_crash():
    (p,) = parse_greenhouse({"jobs": [{"id": 1}]})
    assert p.title == "" and p.location == "" and p.url == ""


# ── identity checking, the only thing discovery escalates on ─────────────────

def _probe(**kw) -> BoardProbe:
    base = dict(company="Druva", provider="greenhouse", slug="druva",
                status=OK, url="https://example.invalid")
    base.update(kw)
    postings = base.pop("postings", [Posting("1", "SDE", "Hyderabad",
                                             "https://boards.greenhouse.io/druva/jobs/1")])
    p = BoardProbe(**base)
    p.postings = postings
    return p


def test_matching_board_name_does_not_escalate():
    assert check_identity(_probe(board_name="Druva")) is None


def test_name_mismatch_escalates():
    esc = check_identity(_probe(company="Pure Storage", slug="purestorage",
                                board_name="Everpure"))
    assert esc is not None
    assert esc.kind == "identity_mismatch"
    assert "Everpure" in esc.reason
    assert esc.evidence["board_name"] == "Everpure"


def test_a_404_is_never_escalated():
    """A plain 404 is an answer, not a question. Escalating it would train the
    human to ignore the queue."""
    assert check_identity(_probe(status=NOT_FOUND, postings=[])) is None


def test_no_board_name_but_slug_agrees_is_accepted():
    # Lever exposes no board metadata; the slug matching is the evidence.
    assert check_identity(_probe(company="CRED", provider="lever", slug="cred",
                                 board_name=None)) is None


def test_no_board_name_and_unrelated_apply_hosts_escalates():
    esc = check_identity(
        _probe(company="Jupiter", provider="lever", slug="somethingelse",
               board_name=None,
               postings=[Posting("1", "SDE", "Bengaluru", "https://unrelated.example/1")])
    )
    assert esc is not None and esc.kind == "identity_unverifiable"


def test_ats_hosted_apply_links_count_as_related():
    assert check_identity(
        _probe(company="Jupiter", provider="lever", slug="somethingelse",
               board_name=None,
               postings=[Posting("1", "SDE", "BLR", "https://jobs.lever.co/x/1")])
    ) is None


def test_apply_hosts_counts_distinct_hosts():
    p = _probe(postings=[
        Posting("1", "a", "x", "https://jobs.lever.co/cred/1"),
        Posting("2", "b", "x", "https://jobs.lever.co/cred/2"),
        Posting("3", "c", "x", "https://careers.cred.club/3"),
    ])
    assert apply_hosts(p)[0] == "jobs.lever.co"
