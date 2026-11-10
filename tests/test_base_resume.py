"""The overall resume and the person's resume style. The profile is fictional."""
from __future__ import annotations

import re
from dataclasses import replace

import pytest
import yaml

from jobsearch import latex
from jobsearch import resume_style as RS
from jobsearch.assemble import Variant, load_store, select_entries, surfaced_skills
from jobsearch.base_resume import build_base, load_record, looser, own_skills, tighter
from jobsearch.render import render_html, render_text, strip_html
from jobsearch.skills import load_index
from samples import make_root, sample


@pytest.fixture
def prof(tmp_path):
    return make_root(tmp_path)


# ── the style ────────────────────────────────────────────────────────────────

def test_starting_values_follow_years_of_experience():
    assert RS.defaults(1).pages == "1" and RS.defaults(1).projects_per_job == 2
    assert RS.defaults(6).pages == "2" and RS.defaults(6).projects_per_job == 3


def test_only_what_the_person_changed_is_written(prof):
    RS.save(prof, {"pages": "2"})
    assert yaml.safe_load((prof.dir / RS.STYLE_FILE).read_text(encoding="utf-8")) == {"pages": "2"}
    assert RS.view(prof)["chosen"] == ["pages"]


@pytest.mark.parametrize("changes, why", [
    ({"projects_per_job": 9}, "from 1 to 4"),
    ({"pages": "3"}, "1, 2 or any"),
    ({"sections": ["summary", "experience"]}, "skills"),       # a parser needs Skills
    ({"sections": ["skills", "experience", "hobbies"]}, "unknown section"),
    ({"colour": "blue"}, "no such setting"),
])
def test_a_bad_choice_is_refused_and_nothing_is_written(prof, changes, why):
    with pytest.raises(RS.StyleError, match=why):
        RS.save(prof, changes)
    assert not (prof.dir / RS.STYLE_FILE).exists()


def test_a_broken_style_file_falls_back_setting_by_setting(prof):
    (prof.dir / RS.STYLE_FILE).write_text("pages: '7'\nprojects_per_job: 1\n", encoding="utf-8")
    style = RS.load(prof)
    assert style.projects_per_job == 1 and style.pages in RS.PAGES


# ── the style on the page ────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def page():
    p = sample()
    store, index = load_store(p), load_index(p)
    kw = own_skills(index)
    entries = select_entries(store, index, kw)
    skills = surfaced_skills(entries, index, kw.claimable, weights=kw.weights)
    v = Variant(id="t", angle="t", emits=[e["id"] for e in entries], surfaces=skills, summary="A summary.")
    return store["identity"], v, entries, skills, index


def _headings(text):
    return [l for l in text.splitlines() if l.strip().upper() == l.strip() and l.strip().isalpha()]


def test_sections_print_in_the_persons_order_and_hidden_ones_do_not(page):
    identity, v, entries, skills, index = page
    style = replace(RS.JOB, sections=("experience", "skills", "education"))
    text = render_text(identity, v, entries, skills, index, style)
    assert _headings(text) == ["EXPERIENCE", "SKILLS", "EDUCATION"]
    html = strip_html(render_html(identity, v, entries, skills, index, style))
    assert "Summary" not in html and html.index("Experience") < html.index("Skills")
    tex = latex.latex_to_text(latex.render_latex(identity, v, entries, skills, index, style))
    assert tex.index("Experience") < tex.index("Skills") < tex.index("Education")
    assert "Projects" not in tex


def test_bullet_counts_follow_the_style(page):
    identity, v, entries, skills, index = page
    one = render_text(identity, v, entries, skills, index, replace(RS.JOB, project_bullets=1, piece_bullets=1,
                                                                     solo_bullets=1))
    exp = one.split("EXPERIENCE", 1)[1].split("PROJECTS", 1)[0]
    # Under each heading line, at most one "- " bullet before the next heading.
    runs = re.findall(r"((?:\n\s+- [^\n]*)+)", exp)
    assert runs and all(r.count("\n") == 1 for r in runs)


def test_the_job_style_prints_what_job_resumes_always_did(page):
    identity, v, entries, skills, index = page
    assert render_text(identity, v, entries, skills, index) == render_text(identity, v, entries, skills, index,
                                                                           RS.JOB)


# ── fitting the page ─────────────────────────────────────────────────────────

def test_tightening_drops_extras_first_and_never_goes_below_a_floor():
    steps = list(tighter(RS.Style(projects_per_job=3, pieces_per_project=3)))
    assert steps[0][1] == "extra projects"
    last = steps[-1][0]
    assert (last.projects_per_job, last.pieces_per_project, last.piece_bullets, last.project_bullets) == (1, 1, 1, 2)


def test_loosening_leaves_what_the_person_chose_alone():
    grown = [s for s, _ in looser(RS.Style(projects_per_job=2), {"projects_per_job"})]
    assert grown and all(s.projects_per_job == 2 for s in grown)


class FakePDF:
    """A PDF engine whose page count follows the length of the plain text: one
    page per `per_page` lines. Enough to drive the fitting loop."""

    def __init__(self, monkeypatch, per_page):
        self.per_page, self.builds = per_page, 0
        monkeypatch.setattr(latex, "find_engine", lambda: "fake")
        monkeypatch.setattr(latex, "compile_pdf", self.compile)
        monkeypatch.setattr(latex, "pdf_pages", self.pages)
        monkeypatch.setattr(latex, "pdf_text", lambda pdf: pdf.with_suffix(".txt").read_text(encoding="utf-8"))

    def compile(self, tex_path):
        self.builds += 1
        pdf = tex_path.with_suffix(".pdf")
        pdf.write_bytes(b"%PDF fake")
        return pdf, "compiled"

    def pages(self, pdf):
        lines = pdf.with_suffix(".txt").read_text(encoding="utf-8").count("\n")
        return -(-lines // self.per_page)


def test_a_long_page_is_shortened_until_it_fits(prof, monkeypatch):
    RS.save(prof, {"pages": "1", "projects_per_job": 3, "pieces_per_project": 3})
    engine = FakePDF(monkeypatch, per_page=60)
    rec = build_base(prof, use_model=False, say=lambda _: None)
    assert rec["pages"] == 1 and rec["note"].startswith("Shortened to fit 1 page")
    assert engine.builds <= 8
    assert load_record(prof)["pages"] == 1
    assert (prof.var / "resumes" / "base.pdf").exists()


def test_a_short_profile_says_so_instead_of_padding(prof, monkeypatch):
    RS.save(prof, {"pages": "2"})
    FakePDF(monkeypatch, per_page=1000)              # everything fits on one page
    rec = build_base(prof, use_model=False, say=lambda _: None)
    assert rec["pages"] == 1 and "fills 1 page" in rec["note"]


def test_with_no_pdf_engine_the_resume_is_still_built(prof, monkeypatch):
    monkeypatch.setattr(latex, "find_engine", lambda: None)
    rec = build_base(prof, use_model=False, say=lambda _: None)
    assert rec["checks_ok"] and "length wasn't checked" in rec["note"]
    assert (prof.var / "resumes" / "base.html").exists()


# ── the API ──────────────────────────────────────────────────────────────────

def test_the_hosted_app_saves_a_style_and_shows_it_with_the_resumes(prof):
    from jobsearch.web import routes
    from jobsearch.web.runner import Runner
    status, out = routes.post(prof, "/api/resumes/style", {"changes": {"pages": "2"}}, Runner(prof),
                              scope=routes.HOSTED)
    assert status == 200 and out["style"]["pages"] == "2"
    with pytest.raises(routes.ApiError, match="1, 2 or any"):
        routes.post(prof, "/api/resumes/style", {"changes": {"pages": "5"}}, Runner(prof), scope=routes.HOSTED)
    view = routes.get(prof, "/api/resumes", {}, Runner(prof), scope=routes.HOSTED)
    assert view["style"]["style"]["pages"] == "2" and view["base"] is None and view["has_profile"]
    assert "Build my overall resume" in routes.get(prof, "/api/actions", {}, Runner(prof), scope=routes.HOSTED).values()
