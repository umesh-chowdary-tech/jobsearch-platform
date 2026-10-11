"""Profiles: one engine, many people. Boards are shared; people are separate."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest
import yaml

from jobsearch import cycle as C
from jobsearch.assemble import build, load_store
from jobsearch.profile import Profile, ProfileError, list_profiles, resolve, shared_var
from jobsearch.rank import candidate_level
from jobsearch.render import render_text
from jobsearch.skills import load_index
from jobsearch.triage import KEEP, REJECT, load_profile, triage_posting
from jobsearch.triage_cli import run_triage
from jobsearch.watch import WatchRun
from samples import ROOT, SAMPLES, make_root, sample


# ── finding the profile ──────────────────────────────────────────────────────

def test_the_samples_are_complete_profiles():
    assert set(SAMPLES) <= set(list_profiles(ROOT))
    for pid in SAMPLES:
        assert sample(pid).exists()


def test_one_profile_is_chosen_without_asking(tmp_path):
    prof = make_root(tmp_path)
    assert resolve(tmp_path) == prof


def test_several_profiles_need_a_name(tmp_path, monkeypatch):
    make_root(tmp_path, "sample-backend")
    make_root(tmp_path, "sample-frontend")
    with pytest.raises(ProfileError, match="pass --profile"):
        resolve(tmp_path)
    assert resolve(tmp_path, "sample-frontend").id == "sample-frontend"
    monkeypatch.setenv("JOBSEARCH_PROFILE", "sample-backend")
    assert resolve(tmp_path).id == "sample-backend"
    with pytest.raises(ProfileError, match="no profile 'nobody'"):
        resolve(tmp_path, "nobody")


@pytest.mark.parametrize("bad", ["_shared", "Has Caps", "../up", "", "a/b"])
def test_a_profile_id_names_a_folder_and_nothing_else(bad):
    with pytest.raises(ProfileError):
        Profile(ROOT, bad)


def test_an_incomplete_folder_is_not_a_profile(tmp_path):
    (tmp_path / "profiles" / "half").mkdir(parents=True)
    (tmp_path / "profiles" / "half" / "skills.yaml").write_text("tiers: {}\n")
    assert list_profiles(tmp_path) == []


def test_profiles_can_live_outside_the_checkout(tmp_path, monkeypatch):
    """A real person's profile can sit well away from the repository."""
    outside = tmp_path / "private"
    make_root(tmp_path / "elsewhere")
    (tmp_path / "elsewhere" / "profiles").rename(outside)
    monkeypatch.setenv("JOBSEARCH_PROFILES", str(outside))
    prof = resolve(ROOT, "sample-backend")
    assert prof.dir == outside / "sample-backend" and prof.var == ROOT / "var" / "sample-backend"


def test_only_the_fictional_samples_are_committed():
    """profiles/* is ignored; only sample-* (and the README) come back in."""
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "profiles/*" in lines and "!profiles/sample-*/" in lines
    assert all(pid.startswith("sample-") for pid in SAMPLES)


def test_the_samples_carry_no_real_contact_details():
    """Fictional people: example.com addresses, placeholder numbers, no links."""
    for pid in SAMPLES:
        idn = load_store(sample(pid))["identity"]
        assert idn["email"].endswith("@example.com")
        number = re.sub(r"^\+\d{1,3}", "", str(idn.get("phone") or ""))   # after the country code
        assert not re.search(r"[1-9]", number)
        assert not idn.get("linkedin") and not idn.get("github")
        text = sample(pid).file("master-store.yaml").read_text(encoding="utf-8")
        assert "FICTIONAL SAMPLE" in text
        for url in re.findall(r"https?://[^\s)]+", text):
            assert url.startswith("https://example.com/"), url


# ── one engine, three people ─────────────────────────────────────────────────

@pytest.mark.parametrize("pid", SAMPLES)
def test_every_sample_loads_and_triages(pid):
    prof = sample(pid)
    index, search = load_index(prof), load_profile(prof.dir)
    t = triage_posting(index, search, {"id": "1", "title": "Software Engineer", "company": "acme",
                                       "location": "Remote - India", "description": "Python, React, Kotlin."})
    assert t.verdict in (KEEP, REJECT, "flag", "no_match")


def test_the_same_posting_is_judged_against_each_persons_own_filters():
    android = {"id": "a", "title": "Senior Android Engineer", "company": "acme", "location": "Chennai, India",
               "description": "Kotlin, Jetpack Compose, Coroutines. 8+ years of experience."}
    verdicts = {pid: triage_posting(load_index(sample(pid)), load_profile(sample(pid).dir), android).verdict
                for pid in SAMPLES}
    assert verdicts["sample-android"] == KEEP
    assert verdicts["sample-frontend"] == REJECT        # "senior" is out of reach for him


def test_the_level_line_is_each_persons_own():
    lead = candidate_level(sample("sample-android"), load_store(sample("sample-android")))[0]
    assert "10 years" in lead and "a tech lead" in lead and "Umbrella Apps 2016-06 to 2020-12" in lead
    junior = candidate_level(sample("sample-frontend"), load_store(sample("sample-frontend")))[0]
    assert "1 years" in junior and "Titles out of reach: senior" in junior


def test_a_resume_is_built_from_one_persons_facts_only(tmp_path):
    prof = make_root(tmp_path, "sample-android")
    store, index = load_store(prof), load_index(prof)
    posting = {"id": "p1", "title": "Android Engineer", "description": "Kotlin and Jetpack Compose."}
    v, entries, _ = build(prof, store, index, posting, use_model=False)
    assert {e["id"] for e in entries} <= {e["id"] for e in store["entries"]}
    text = render_text(store["identity"], v, entries, v.surfaces, index)
    assert "Meera Placeholder" in text and "Acme" not in text and "Kavya" not in text
    # No approved descriptor: the official title stands alone, nothing coined.
    assert v.title_descriptor == "" and "Lead Android Engineer\n" in text
    assert "Lead Android Engineer (" not in text


# ── boards are shared, people are separate ───────────────────────────────────

def _queue(root: Path, *postings: dict) -> None:
    shared = shared_var(root)
    shared.mkdir(parents=True, exist_ok=True)
    with (shared / "new-postings.jsonl").open("a", encoding="utf-8") as fh:
        for p in postings:
            fh.write(json.dumps({"first_seen": "2026-10-01T00:00:00+00:00", "board": "lever:acme",
                                 "company": "Acme", "url": "u", **p}) + "\n")


BACKEND = {"id": "b1", "title": "Backend Engineer", "location": "Pune, India",
           "description": "Python, FastAPI and PostgreSQL. 3+ years of experience."}
FRONTEND = {"id": "f1", "title": "Frontend Developer", "location": "Bengaluru, India",
            "description": "React and TypeScript. 1+ years of experience."}


def _results(prof: Profile) -> dict:
    return json.loads((prof.var / "triage-results.json").read_text(encoding="utf-8"))["postings"]


def test_one_queue_is_triaged_separately_for_each_person(tmp_path):
    a = make_root(tmp_path, "sample-backend")
    b = make_root(tmp_path, "sample-frontend")
    _queue(tmp_path, BACKEND, FRONTEND)
    args = argparse.Namespace(all=False, board=None, quiet=True)
    run_triage(a, args)
    run_triage(b, args)
    assert _results(a)["b1"]["verdict"] == "keep" and _results(b)["f1"]["verdict"] == "keep"
    assert _results(b)["b1"]["verdict"] != "keep"                       # not a frontend role
    assert a.var != b.var and (a.var / "triaged.json").exists() and (b.var / "triaged.json").exists()


def test_a_profile_added_later_reads_the_whole_queue(tmp_path):
    a = make_root(tmp_path, "sample-backend")
    _queue(tmp_path, BACKEND)
    args = argparse.Namespace(all=False, board=None, quiet=True)
    run_triage(a, args)
    _queue(tmp_path, FRONTEND)
    late = make_root(tmp_path, "sample-frontend")
    run_triage(late, args)
    assert set(_results(late)) == {"b1", "f1"}
    run_triage(a, args)
    assert set(_results(a)) == {"b1", "f1"}                             # only the new one was read


def test_the_cycle_polls_once_and_works_for_everyone(tmp_path, monkeypatch):
    for pid in SAMPLES:
        make_root(tmp_path, pid)
    polls, triaged, drawn = [], [], []
    monkeypatch.setattr(C, "load_boards", lambda v: [{"provider": "lever", "slug": "acme"}])
    monkeypatch.setattr(C, "default_fetcher", lambda v: None)
    monkeypatch.setattr(C, "watch_all", lambda f, v, b: polls.append(v) or WatchRun())

    def triage(prof, args):
        triaged.append(prof.id)
        return 0

    def render(prof):
        drawn.append(prof.id)
        prof.var.mkdir(parents=True, exist_ok=True)
        (prof.var / "dashboard.html").write_text("x")
        return prof.var / "dashboard.html"
    monkeypatch.setattr(C, "run_triage", triage)
    monkeypatch.setattr(C, "render", render)
    import jobsearch.mailbox
    monkeypatch.setattr(jobsearch.mailbox, "configured", lambda prof: False)
    assert C.run_cycle(tmp_path, quiet=True) == 0
    assert polls == [shared_var(tmp_path)]                              # one poll, into the shared state
    assert sorted(triaged) == sorted(drawn) == sorted(SAMPLES)
    triaged.clear()
    C.run_cycle(tmp_path, quiet=True, profiles=[Profile(tmp_path, "sample-android")])
    assert triaged == ["sample-android"]


def test_the_cli_takes_a_profile(tmp_path, capsys):
    from jobsearch import cli
    make_root(tmp_path, "sample-backend")
    make_root(tmp_path, "sample-frontend")
    assert cli.cmd_profiles(argparse.Namespace(root=tmp_path)) == 0
    assert "sample-backend" in capsys.readouterr().out
    ns = argparse.Namespace(root=tmp_path, profile=None)
    with pytest.raises(ProfileError):
        cli._prof(ns)
    ns.profile = "sample-frontend"
    assert cli._prof(ns).id == "sample-frontend"


def test_shared_knowledge_holds_no_ones_facts():
    """data/ is everyone's: no resume facts, skills, search settings or
    learned inbox rules belong there."""
    for name in ("master-store.yaml", "skills.yaml", "search-profile.yaml", "mail-learned.yaml"):
        assert not (ROOT / "data" / name).exists(), name
    limits = yaml.safe_load((ROOT / "data" / "llm-limits.yaml").read_text(encoding="utf-8"))
    assert all("per_profile" in v for v in limits.values() if v.get("requests_per_day"))
