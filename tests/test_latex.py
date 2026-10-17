"""LaTeX tests.

Most of these stand in for compiling, so they run anywhere. The cheapest class
of LaTeX bug - a macro called with the wrong number of arguments, an unbalanced
environment, an unescaped special character - is exactly what a compile would
have caught and a human reading the output would not.

Where Tectonic is installed, one test also compiles and reads the PDF back:
the first real compile showed the source checks were not enough, because the
PDF looked right and extracted "Conﬂuence" and a title split as "T echnical".
"""
from __future__ import annotations

import re

import pytest

from jobsearch.assemble import Variant, load_store, select_entries, surfaced_skills
from jobsearch.latex import (PREAMBLE, compile_pdf, find_engine, latex_to_text,
                             pdf_pages, pdf_text, render_latex, tex,
                             text_layer_problems)
from jobsearch.resume_layout import contact_bits
from jobsearch.render import check_text
from jobsearch.skills import load_index
from samples import sample


@pytest.fixture(scope="module")
def store():
    return load_store(sample())


@pytest.fixture(scope="module")
def index():
    return load_index(sample())


@pytest.fixture(scope="module")
def doc(store, index):
    wanted = {"Python", "PostgreSQL", "AWS", "React", "TypeScript"}
    entries = select_entries(store, index, wanted)
    skills = surfaced_skills(entries, index, wanted)
    v = Variant(id="fullstack", angle="fullstack",
                emits=[e["id"] for e in entries], surfaces=skills,
                summary="Test summary with no numbers.")
    return render_latex(store["identity"], v, entries, skills, index), entries, skills


# ── escaping ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("100%", r"100\%"),
    ("A & B", r"A \& B"),
    ("under_score", r"under\_score"),
    ("$5", r"\$5"),
    ("#1", r"\#1"),
    ("~1 week", r"\textasciitilde{}1 week"),
])
def test_special_characters_are_escaped(raw, expected):
    assert tex(raw) == expected


def test_angle_brackets_are_escaped():
    """"nano (<10K followers)": unescaped these render as inverted punctuation
    in the default TeX font."""
    assert tex("<10K") == r"\textless{}10K"
    assert "<" not in tex("a < b > c")


# ── structure ────────────────────────────────────────────────────────────────

def test_document_is_balanced(doc):
    src, _, _ = doc
    assert src.count(r"\begin{document}") == 1
    assert src.count(r"\end{document}") == 1
    assert src.count("{") == src.count("}")


@pytest.mark.parametrize("env", ["itemize", "center"])
def test_environments_are_balanced(doc, env):
    src, _, _ = doc
    assert src.count(rf"\begin{{{env}}}") == src.count(rf"\end{{{env}}}")


def _declared_arity(src: str, name: str) -> int | None:
    m = re.search(r"\\newcommand\{\\" + name + r"\}\[(\d)\]", src)
    return int(m.group(1)) if m else None


def _call_arities(src: str, name: str) -> list[int]:
    """How many {...} groups follow each call of \\name (declaration excluded)."""
    body = src.split(r"\begin{document}", 1)[-1]
    out = []
    for m in re.finditer(r"\\" + name + r"(?![a-zA-Z])", body):
        i, n = m.end(), 0
        while i < len(body) and body[i] == "{":
            depth = 0
            while i < len(body):
                if body[i] == "{":
                    depth += 1
                elif body[i] == "}":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            n += 1
        out.append(n)
    return out


@pytest.mark.parametrize("macro", ["position", "company", "stream", "role"])
def test_every_macro_call_matches_its_declared_arity(doc, macro):
    """`\\employer` was declared [3] and called with 2 - a hard compile error,
    invisible without a TeX engine and invisible when reading the output."""
    src, _, _ = doc
    declared = _declared_arity(PREAMBLE, macro)
    assert declared is not None, f"{macro} not declared"
    for n in _call_arities(src, macro):
        assert n == declared, f"\\{macro} declared [{declared}] but called with {n}"


def test_no_macro_is_called_before_it_is_defined(doc):
    src, _, _ = doc
    body = src.split(r"\begin{document}", 1)[-1]
    used = set(re.findall(r"\\([a-zA-Z]{4,})", body))
    custom = set(re.findall(r"\\newcommand\{\\([a-zA-Z]+)\}", PREAMBLE))
    known_builtins = {
        "begin", "end", "item", "section", "textbf", "textit", "itshape",
        "large", "Large", "LARGE", "small", "normalfont", "normalsize",
        "bfseries", "scshape", "hfill", "vspace", "par", "cdot", "ldots",
        "textbackslash", "textasciitilde", "textasciicircum", "textless",
        "textgreater", "times", "documentclass", "usepackage", "pagestyle",
        "setlength", "parindent", "setlist", "titleformat", "titlespacing",
        "newcommand", "rule", "linewidth", "hidelinks",
    }
    unknown = {u for u in used if u not in custom and u not in known_builtins}
    assert not unknown, f"undefined macros: {sorted(unknown)}"


# ── the content still survives ───────────────────────────────────────────────

def test_the_latex_passes_the_same_assertions_as_the_html(doc, store):
    src, entries, skills = doc
    res = check_text(latex_to_text(src), store["identity"], entries, skills)
    assert res["ok"], [c for c in res["checks"] if not c["ok"]]


def test_work_is_grouped_under_one_employer(doc):
    """Six consecutive headings for one employer read as six jobs. One
    employer, one date range, named workstreams underneath."""
    src, _, _ = doc
    assert src.count(r"\position{Software Engineer II") == 1
    assert src.count(r"\company{Acme Freight") == 1
    assert len(_call_arities(src, "stream")) >= 3


def test_skills_are_grouped_not_one_long_line(doc):
    src, _, _ = doc
    labels = re.findall(r"\\textbf\{([A-Za-z \\&]+):\}", src)
    assert len(labels) >= 3


# ── what a parser reads out of the PDF ───────────────────────────────────────

def test_preamble_keeps_ligatures_out_of_the_text_layer():
    assert "Ligatures={TeX,NoCommon}" in PREAMBLE                      # Tectonic / XeTeX
    assert "DisableLigatures" in PREAMBLE and "glyphtounicode" in PREAMBLE  # Overleaf / pdfLaTeX


def test_preamble_never_hyphenates():
    """ "Post-/greSQL" split across a line in one generated resume."""
    assert "hyphenpenalty=10000" in PREAMBLE


def test_a_github_handle_prints_as_a_link():
    assert "github.com/some-handle" in contact_bits({"github": "some-handle"})
    assert contact_bits({"github": "https://github.com/x"}) == ["https://github.com/x"]


@pytest.mark.parametrize("text, broken", [
    ("Con\ufb02uence", True),
    ("Confluence", False),
    ("Post-\ngreSQL", True),
    ("second-in-command", False),
])
def test_text_layer_problems_are_found(text, broken):
    assert bool(text_layer_problems(text)) is broken


@pytest.mark.skipif(find_engine() is None, reason="no TeX engine installed")
def test_the_compiled_pdf_reads_back_clean(doc, store, tmp_path):
    src, entries, skills = doc
    tex_path = tmp_path / "resume.tex"
    tex_path.write_text(src, encoding="utf-8")
    pdf, msg = compile_pdf(tex_path)
    if pdf is None and any(t in msg for t in ("fetch", "internet", "request")):
        pytest.skip(f"package download failed, not a LaTeX error: {msg}")
    assert pdf, msg
    text = pdf_text(pdf)
    assert not text_layer_problems(text)
    assert "Software Engineer II" in text              # a title once came out as "T echnical"
    res = check_text(text, store["identity"], entries, skills)
    assert res["ok"], [c for c in res["checks"] if not c["ok"]]
    assert pdf_pages(pdf) <= 2


# ── a profile with no headline title ─────────────────────────────────────────

@pytest.fixture(scope="module")
def untitled(store, index):
    """A profile saved by setup can have no title: no official one, no descriptor."""
    identity = {k: v for k, v in store["identity"].items() if k != "title"}
    wanted = {"Python", "PostgreSQL", "AWS"}
    entries = select_entries(store, index, wanted)
    skills = surfaced_skills(entries, index, wanted)
    v = Variant(id="untitled", angle="backend", emits=[e["id"] for e in entries], surfaces=skills)
    return render_latex(identity, v, entries, skills, index), identity, entries, skills


def test_no_title_leaves_no_empty_line_to_end(untitled):
    # "{\large }\\[4pt]" ends a line with nothing on it: XeTeX stops with
    # "There's no line here to end", and the live site made no PDF.
    src = untitled[0]
    head = src[src.index(r"\begin{center}"):src.index(r"\end{center}")]
    assert r"{\large }" not in head
    assert head.count("\\\\") == 1                       # only the name's line ends


@pytest.mark.skipif(find_engine() is None, reason="no TeX engine installed")
def test_no_title_still_compiles(untitled, tmp_path):
    src, identity, entries, skills = untitled
    tex_path = tmp_path / "resume.tex"
    tex_path.write_text(src, encoding="utf-8")
    pdf, msg = compile_pdf(tex_path)
    if pdf is None and any(t in msg for t in ("fetch", "internet", "request")):
        pytest.skip(f"package download failed, not a LaTeX error: {msg}")
    assert pdf, msg


# ── the copy scripts/install-tectonic.sh puts in the checkout (the hosted build) ─

@pytest.fixture
def bundled(tmp_path, monkeypatch):
    """Only the checkout's copy: no $TECTONIC, nothing on PATH."""
    import jobsearch.latex as latex
    engine = tmp_path / ".tectonic" / "bin" / "tectonic"
    engine.parent.mkdir(parents=True)
    engine.write_bytes(b"")
    monkeypatch.setattr(latex, "BUNDLED", tmp_path / ".tectonic")
    monkeypatch.delenv("TECTONIC", raising=False)
    monkeypatch.delenv("TECTONIC_CACHE_DIR", raising=False)
    monkeypatch.setattr(latex.shutil, "which", lambda _name: None)
    return engine


def test_the_checkouts_own_tectonic_is_found(bundled):
    # Render had no TeX engine, so every hosted resume came without a PDF.
    assert find_engine() == str(bundled)


def _env_of_compile(monkeypatch, tmp_path):
    import subprocess

    import jobsearch.latex as latex
    seen = {}

    def run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 1, "", "error: stop here")
    monkeypatch.setattr(latex.subprocess, "run", run)
    (tmp_path / "r.tex").write_text("x", encoding="utf-8")
    compile_pdf(tmp_path / "r.tex", attempts=1)
    return seen.get("env")


def test_the_checkouts_tectonic_reads_the_packages_fetched_at_build_time(bundled, tmp_path, monkeypatch):
    # Its default cache is under the home folder, which Render does not keep
    # from the build: every first PDF would wait minutes on the download.
    env = _env_of_compile(monkeypatch, tmp_path)
    assert env["TECTONIC_CACHE_DIR"] == str(tmp_path / ".tectonic" / "cache")


def test_a_cache_dir_someone_set_is_kept(bundled, tmp_path, monkeypatch):
    monkeypatch.setenv("TECTONIC_CACHE_DIR", str(tmp_path / "mine"))
    env = _env_of_compile(monkeypatch, tmp_path)
    assert env is None or env["TECTONIC_CACHE_DIR"] == str(tmp_path / "mine")


def test_another_tectonic_keeps_its_own_cache(tmp_path, monkeypatch):
    import jobsearch.latex as latex
    other = tmp_path / "tectonic.exe"
    other.write_bytes(b"")
    monkeypatch.setenv("TECTONIC", str(other))
    monkeypatch.delenv("TECTONIC_CACHE_DIR", raising=False)
    monkeypatch.setattr(latex, "BUNDLED", tmp_path / ".tectonic")
    assert _env_of_compile(monkeypatch, tmp_path) is None


# ── tex-check: the hosted build's warm-up, and anyone's "does my TeX work?" ───

def test_tex_check_says_when_there_is_no_engine(monkeypatch, capsys):
    import jobsearch.latex as latex
    from jobsearch.assemble_cli import run_tex_check
    from samples import ROOT
    import jobsearch.assemble_cli as cli
    monkeypatch.setattr(latex, "find_engine", lambda: None)
    monkeypatch.setattr(cli, "find_engine", lambda: None)
    assert run_tex_check(ROOT) == 1
    assert "no TeX engine" in capsys.readouterr().out


def test_tex_check_reads_the_committed_sample_wherever_profiles_point(monkeypatch, tmp_path, capsys):
    # A local .env points JOBSEARCH_PROFILES at a private folder, which has no samples.
    import jobsearch.latex as latex
    from jobsearch.assemble_cli import run_tex_check
    from samples import ROOT
    monkeypatch.setenv("JOBSEARCH_PROFILES", str(tmp_path))
    import jobsearch.assemble_cli as cli
    monkeypatch.setattr(latex, "find_engine", lambda: None)
    monkeypatch.setattr(cli, "find_engine", lambda: None)
    assert run_tex_check(ROOT) == 1
    assert "no TeX engine" in capsys.readouterr().out


@pytest.mark.skipif(find_engine() is None, reason="no TeX engine installed")
def test_tex_check_compiles_and_reads_back_a_sample(capsys):
    from jobsearch.assemble_cli import run_tex_check
    from samples import ROOT
    code = run_tex_check(ROOT)
    out = capsys.readouterr().out
    if code and any(t in out for t in ("fetch", "internet", "request")):
        pytest.skip(f"package download failed, not a LaTeX error: {out}")
    assert code == 0, out
    assert re.search(r"pdf \d+/\d+ pass, [12] page", out), out


def test_the_hosted_build_installs_a_pinned_tectonic():
    import yaml
    from samples import ROOT
    build = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))["services"][0]["buildCommand"]
    assert "scripts/install-tectonic.sh" in build
    script = (ROOT / "scripts" / "install-tectonic.sh").read_text(encoding="utf-8")
    assert re.search(r"^VERSION=\d+\.\d+\.\d+$", script, re.M)
    assert re.search(r"^SHA256=[0-9a-f]{64}$", script, re.M)
    assert "sha256sum -c" in script
