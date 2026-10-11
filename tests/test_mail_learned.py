"""Every rule in each sample profile's mail-learned.yaml, as its own test.
Approving a rule in the app adds a case here: its example must still read as
it was approved, and its sender must still name its company."""
from __future__ import annotations

import pytest
import yaml

from jobsearch import mail_rules as R
from samples import SAMPLES, sample


def _rules(kind: str) -> list[tuple[str, dict]]:
    out = []
    for pid in SAMPLES:
        p = sample(pid).file("mail-learned.yaml")
        doc = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}
        out += [(pid, r) for r in doc.get(kind) or []]
    return out


PHRASES, SENDERS = _rules("phrases"), _rules("senders")


def test_there_is_something_to_check():
    assert PHRASES and SENDERS


@pytest.mark.parametrize("pid", SAMPLES)
def test_the_file_loads(pid):
    got = R.learned(sample(pid))
    assert len(got["phrases"]) == len([r for p, r in PHRASES if p == pid])
    assert len(got["senders"]) == len([r for p, r in SENDERS if p == pid])


@pytest.mark.parametrize("pid, rule", PHRASES, ids=[r["phrase"][:40] for _, r in PHRASES])
def test_each_learned_phrase_still_reads_its_example(pid, rule):
    v = R.classify("someone@example.com", "", rule["example"], sample(pid))
    assert (v.kind, v.confidence) == (rule["means"], "high")


@pytest.mark.parametrize("pid, rule", SENDERS, ids=[r["sender"] for _, r in SENDERS])
def test_each_learned_sender_still_names_its_company(pid, rule):
    address = rule["sender"] if "@" in rule["sender"] else f"someone@{rule['sender']}"
    assert R.company(address, "", "", sample(pid)) == (rule["company"], "learned")


def test_learned_rules_are_one_persons():
    """Another profile's approvals never apply: the rule is in the profile."""
    other = next(pid for pid in SAMPLES if not sample(pid).file("mail-learned.yaml").exists())
    pid, rule = SENDERS[0]
    address = rule["sender"] if "@" in rule["sender"] else f"someone@{rule['sender']}"
    assert R.company(address, "", "", sample(other))[1] != "learned"
