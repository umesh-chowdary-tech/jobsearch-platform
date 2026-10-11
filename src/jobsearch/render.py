"""Rendering a variant to a resume, and proving it survives being parsed.

Two outputs. The HTML prints to a PDF with a real text layer; the .txt is the
same content with nothing to go wrong. Both are single-column, no tables, no
text boxes, no images, with contact details in the body rather than a header -
because a header is the first thing a parser drops, and losing a phone number
silently is worse than an ugly resume.

`parse_check` is the point of this module. "Parse safety" as a principle gets
ignored; as an assertion that the name, the email, every employment date and
every emitted skill survive extraction, it fails loudly.
"""
from __future__ import annotations

import html as _html
import re
from pathlib import Path
from typing import Any, Iterable

from .skills import SkillIndex

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")


def strip_html(doc: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", doc, flags=re.S | re.I)
    text = re.sub(r"</(p|div|li|h[1-6]|tr)>", "\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = _TAG.sub("", text)
    text = _html.unescape(text)
    return "\n".join(_WS.sub(" ", l).strip() for l in text.splitlines())


def posting_spelling(canon: str, index: SkillIndex, posting_text: str) -> str:
    """Use the posting's spelling when it means the same thing.

    Store says `Node.js`, posting says `NodeJS`. Both are true and matching
    their string is free - it is vocabulary, not a claim.
    """
    if not posting_text:
        return canon
    target = re.sub(r"[^a-z0-9]", "", canon.lower())
    for alias in sorted(index.aliases.get(canon, set()), key=len, reverse=True):
        # Only a true SPELLING variant qualifies: same letters and digits,
        # different punctuation or case. "Node.js" -> "NodeJS" yes.
        #
        # Matching any alias put `Lambda` on the page in place of AWS and
        # `monitoring` in place of Observability - the posting's word, but a
        # narrower and weaker claim than the one the store actually backs. An
        # alias is good enough to DETECT a skill; it is not good enough to
        # rename it.
        if re.sub(r"[^a-z0-9]", "", alias.lower()) != target:
            continue
        for m in re.finditer(rf"(?<![\w]){re.escape(alias)}(?![\w])", posting_text, re.I):
            # "an agile team" is prose, not the posting's way of writing the
            # name - and every ATS matches case-insensitively, so a lowercase
            # "agile" on the Skills line would gain nothing and read worse.
            if m.group(0).islower() and not canon.islower():
                continue
            return m.group(0)
    return canon


# ── plain text ───────────────────────────────────────────────────────────────

def _period(e: dict[str, Any]) -> str:
    p = e.get("period")
    return str(p) if p else ""


_STOP = {"the", "a", "an", "it", "its", "is", "are", "was", "were", "by", "to",
         "of", "and", "in", "on", "for", "with", "use", "uses", "used", "that"}


def _tokens(s: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9.]+", str(s).lower()) if w and w not in _STOP}


def _same_claim(a: str, b: str, threshold: float = 0.6) -> bool:
    """Do these two sentences say the same thing?

    Substring containment is not enough. The store holds

        fact:   "used by ~3,000 business customers; handles ~2M events a day"
        metric: "~3,000 business customers use it; it handles ~2M events a day"

    which are the same claim with the words reordered, so neither contains the
    other and both landed on the page. Compare the significant tokens instead.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= threshold


# How many bullets each kind of entry earns. A current role justifies depth; a
# side project does not. Uncapped, one model-training project ran to eight
# bullets, including a figure its own notes had already discarded, and a side
# marketplace to seven about escrow - on a tax-provisioning web app posting.
# That is how a three-year resume reaches two pages.
BULLET_CAP = {"work": 5, "project": 3, "education": 1, "certification": 3,
              "award": 1, "profile": 2}


def bullets(e: dict[str, Any]) -> list[str]:
    """Facts, then any measured metric that is not already one of them.

    The store keeps metrics separately so the assembler can drop them when
    empty. But "~3,000 customers use it" is often stored BOTH as a fact and
    as the measured claim, and emitting both put the same sentence on the page
    twice - which reads as padding, the exact opposite of what a measured
    number is for.
    """
    out = [str(f) for f in (e.get("facts") or [])]
    for m in (e.get("metrics") or []):
        if not m.get("measured"):
            continue
        claim = str(m.get("claim", "")).strip()
        if not claim or any(_same_claim(claim, existing) for existing in out):
            continue
        out.append(claim)

    cap = BULLET_CAP.get(str(e.get("type")), 4)
    if len(out) <= cap:
        return out
    # Keep anything carrying a figure. A number is the scarcest thing on the
    # page and the first thing an interviewer asks about.
    #
    # Protecting only `metrics` was not enough: "adopted by 30+ analysts
    # across three teams" lives in `facts`, and trimming cut it
    # from a posting that explicitly asked for cross-team delivery. Where the
    # number is stored is a schema detail; the reader cannot see it.
    def _weight(b: str) -> int:
        return 0 if re.search(r"\d", b) else 1

    kept = sorted(range(len(out)), key=lambda i: (_weight(out[i]), i))[:cap]
    return [out[i] for i in sorted(kept)]


def _start_year(e: dict[str, Any]) -> tuple[int, int]:
    """(year, still-running) for reverse-chronological ordering."""
    p = str(e.get("period") or "")
    years = re.findall(r"(19|20)\d{2}", p)
    year = int(re.search(r"((?:19|20)\d{2})", p).group(1)) if years else 0
    return (year, 1 if "present" in p.lower() else 0)


def chronological(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest first. Relevance decides WHICH entries appear; it must not decide
    the order, or the reader sees 2023 above 2026 and stops trusting the dates.
    """
    return sorted(entries, key=lambda e: (-_start_year(e)[0], -_start_year(e)[1]))


def render_text(identity, variant, entries, skills, index=None) -> str:
    from .resume_layout import (contact_bits, entry_period, group_skills, group_work,
                                heading, lead_line, position_title, practice_items,
                                stack_items)

    def stack(e: dict[str, Any], indent: str) -> None:
        items = stack_items(e, skills, index)
        if items:
            lines.append(f"{indent}Stack: {', '.join(items)}")

    lines = [
        identity["name"],
        position_title(identity, variant.title_descriptor),
        " | ".join(contact_bits(identity)),
    ]
    if variant.summary:
        lines += ["", "SUMMARY", variant.summary]
    if skills:
        lines += ["", "SKILLS"]
        if index is not None:
            for label, items in group_skills(skills, index):
                lines.append(f"{label}: {', '.join(items)}")
        else:
            lines.append(", ".join(skills))

    blocks = group_work(entries, identity)
    if blocks:
        lines += ["", "EXPERIENCE"]
        for b in blocks:
            # Title and dates, then employer: one dated line per job. The
            # workstreams below carry no range, or a parser reads each as a job.
            lines.append(f"{position_title(identity, variant.title_descriptor)}  "
                         f"({b['period']})")
            lines.append(b["employer"] + (f", {b['location']}" if b["location"] else ""))
            practices = practice_items(b["entries"], skills, index)
            if practices:
                lines.append(f"  Practices: {', '.join(practices)}")
            for e in b["entries"]:
                lines.append(f"  {heading(e)}")
                lead = lead_line(e, b["start_year"])
                if lead:
                    lines.append(f"    {lead}")
                for x in bullets(e):
                    lines.append(f"    - {x}")
                stack(e, "    ")

    def simple(head, kinds):
        chosen = chronological([e for e in entries if e.get("type") in kinds])
        if not chosen:
            return
        lines.extend(["", head])
        for e in chosen:
            lines.append(f"  {heading(e)}  [{entry_period(e)}]")
            if lead_line(e):
                lines.append(f"    {lead_line(e)}")
            for x in bullets(e):
                lines.append(f"    - {x}")
            stack(e, "    ")

    simple("PROJECTS", ("project",))
    simple("EDUCATION", ("education",))
    simple("CERTIFICATIONS", ("certification",))

    awards = [e for e in entries if e.get("type") in ("award", "profile")]
    if awards:
        lines += ["", "ACHIEVEMENTS"]
        for e in awards:
            if e.get("type") == "profile":
                lines += [f"  - {b}" for b in bullets(e)]
            else:
                lines.append(f"  - {e.get('headline')}")
    if identity.get("languages"):
        lines += ["", "LANGUAGES", ", ".join(identity["languages"])]
    return "\n".join(lines) + "\n"


# ── html ─────────────────────────────────────────────────────────────────────
#
# Same structure as the LaTeX and the plain text: one employer block with named
# workstreams under it, and skills grouped by category. Emitting each store
# entry as its own top-level heading produced six consecutive blocks for one
# employer, which reads as six jobs rather than one role with depth - and was
# visibly weaker than the hand-written resume it was meant to replace.

_CSS = """
@page { size: A4; margin: 12mm 13mm; }
* { box-sizing: border-box; }
body { font: 10.2pt/1.38 Charter, Georgia, "Times New Roman", serif; color: #111;
       background: #fff; max-width: 200mm; margin: 0 auto; padding: 16px; }
header { text-align: center; margin-bottom: 10px; }
h1 { font-size: 21pt; margin: 0 0 3px; letter-spacing: .01em; }
.role { font-size: 11.5pt; margin: 0 0 4px; }
.contact { font-size: 8.9pt; color: #222; margin: 0; }
h2 { font-size: 9.6pt; text-transform: uppercase; letter-spacing: .09em;
     font-variant: small-caps; border-bottom: .6pt solid #111;
     padding-bottom: 1px; margin: 12px 0 5px; }
p { margin: 0 0 4px; }
.skill { margin-bottom: 2.5px; }
.employer { display: flex; justify-content: space-between; align-items: baseline;
            font-size: 12pt; font-weight: bold; }
.employer .when { font-size: 9.2pt; font-weight: normal; }
.company { margin: 0 0 2px; }
.stream { display: flex; justify-content: space-between; align-items: baseline;
          font-weight: bold; margin-top: 6px; }
.stream .when { font-weight: normal; font-size: 9pt; }
.lead { font-style: italic; font-size: 9.2pt; margin: 1px 0 0; }
ul { margin: 3px 0 0; padding-left: 15px; }
li { margin-bottom: 1.5px; }
.small { font-size: 9.2pt; }
.stack { font-size: 9pt; margin: 1px 0 0 15px; }
a { color: inherit; text-decoration: none; }
@media print { body { padding: 0; max-width: none; } }
"""


def _e(s: Any) -> str:
    return _html.escape(str(s if s is not None else ""))


def _entry_html(e: dict[str, Any], cls: str = "stream", start_year: int | None = None,
                skills: list[str] | None = None, index: SkillIndex | None = None) -> str:
    """`start_year` set = a workstream inside a job: no date range of its own,
    only "(since 2025)" in the lead when it began after the job did."""
    from .resume_layout import entry_period, heading, lead_line, stack_items
    when = "" if start_year is not None else entry_period(e)
    out = [f'<div class="{cls}"><span>{_e(heading(e))}</span>'
           + (f'<span class="when">{_e(when)}</span>' if when else "") + "</div>"]
    lead = lead_line(e, start_year or 0)
    if lead:
        out.append(f'<p class="lead">{_e(lead)}</p>')
    items = bullets(e)
    if items:
        out.append("<ul>" + "".join(f"<li>{_e(b)}</li>" for b in items) + "</ul>")
    stack = stack_items(e, skills or [], index)
    if stack:
        out.append(f'<p class="stack">Stack: {_e(", ".join(stack))}</p>')
    return "".join(out)


def render_html(identity: dict[str, Any], variant, entries: list[dict[str, Any]],
                skills: list[str], index: SkillIndex | None = None) -> str:
    from .resume_layout import (contact_bits, group_skills, group_work, position_title,
                                practice_items)

    contact = " &middot; ".join(_e(x) for x in contact_bits(identity))
    parts = [
        "<header>",
        f"<h1>{_e(identity['name'])}</h1>",
        f'<p class="role">{_e(position_title(identity, variant.title_descriptor))}</p>',
        f'<p class="contact">{contact}</p>',
        "</header>",
    ]

    if variant.summary:
        parts.append(f"<h2>Summary</h2><p>{_e(variant.summary)}</p>")

    if skills:
        parts.append("<h2>Skills</h2>")
        if index is not None:
            for label, items in group_skills(skills, index):
                parts.append(f'<p class="skill"><b>{_e(label)}:</b> '
                             f'{_e(", ".join(items))}</p>')
        else:
            parts.append(f'<p class="skill">{_e(", ".join(skills))}</p>')

    blocks = group_work(entries, identity)
    if blocks:
        parts.append("<h2>Experience</h2>")
        for b in blocks:
            parts.append(f'<div class="employer"><span>{_e(position_title(identity, variant.title_descriptor))}</span>'
                         f'<span class="when">{_e(b["period"])}</span></div>')
            parts.append(f'<p class="company">{_e(b["employer"])}'
                         + (f', {_e(b["location"])}' if b["location"] else "") + "</p>")
            practices = practice_items(b["entries"], skills, index)
            if practices:
                parts.append(f'<p class="stack">Practices: {_e(", ".join(practices))}</p>')
            parts += [_entry_html(e, start_year=b["start_year"], skills=skills, index=index)
                      for e in b["entries"]]

    def block(head: str, kinds: tuple[str, ...]) -> None:
        chosen = chronological([e for e in entries if e.get("type") in kinds])
        if not chosen:
            return
        parts.append(f"<h2>{_e(head)}</h2>")
        parts.extend(_entry_html(e, skills=skills, index=index) for e in chosen)

    block("Projects", ("project",))
    block("Education", ("education",))

    certs = [e for e in entries if e.get("type") == "certification"]
    if certs:
        parts.append("<h2>Certifications</h2>")
        parts.append('<p class="small">' + " &middot; ".join(
            _e(e.get("headline")) + (f' ({_e(e.get("period"))})' if e.get("period") else "")
            for e in certs) + "</p>")

    awards = [e for e in entries if e.get("type") in ("award", "profile")]
    if awards:
        bits: list[str] = []
        for e in awards:
            # A `profile` entry's headline is "Active competitive programmer",
            # which says nothing. Its facts carry the ratings.
            if e.get("type") == "profile":
                bits += [_e(b) for b in bullets(e)]
            else:
                bits.append(_e(e.get("headline")))
        parts.append("<h2>Achievements</h2>")
        parts.append('<p class="small">' + " &middot; ".join(bits) + "</p>")

    if identity.get("languages"):
        parts.append("<h2>Languages</h2>")
        parts.append('<p class="small">' + _e(", ".join(identity["languages"])) + "</p>")

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{_e(identity["name"])} - {_e(variant.title_descriptor or variant.id)}</title>'
        f"<style>{_CSS}</style></head><body>" + "".join(parts) + "</body></html>"
    )


# ── the test that makes "parse safety" mean something ────────────────────────

def parse_check(doc_html: str, identity: dict[str, Any], entries: list[dict[str, Any]],
                skills: list[str]) -> dict[str, Any]:
    """Extract the text back out of HTML and assert nothing important vanished."""
    res = check_text(strip_html(doc_html), identity, entries, skills)
    # A parser that meets a table or an image is where fields start disappearing.
    for tag in ("table", "img", "svg"):
        ok = f"<{tag}" not in doc_html.lower()
        res["checks"].append({"check": f"no <{tag}>", "ok": ok, "detail": ""})
    res["ok"] = all(c["ok"] for c in res["checks"])
    return res


_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
# "Sep 2023 - Present", "2023 - 2024", "Mar 2019 to Jun 2023". Not "since 2025":
# a single year is not a range, which is exactly why workstreams say it that way.
_RANGE = re.compile(
    rf"(?:{_MON}\s+)?(?:19|20)\d{{2}}\s*(?:-|–|—|to)\s*"
    rf"(?:present|current|now|(?:{_MON}\s+)?(?:19|20)\d{{2}})", re.I)
_SECTION_NAMES = ("summary", "skills", "experience", "projects", "education",
                  "certifications", "achievements", "languages")


def experience_section(text: str) -> str:
    """The lines between the Experience heading and the next section heading."""
    out, inside = [], False
    for line in text.splitlines():
        key = line.strip().lower()
        if key in _SECTION_NAMES:
            inside = key == "experience"
            continue
        if inside:
            out.append(line)
    return "\n".join(out)


def check_text(text: str, identity: dict[str, Any], entries: list[dict[str, Any]],
               skills: list[str]) -> dict[str, Any]:
    """The assertions themselves, against already-extracted plain text.

    Split out from parse_check because the LaTeX path was being checked by
    wrapping its text in <body> and running it through the HTML stripper. One
    entry contained the literal string "nano (<10K followers)", so the tag regex
    matched from that "<" to the next ">" and silently deleted everything in
    between - including a whole section. A checker that mangles its own input
    reports failures that are not there, and hides ones that are.
    """
    flat = " ".join(text.split())
    checks: list[tuple[str, bool, str]] = []

    checks.append(("name", identity["name"] in flat, identity["name"]))
    for field in ("email", "phone"):
        v = identity.get(field)
        if v:
            checks.append((field, str(v).replace(" ", "") in flat.replace(" ", ""), str(v)))

    # The YEAR must survive, not the store's raw string. The renderer
    # deliberately reformats "2026-present" into "2026 - Present" for a human
    # reader, and asserting on the storage format tested the wrong thing: an
    # ATS parses dates, it does not care which hyphen we used.
    jobs = {str(r.get("employer", "")).strip().lower(): r
            for r in identity.get("employment") or []}
    missing_dates = []
    for e in entries:
        if not e.get("period"):
            continue
        # A workstream inside a recorded job has no range of its own any more;
        # the job's dates are checked below, as a parser would read them.
        if e.get("type") == "work" and str(e.get("where", "")).strip().lower() in jobs:
            continue
        years = re.findall(r"(?:19|20)\d{2}", str(e["period"]))
        if years and not any(y in flat for y in years):
            missing_dates.append(e["id"])
    checks.append(("employment dates", not missing_dates, ", ".join(missing_dates) or "all present"))

    # ── how an ATS segments Experience ───────────────────────────────────
    # All three found by a Workday parse of a real application, none by the
    # checks above: the resume read well and parsed wrong.
    employers = {str(e.get("where", "")).strip() for e in entries if e.get("type") == "work"}
    if employers:
        exp = experience_section(text)
        band = str((identity.get("title") or {}).get("official") or "")
        if band:
            checks.append(("job title in experience", band.lower() in " ".join(exp.split()).lower(),
                           band if band else "no title"))
        ranges = _RANGE.findall(exp)
        checks.append(("one dated line per job", len(ranges) == len(employers),
                       f"{len(ranges)} date ranges for {len(employers)} employer(s)"
                       + ("" if len(ranges) == len(employers)
                          else " - a parser reads every dated line as a job")))
        from .resume_layout import _month
        for name in sorted(employers):
            job = jobs.get(name.lower())
            if job:
                start = _month(str(job.get("start")))
                checks.append((f"start month ({name})", start in " ".join(exp.split()),
                               start + ("" if " " in start else " - no month, read as January")))

    missing_skills = [s for s in skills if s.lower() not in flat.lower()]
    checks.append(("skills", not missing_skills, ", ".join(missing_skills) or "all present"))

    langs = [str(l) for l in identity.get("languages") or []]
    if langs:
        missing = [l for l in langs if l not in flat]
        checks.append(("languages", not missing, ", ".join(missing) or ", ".join(langs)))

    return {
        "ok": all(ok for _, ok, _ in checks),
        "checks": [{"check": n, "ok": ok, "detail": d} for n, ok, d in checks],
        "chars": len(flat),
    }


def write_outputs(var_dir: Path, variant, doc_html: str, text: str) -> dict[str, Path]:
    """The resume files, in the person's var/<id>/resumes/."""
    out = Path(var_dir) / "resumes"
    out.mkdir(parents=True, exist_ok=True)
    h = out / f"{variant.id}.html"
    t = out / f"{variant.id}.txt"
    h.write_text(doc_html, encoding="utf-8")
    t.write_text(text, encoding="utf-8")
    return {"html": h, "txt": t}
