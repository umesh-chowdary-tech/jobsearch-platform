"""The `assemble` subcommand."""
from __future__ import annotations

import json
from typing import Any

from .assemble import build, load_library, load_store, save_library
from .latex import (compile_pdf, latex_to_text, pdf_pages, pdf_text, render_latex,
                    text_layer_problems)
from .llm import LLM
from .pool import within_window
from .profile import Profile
from .render import (check_text, parse_check, posting_spelling, render_html,
                     render_text, write_outputs)
from .skills import load_index


def _load_postings(prof: Profile) -> dict[str, dict[str, Any]]:
    p = prof.var / "triage-results.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("postings") or {}
    except (json.JSONDecodeError, OSError):
        return {}


def run_assemble(prof: Profile, args) -> int:
    index = load_index(prof)
    store = load_store(prof)
    identity = store.get("identity") or {}
    postings = _load_postings(prof)

    if not postings:
        print("no triaged postings - run `watch` then `triage`", flush=True)
        return 1

    if args.posting:
        chosen = [p for pid, p in postings.items() if pid == args.posting]
        if not chosen:
            print(f"no posting with id {args.posting}")
            return 1
    else:
        from .rank import sort_key
        keeps = [p for p in postings.values()
                 if p.get("verdict") == "keep" and not p.get("closed_at") and within_window(p)]
        keeps.sort(key=sort_key)
        chosen = keeps[: args.top]

    if not chosen:
        print("nothing kept to assemble for")
        return 1

    llm = LLM(prof, purpose="resume")
    if args.no_model:
        llm.chain = []

    failures = 0
    for posting in chosen:
        variant, entries, decision = build(
            prof, store, index, posting, llm=llm, use_model=not args.no_model
        )

        # Vocabulary: use the posting's spelling where it means the same thing.
        ptext = f"{posting.get('title','')} {posting.get('description','')}"
        skills = [posting_spelling(s, index, ptext) for s in variant.surfaces]

        doc = render_html(identity, variant, entries, skills, index)
        text = render_text(identity, variant, entries, skills, index)
        tex_src = render_latex(identity, variant, entries, skills, index)
        check = parse_check(doc, identity, entries, skills)

        # The LaTeX has to survive extraction too, or a clean-looking PDF can
        # still lose a field. Checked against the same assertions, on text
        # pulled back out of the .tex source.
        tex_check = check_text(latex_to_text(tex_src), identity, entries, skills)
        variant.parse_check = check
        paths = write_outputs(prof.var, variant, doc, text)
        tex_path = paths["html"].with_suffix(".tex")
        tex_path.write_text(tex_src, encoding="utf-8")
        paths["tex"] = tex_path

        # The PDF is what gets uploaded, so the PDF is what gets checked: its
        # own text layer, through the same assertions, plus the problems only
        # a compiled page has (ligature glyphs, words split across lines).
        pdf_line = ""
        if not args.no_pdf:
            pdf, msg = compile_pdf(tex_path)
            if pdf:
                ptext = pdf_text(pdf)
                pdf_check = check_text(ptext, identity, entries, skills)
                problems = text_layer_problems(ptext)
                ok = sum(c["ok"] for c in pdf_check["checks"])
                total = len(pdf_check["checks"])
                paths["pdf"] = pdf
                pdf_line = f"pdf {ok}/{total} pass, {pdf_pages(pdf)} page(s)"
                if ok < total or problems:
                    failures += 1
                    bad_pdf = [c["check"] for c in pdf_check["checks"] if not c["ok"]]
                    pdf_line = "PDF FAILED: " + "; ".join(bad_pdf + problems)
            else:
                pdf_line = f"pdf not built ({msg})"

        # build() saved the library before this check existed, and it holds its
        # own objects - so write the result back by id rather than assuming
        # `variant` is the same object the library has.
        lib = load_library(prof)
        for v in lib:
            if v.id == variant.id:
                v.parse_check = check
        save_library(prof, lib)

        print(f"\n  {posting.get('company')} — {posting.get('title')}")
        print(f"    variant   {variant.id}" + (f"  ({variant.title_descriptor})" if variant.title_descriptor else ""))
        print(f"    decision  {decision}")
        ats = (variant.sent_to[-1].get("ats") if variant.sent_to else None) or {}
        if ats:
            print(f"    ats       {ats['match']:.0%} of what the posting asks for"
                  f" (the store allows at most {ats['ceiling']:.0%})")
            if ats.get("missing"):
                print(f"              in the store, not on this page: {', '.join(ats['missing'])}")
            if ats.get("unclaimable"):
                print(f"              asked for, nothing to claim: {', '.join(ats['unclaimable'])}")
        print(f"    emits     {len(entries)} entries, {len(skills)} skills")
        if variant.summary:
            print(f"    summary   {variant.summary[:110]}")
            print(f"              written by {variant.summary_by}, numbers verified")
        else:
            print("    summary   none (no model, or it was rejected)")
        bad = [c for c in check["checks"] if not c["ok"]]
        if bad:
            failures += 1
            print(f"    PARSE     FAILED: {', '.join(c['check'] for c in bad)}")
            for c in bad:
                print(f"                {c['check']}: {c['detail']}")
        else:
            print(f"    parse     html {len(check['checks'])}/{len(check['checks'])} pass"
                  f"  ({check['chars']} chars)   "
                  f"latex {sum(c['ok'] for c in tex_check['checks'])}/"
                  f"{len(tex_check['checks'])} pass")
        if pdf_line:
            print(f"    {pdf_line}")
        print(f"    wrote     {paths['html']}  {paths['txt'].name}  {paths['tex'].name}"
              + (f"  {paths['pdf'].name}" if "pdf" in paths else ""))

    lib = load_library(prof)
    print(f"\n  library: {len(lib)} variant(s) — {', '.join(v.id for v in lib)}")
    return 1 if failures else 0
