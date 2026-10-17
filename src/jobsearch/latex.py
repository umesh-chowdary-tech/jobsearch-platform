"""LaTeX output. Single column, standard packages, real text layer.

Compiles with Tectonic (XeTeX) here and with pdflatex on Overleaf - the font
block branches on the engine. No tables, no multicol, no tikz: the same
parse-safety rules as the HTML, for the same reason.

A PDF that looks right can still read wrong, so the compiled PDF's text layer
is extracted and checked, not the .tex source it came from.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .resume_layout import (
    contact_bits,
    entry_period,
    group_skills,
    group_work,
    heading,
    lead_line,
    position_title,
    practice_items,
    stack_items,
)
from .render import bullets, chronological
from .skills import SkillIndex

_ESC = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
    "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    # "nano (<10K followers)": unescaped, these render as inverted
    # punctuation in the default TeX font rather than as angle brackets.
    "<": r"\textless{}", ">": r"\textgreater{}",
}


def tex(s: Any) -> str:
    out = []
    for ch in str(s if s is not None else ""):
        out.append(_ESC.get(ch, ch))
    t = "".join(out)
    # Typographic niceties that survive extraction as plain characters.
    t = t.replace("...", r"\ldots{}")
    return t


# The font block is the part an ATS cares about. Compiled and read back with
# pypdf, the first PDFs looked right and extracted wrong: "Conﬂuence" and
# "ﬁne-tuned" as ligature glyphs, so a search for either missed; "Post-/greSQL"
# split across a line; "Member T echnical" with a kerning gap.
#
# What fixed it is the OpenType Charter in the XeTeX branch (Tectonic's):
# its text layer maps the fi/fl glyphs back to letters and carries no kerning
# gaps. A mutation test confirmed it - with ligatures switched back ON the
# compiled PDF still reads clean; with the old Type 1 Charter it does not.
# Ligatures are off anyway, for parsers that read glyphs without that map. The
# pdfLaTeX branch keeps the file compiling on Overleaf's default engine.
PREAMBLE = r"""\documentclass[a4paper,10pt]{article}
\usepackage{iftex}
\ifPDFTeX
  \input{glyphtounicode}\pdfgentounicode=1
  \usepackage[T1]{fontenc}
  \usepackage[utf8]{inputenc}
  \usepackage{charter}
  \usepackage{microtype}
  \DisableLigatures[f]{encoding=*}
\else
  \usepackage{fontspec}
  \setmainfont{XCharter}[Ligatures={TeX,NoCommon}]
\fi
\usepackage[margin=14mm,top=13mm,bottom=13mm]{geometry}
\usepackage{enumitem}
\usepackage{titlesec}
\usepackage[hidelinks]{hyperref}

\pagestyle{empty}
\setlength{\parindent}{0pt}
\hyphenpenalty=10000 \exhyphenpenalty=10000 \emergencystretch=3em
\raggedright
\setlist[itemize]{leftmargin=12pt, itemsep=1pt, topsep=2pt, parsep=0pt}

\titleformat{\section}{\normalsize\bfseries\scshape}{}{0pt}{}[\vspace{-7pt}\rule{\linewidth}{0.5pt}]
\titlespacing{\section}{0pt}{9pt}{5pt}

\newcommand{\role}[2]{%
  \textbf{#1}\hfill{\small #2}\par\vspace{1pt}}
\newcommand{\position}[2]{%
  {\large\textbf{#1}}\hfill{\small\textbf{#2}}\par}
\newcommand{\company}[1]{%
  {#1}\par\vspace{3pt}}
\newcommand{\stream}[1]{%
  \textbf{#1}\par\vspace{1pt}}
\newcommand{\stackline}[1]{%
  {\small\hspace{12pt}Stack: #1}\par}
\newcommand{\practices}[1]{%
  {\small Practices: #1}\par\vspace{3pt}}
"""


def render_latex(identity: dict[str, Any], variant, entries: list[dict[str, Any]],
                 skills: list[str], index: SkillIndex) -> str:
    L: list[str] = [PREAMBLE, r"\begin{document}", ""]

    def stack(e: dict[str, Any]) -> None:
        items = stack_items(e, skills, index)
        if items:
            L.append(r"\stackline{" + tex(", ".join(items)) + "}")

    # ── header ───────────────────────────────────────────────────────────────
    L.append(r"\begin{center}")
    L.append(r"{\LARGE\bfseries " + tex(identity["name"]) + r"}\\[3pt]")
    # No title, no title line: "\\" after nothing stops XeTeX with "There's
    # no line here to end", and a profile saved by setup can have no title.
    title = position_title(identity, variant.title_descriptor)
    if title:
        L.append("{\\large " + tex(title) + "}\\\\[4pt]")
    L.append("{\\small " + r" $\cdot$ ".join(tex(b) for b in contact_bits(identity)) + "}")
    L.append(r"\end{center}")
    L.append(r"\vspace{2pt}")

    # ── summary ──────────────────────────────────────────────────────────────
    if variant.summary:
        L += [r"\section{Summary}", tex(variant.summary)]

    # ── skills, grouped ──────────────────────────────────────────────────────
    groups = group_skills(skills, index)
    if groups:
        L.append(r"\section{Skills}")
        for label, items in groups:
            L.append(r"\textbf{" + tex(label) + r":} " + tex(", ".join(items))
                     + r"\par\vspace{1.5pt}")

    # ── experience, grouped by employer ──────────────────────────────────────
    # One dated line per job - title, then employer - and undated workstreams
    # beneath it. Workday read each dated workstream as a separate job and took
    # its Job Title from a workstream named "... Mismatch Checker".
    blocks = group_work(entries, identity)
    if blocks:
        L.append(r"\section{Experience}")
        for b in blocks:
            L.append(r"\position{" + tex(position_title(identity, variant.title_descriptor))
                     + "}{" + tex(b["period"]) + "}")
            L.append(r"\company{" + tex(b["employer"])
                     + (", " + tex(b["location"]) if b["location"] else "") + "}")
            practices = practice_items(b["entries"], skills, index)
            if practices:
                L.append(r"\practices{" + tex(", ".join(practices)) + "}")
            for e in b["entries"]:
                L.append(r"\stream{" + tex(heading(e)) + "}")
                lead = lead_line(e, b["start_year"])
                if lead:
                    L.append(r"{\small\itshape " + tex(lead) + r"}\par")
                items = bullets(e)
                if items:
                    L.append(r"\begin{itemize}")
                    L += [r"  \item " + tex(x) for x in items]
                    L.append(r"\end{itemize}")
                stack(e)
                L.append(r"\vspace{3pt}")

    # ── projects ─────────────────────────────────────────────────────────────
    projects = chronological([e for e in entries if e.get("type") == "project"])
    if projects:
        L.append(r"\section{Projects}")
        for e in projects:
            L.append(r"\role{" + tex(heading(e)) + "}{"
                     + tex(entry_period(e)) + "}")
            lead = lead_line(e)
            if lead:
                L.append(r"{\small\itshape " + tex(lead) + r"}\par")
            items = bullets(e)
            if items:
                L.append(r"\begin{itemize}")
                L += [r"  \item " + tex(x) for x in items]
                L.append(r"\end{itemize}")
            stack(e)
            L.append(r"\vspace{3pt}")

    # ── education ────────────────────────────────────────────────────────────
    edu = [e for e in entries if e.get("type") == "education"]
    if edu:
        L.append(r"\section{Education}")
        for e in edu:
            L.append(r"\role{" + tex(e.get("headline")) + "}{"
                     + tex(entry_period(e)) + "}")
            L.append(r"{\small " + tex(e.get("where"))
                     + (r" $\cdot$ " + tex(", ".join(bullets(e))) if bullets(e) else "")
                     + r"}\par")

    # ── certifications & achievements on one compact block ───────────────────
    certs = [e for e in entries if e.get("type") == "certification"]
    if certs:
        L.append(r"\section{Certifications}")
        line = " $\\cdot$ ".join(
            tex(e.get("headline")) + (f" ({tex(e.get('period'))})" if e.get("period") else "")
            for e in certs
        )
        L.append("{\\small " + line + "}")

    awards = [e for e in entries if e.get("type") in ("award", "profile")]
    if awards:
        L.append(r"\section{Achievements}")
        parts: list[str] = []
        for e in awards:
            if e.get("type") == "profile":
                parts += [tex(b) for b in bullets(e)]
            else:
                parts.append(tex(e.get("headline")))
        L.append("{\\small " + " $\\cdot$ ".join(parts) + "}")

    if identity.get("languages"):
        L.append(r"\section{Languages}")
        L.append("{\\small " + tex(", ".join(identity["languages"])) + "}")

    L += ["", r"\end{document}", ""]
    return "\n".join(L)


# ── compiling, and reading the result back ───────────────────────────────────

# Where scripts/install-tectonic.sh puts Tectonic (bin/) and the packages it
# fetches (cache/): inside the checkout, because Render keeps the checkout from
# the build but not the home folder, where Tectonic caches by default.
BUNDLED = Path(__file__).resolve().parents[2] / ".tectonic"


def find_engine() -> str | None:
    """Tectonic: $TECTONIC, then PATH, then the checkout's own copy (the
    hosted build installs it), then the per-user install location.

    Tectonic rather than MiKTeX or TeX Live: one 20 MB binary, no installer,
    and it never stops to ask a question - MiKTeX's default of prompting before
    installing a package can hang a compile nobody is watching.
    """
    env = os.environ.get("TECTONIC")
    if env and Path(env).is_file():
        return env
    found = shutil.which("tectonic")
    if found:
        return found
    bundled = BUNDLED / "bin" / "tectonic"
    if bundled.is_file():
        return str(bundled)
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "tectonic" / "tectonic.exe"
    return str(local) if local.is_file() else None


# First compiles fetch packages, and the package server fails intermittently.
_TRANSIENT = ("failure fetching", "couldn't get it from the internet",
              "error sending request")


def compile_pdf(tex_path: Path, timeout: int = 300, attempts: int = 3
                ) -> tuple[Path | None, str]:
    """(pdf, message). Never raises: no engine is a message, not a crash."""
    engine = find_engine()
    if not engine:
        return None, "no TeX engine - see docs/assembly.md to install Tectonic"
    tex_path = Path(tex_path)
    # hyperref reads <name>.out as its bookmarks file. A stale one - the first
    # attempt here had a captured error message in it - is read as LaTeX and
    # fails the build with "Undefined control sequence".
    tex_path.with_suffix(".out").unlink(missing_ok=True)
    env = None
    if engine == str(BUNDLED / "bin" / "tectonic") and "TECTONIC_CACHE_DIR" not in os.environ:
        env = {**os.environ, "TECTONIC_CACHE_DIR": str(BUNDLED / "cache")}
    detail = ""
    for _ in range(attempts):
        try:
            r = subprocess.run([engine, "-X", "compile", tex_path.name],
                               cwd=tex_path.parent, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return None, f"timed out after {timeout}s"
        pdf = tex_path.with_suffix(".pdf")
        if r.returncode == 0 and pdf.exists():
            return pdf, "compiled"
        out = (r.stderr or "") + (r.stdout or "")
        errors = [l.strip() for l in out.splitlines()
                  if l.startswith(("error", "!")) and "Fontconfig" not in l]
        detail = "; ".join(errors[:3]) or f"exit {r.returncode}"
        if not any(t in out for t in _TRANSIENT):
            break
    return None, detail


def pdf_text(pdf: Path) -> str:
    """The text layer, as a parser reads it - not the source we meant to write."""
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(str(pdf)).pages)


def pdf_pages(pdf: Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(str(pdf)).pages)


def text_layer_problems(text: str) -> list[str]:
    """What looks fine on the page and breaks a keyword search."""
    problems = []
    lig = sorted({m.group(0) for m in re.finditer(r"\w*[ﬀ-ﬆ]\w*", text)})
    if lig:
        problems.append("ligature glyphs: " + ", ".join(lig[:5]))
    split = re.findall(r"\b[A-Za-z]+-\n[a-z]\w*", text)
    if split:
        problems.append("words split across lines: "
                        + ", ".join(s.replace("\n", "") for s in split[:5]))
    return problems


def latex_to_text(src: str) -> str:
    """Rough extraction, for the parse check. Not a TeX engine - just enough to
    assert the name, dates and skills are present as literal text."""
    body = src.split(r"\begin{document}", 1)[-1]
    body = re.sub(r"%.*", " ", body)
    # A section heading on a line of its own, as it is in a compiled PDF.
    # Left inline it came out as "{Experience", and a check that looks for the
    # Experience section found nothing to check.
    body = re.sub(r"\\section\{([^}]*)\}", r"\n\1\n", body)
    body = re.sub(r"\\(?:section|role|position|company|stream|item|textbf|large|LARGE|small|"
                  r"itshape|bfseries|scshape|par|vspace|hfill|newcommand)\b", " ", body)
    body = re.sub(r"\\begin\{[^}]*\}|\\end\{[^}]*\}", " ", body)
    body = re.sub(r"\\[a-zA-Z]+\{?|[{}]", " ", body)
    body = body.replace(r"\&", "&").replace(r"\%", "%").replace(r"\_", "_")
    body = body.replace("$\\cdot$", "·").replace("\\\\", " ")
    return re.sub(r"[ \t]+", " ", body)
