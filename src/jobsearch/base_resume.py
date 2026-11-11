"""The overall resume: one page (or two) from the whole profile, in the person's style.

Built after Setup, before any job is in view. It is where a person sees what
their profile makes and says how they like it (resume_style.py) - and it is
never sent, so rebuilding replaces it instead of adding a variant.

With no posting, the person's own skills stand in for what is asked: core
skills weigh most, then working ones. The same selection and the same checks
as a job resume decide the rest.

**Length is checked on the PDF.** A page count is what a reader sees and the
text alone cannot say it. When the PDF runs past the chosen length, the
resume is rebuilt with less - extras first, then pieces, then bullets, then
projects - until it fits. When it falls short, what the person did not set
themselves grows back, as long as it still fits. Nothing is ever padded: a
profile that cannot fill two pages says so.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterator

from .assemble import (Variant, best_descriptor, emittable, load_store, select_entries,
                       summarize, surfaced_skills)
from .ats import PostingKeywords, Scorer
from .llm import LLM
from .models import utcnow
from .profile import Profile
from . import resume_edits
from .render import bullets, caps, check_text, render_html, render_text, write_outputs
from .resume_layout import group_work, heading, position_title
from .resume_style import RANGES, Style, load
from .skills import CORE, SCOPED, WORKING, SkillIndex, load_index

BASE_ID = "base"
MAX_BUILDS = 8                     # PDF compiles for one fit; each takes seconds
WEIGHT = {CORE: 2.0, WORKING: 1.0, SCOPED: 0.5}

# The order things give way when a page runs long, and the floor for each.
TIGHTEN: list[tuple[str, int]] = [("pieces_per_project", 1), ("piece_bullets", 1),
                                  ("project_bullets", 2), ("side_projects", 0),
                                  ("projects_per_job", 1)]
LOOSEN = ["projects_per_job", "pieces_per_project", "project_bullets", "piece_bullets",
          "side_projects"]
WORDS = {"projects_per_job": "projects per job", "pieces_per_project": "pieces per project",
         "project_bullets": "bullets per project", "piece_bullets": "bullets per piece",
         "side_projects": "side projects"}


def record_path(prof: Profile) -> Path:
    return prof.var / "base-resume.json"


def load_record(prof: Profile) -> dict[str, Any] | None:
    try:
        d = json.loads(record_path(prof).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return d if isinstance(d, dict) else None


def own_skills(index: SkillIndex) -> PostingKeywords:
    """The person's skills as the "posting": what the overall resume answers."""
    weights = {name: WEIGHT[c.tier] for name, c in index.claims.items() if c.tier in WEIGHT}
    claimable = set(weights)
    return PostingKeywords(weights, {k: "required" for k in weights}, claimable, set(),
                           creditable=claimable)


def tighter(style: Style) -> Iterator[tuple[Style, str]]:
    """Each next, smaller style, with what it gave up. Extras go first; then
    one step of each setting in turn, so no one part of the page takes all
    the cuts."""
    if style.extras:
        style = replace(style, extras=False)
        yield style, "extra projects"
    moved = True
    while moved:
        moved = False
        for key, floor in TIGHTEN:
            if getattr(style, key) > floor:
                style = replace(style, **{key: getattr(style, key) - 1})
                moved = True
                yield style, WORDS[key]


def looser(style: Style, chosen: set[str]) -> Iterator[tuple[Style, str]]:
    """Each next, larger style - only in what the person left to the defaults."""
    moved = True
    while moved:
        moved = False
        for key in LOOSEN:
            if key in chosen or getattr(style, key) >= RANGES[key][1]:
                continue
            style = replace(style, **{key: getattr(style, key) + 1})
            moved = True
            yield style, WORDS[key]


def build_base(prof: Profile, llm: LLM | None = None, use_model: bool = True,
               pdf: bool = True, say: Callable[[str], None] = print) -> dict[str, Any]:
    from .latex import compile_pdf, find_engine, pdf_pages, pdf_text, render_latex, text_layer_problems
    from .resume_style import _read

    raw = load_store(prof)
    edits = resume_edits.load(prof)
    store = resume_edits.apply(raw, edits)        # hidden out, wording in, order and pins marked
    index = load_index(prof)
    chosen_style = load(prof)
    chosen = set(_read(prof))
    identity = dict(store.get("identity") or {})
    if not chosen_style.shows("languages"):
        identity["languages"] = []                 # not printed, so not checked
    kw = own_skills(index)
    scorer = Scorer(kw, index)
    approved = list((identity.get("title") or {}).get("approved_descriptors") or [])
    scorer.reach((e for e in (store.get("entries") or []) if emittable(e)), approved)
    descriptor = best_descriptor(approved, kw)

    def page(style: Style) -> tuple[list[dict[str, Any]], list[str]]:
        entries = select_entries(store, index, kw, scorer=scorer, max_projects=style.side_projects,
                                 per_job=style.projects_per_job, pieces=style.pieces_per_project,
                                 extras=style.extras)
        return entries, surfaced_skills(entries, index, kw.claimable, weights=kw.weights)

    entries, skills = page(chosen_style)
    summary, by = None, None
    llm = llm or (LLM(prof, purpose="resume") if use_model else None)
    if use_model and llm is not None and llm.available and chosen_style.shows("summary"):
        say("Writing the two-line summary from your facts…")
        applying = position_title(identity, descriptor) or "the next role"
        summary, by, why = summarize(prof, entries, applying, llm)
        if why:
            say(f"The summary was left out: {why}.")

    def variant(entries: list[dict[str, Any]], skills: list[str]) -> Variant:
        return Variant(id=BASE_ID, angle=BASE_ID, emits=[e["id"] for e in entries], surfaces=skills,
                       title_descriptor=descriptor, summary=summary, summary_by=by)

    out = prof.var / "resumes"
    out.mkdir(parents=True, exist_ok=True)
    tex_path = out / f"{BASE_ID}.tex"

    def write(style: Style) -> tuple[list[dict[str, Any]], list[str], Variant]:
        e, s = page(style)
        v = variant(e, s)
        write_outputs(prof.var, v, render_html(identity, v, e, s, index, style),
                      render_text(identity, v, e, s, index, style))
        tex_path.write_text(render_latex(identity, v, e, s, index, style), encoding="utf-8")
        return e, s, v

    last: dict[str, Any] = {}

    def pages_of(style: Style) -> tuple[int | None, str]:
        """Pages for this style. A step that changes nothing on the page (no
        project had a third piece to drop) reuses the last count: a compile
        takes seconds, and only MAX_BUILDS of them run."""
        write(style)
        src = tex_path.read_text(encoding="utf-8")
        if last.get("src") == src:
            return last["pages"], "unchanged"
        made, msg = compile_pdf(tex_path)
        n = pdf_pages(made) if made else None
        last.update(src=src, pages=n, builds=last.get("builds", 0) + 1)
        return n, msg

    target = None if chosen_style.pages == "any" else int(chosen_style.pages)
    style, note, gave_up = chosen_style, "", []
    engine = pdf and find_engine()
    if pdf and not engine:
        note = "No PDF engine here, so the length wasn't checked."
    elif engine:
        say("Building the PDF…")
        n, msg = pages_of(style)
        if n is None:
            note = f"The PDF wasn't built ({msg})."
        elif target and n > target:
            for smaller, what in tighter(style):
                if last["builds"] >= MAX_BUILDS:
                    break
                say(f"{n} pages - trying fewer {what}…")
                style, gave_up = smaller, gave_up + [what]
                n, _ = pages_of(style)
                if n is not None and n <= target:
                    break
            note = (f"Shortened to fit {target} page{'s' if target > 1 else ''}: fewer "
                    + ", ".join(dict.fromkeys(gave_up)) + "." if n is not None and n <= target
                    else f"Still {n} pages after shortening; hide something in edit mode, or choose more pages.")
        elif target and n < target:
            for bigger, what in looser(style, chosen):
                if last["builds"] >= MAX_BUILDS:
                    break
                say(f"{n} page{'s' if n > 1 else ''} - adding {what}…")
                m, _ = pages_of(bigger)
                if m is None or m > target:
                    break                  # too far: the last one that fit stands
                style, n = bigger, m
                if n >= target:
                    break
            if n < target:
                note = (f"Your profile fills {n} page{'s' if n > 1 else ''}; add more work on your "
                        f"Profile to fill {target}.")
        n_final = n
    entries, skills, v = write(style)
    pdf_file = out / f"{BASE_ID}.pdf"
    checks: dict[str, Any] = check_text(render_text(identity, v, entries, skills, index, style),
                                        identity, entries, skills)
    pages = None
    if engine and pdf_file.exists():
        made, _ = compile_pdf(tex_path)
        if made:
            ptext = pdf_text(made)
            checks = check_text(ptext, identity, entries, skills)
            problems = text_layer_problems(ptext)
            if problems:
                checks["checks"].append({"check": "PDF text layer", "ok": False, "detail": "; ".join(problems)})
                checks["ok"] = False
            pages = pdf_pages(made)
    rec = {"created": utcnow(), "summary": summary, "summary_by": by, "descriptor": descriptor,
           "emits": v.emits, "surfaces": skills, "style": style.to_dict(), "target": chosen_style.pages,
           "pages": pages if pages is not None else (n_final if engine else None), "note": note,
           "checks_ok": bool(checks.get("ok")),
           "failed": [c["check"] for c in checks.get("checks", []) if not c.get("ok")],
           "outline": outline(raw, entries, identity, style, edits)}
    record_path(prof).write_text(json.dumps(rec, indent=2), encoding="utf-8")
    say(f"Done: {len(entries)} entries, {len(skills)} skills"
        + (f", {rec['pages']} page(s)" if rec["pages"] else "") + (f". {note}" if note else "."))
    if not rec["checks_ok"]:
        say("Read-back check failed: " + ", ".join(rec["failed"]))
    return rec


def outline(raw: dict[str, Any], entries: list[dict[str, Any]], identity: dict[str, Any],
            style: Style, edits: dict[str, Any]) -> dict[str, Any]:
    """What is on the page, in the page's order, for edit mode to draw: each
    job's projects and pieces with the bullets that printed (and the store's
    own text behind any reworded one), the side projects, what was left out,
    what the person hid, and the projects a piece could move under."""
    def item(e: dict[str, Any], cap: int | None) -> dict[str, Any]:
        mine = {new: old for old, new in (edits["wording"].get(str(e["id"])) or {}).items()}
        return {"id": e["id"], "title": heading(e), "pinned": bool(e.get("_pinned")),
                "bullets": [{"text": b, "original": mine.get(b, b), "reworded": b in mine}
                            for b in bullets(e, cap)]}

    jobs = []
    for b in group_work(entries, identity):
        projects = []
        for g in b["projects"]:
            own, each = caps(style, bool(g["pieces"]))
            projects.append({**item(g["entry"], own), "pieces": [item(p, each) for p in g["pieces"]]})
        jobs.append({"employer": b["employer"], "projects": projects})
    on_page = {str(e.get("id")) for e in entries}
    every = [e for e in raw.get("entries") or [] if e.get("id") and emittable(e)]
    hidden = set(edits.get("hidden") or [])
    short = lambda e: {"id": e["id"], "title": heading(e), "type": e.get("type"),  # noqa: E731
                       "where": e.get("where") or "", "part_of": e.get("part_of") or ""}
    return {
        "jobs": jobs,
        "side": [item(e, None) for e in entries if e.get("type") == "project"],
        "left_out": [short(e) for e in every if e.get("type") in ("work", "project")
                     and str(e["id"]) not in on_page and str(e["id"]) not in hidden],
        "hidden": [short(e) for e in every if str(e["id"]) in hidden],
        # A piece moves under a project at the same job: work that is not
        # itself a piece.
        "projects": [{"id": e["id"], "title": heading(e), "where": e.get("where") or ""}
                     for e in every if e.get("type") == "work" and not e.get("part_of")],
    }
