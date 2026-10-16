"""Requirements are kept, shown to the model, and refreshed from the source.
Found 2026-09-29: the stored 2,000-character head dropped the requirements of
most postings (Amazon's come 5,000 characters in), so the model ranked on the
pitch alone."""
from __future__ import annotations

import json
from types import SimpleNamespace

from jobsearch import triage_cli
from jobsearch.rank import _posting_block
from jobsearch.triage import requirements_excerpt
from samples import make_root
PITCH = "Join Amazon Music's search team and build ranking for millions of listeners. " * 40
AMAZON = (PITCH + "\nBasic qualifications\n- PhD, or Master's degree and 4+ years of CS, CE, ML or related "
          "field experience\n- 3+ years of building machine learning models for business application experience")


def test_the_requirements_are_found_past_the_pitch():
    req = requirements_excerpt(AMAZON)
    assert req.startswith("Basic qualifications") and "PhD, or Master's degree" in req
    assert requirements_excerpt("Just a pitch, no requirement heading at all.") == ""


def test_the_model_reads_the_requirements_not_just_the_head():
    rec = {"id": "amazon-1", "title": "Applied Scientist II", "company": "Amazon", "description": AMAZON[:2000],
           "requirements": requirements_excerpt(AMAZON), "years_required": 3}
    block = _posting_block(rec)
    assert "REQUIREMENTS:\nBasic qualifications" in block and "PhD, or Master's degree" in block
    assert "stated minimum experience: 3 years" in block                  # from the whole text


def _root(tmp_path, postings):
    prof = make_root(tmp_path)
    (prof.var / "triage-results.json").write_text(json.dumps({"postings": postings}))
    return prof


def _rec(pid, title, board, ai="strong"):
    return {"id": pid, "title": title, "company": "Acme", "location": "Pune, India", "url": "u",
            "board": board, "verdict": "keep", "description": "Build Java and Python services on AWS.",
            "ai": {"fit": ai, "why": "x", "by": "m"}}


def test_refresh_re_triages_from_the_whole_text_and_leaves_what_it_cannot_fetch(tmp_path, monkeypatch):
    root = _root(tmp_path, {
        "zoho-1": _rec("zoho-1", "AI Engineer", "zoho:example.zohorecruit.com"),
        "gone-2": _rec("gone-2", "Software Engineer", "zoho:example.zohorecruit.com"),
        "amazon-3": _rec("amazon-3", "Software Development Engineer II", "amazon:IND@software-development"),
    })
    full = {"zoho-1": "Experience: 10 + Years. Build agentic AI with Python, LLMs and RAG on AWS.",
            "gone-2": ""}                                                  # the fetch failed
    monkeypatch.setattr(triage_cli, "fetch_details",
                        lambda f, prov, slug, rec: {"description": full[rec["id"]]} if full[rec["id"]] else {})
    monkeypatch.setattr(triage_cli, "probe", lambda *a, **k: SimpleNamespace(postings=[SimpleNamespace(
        id="amazon-3", description=PITCH + "\nBasic qualifications\n- 3+ years of non-internship professional "
                                           "software development experience\n- Java, Python, AWS")]))
    report = triage_cli.refresh_results(root, fetcher=object(), say=lambda *_: None)
    stored = json.loads((root.var / "triage-results.json").read_text())["postings"]
    assert report["skipped"] == 1 and stored["gone-2"]["ai"]["fit"] == "strong"      # untouched
    assert stored["zoho-1"]["verdict"] == "reject"          # 10+ years: rejected, kept as a marked record
    amazon = stored["amazon-3"]
    assert amazon["years_required"] == 3 and amazon["requirements"].startswith("Basic qualifications")
    assert amazon["ai"]["fit"] == "strong"            # carried over; the new prompt version re-reads it
