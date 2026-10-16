"""Triage pass 3 — a model reads the shortlist.

Passes 1 and 2 are keyword work: they know that a posting names Python and that
Python is backed. What they cannot see is that "take our AI platform from
prototype to production" is a near-perfect description of the headline entry,
with no keyword overlap at all.

**The model has no write access to claims.** It ranks and explains; it never
adds a skill, never edits the store, and never touches a resume. Every fact it
is given comes from `master-store.yaml`, and the only thing it returns is a
judgement plus a sentence. That keeps "never claim an unearned keyword" true by
construction rather than by instruction.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import yaml

from .busy import stop_requested
from .llm import LLM, Answer
from .pool import within_window
from .profile import Profile

FIT_ORDER = {"strong": 3, "possible": 2, "weak": 1}
# Stamped on every judgement. One made under older rules is stale and is read
# again, like an unread one: after the level rules arrived, a re-read that
# lost 52 requests to rate limits left dozens of "strong" fits judged without
# knowing the engineer's level, and nothing marked which. Bump when the
# prompt or the level line changes what a fit means.
PROMPT_VERSION = "level-3"      # level-3: the level line and its limits come from the profile

SYSTEM = """You triage job postings for one person looking for work.

You judge FIT ONLY. You are given a profile of what this person has actually
done. You must not assume any skill, tool or experience that is not in that
profile - if a posting needs something the profile does not show, that counts
against fit, it is not something to be generous about.

For each posting return:
  id    - exactly the id given
  fit   - "strong" | "possible" | "weak"
  why   - ONE sentence, under 25 words, naming something specific from THIS
          posting and something specific from the profile. No generic praise.

"strong" means the posting's core work matches work the person has already
done AND the level is within reach. "possible" means a real overlap with a
real gap. "weak" means the overlap is incidental.

LEVEL decides as much as the work. The LEVEL line gives the person's years of
experience, level and degrees, the most years a posting may ask, and the
titles out of reach; each posting gives its stated minimum years. A posting
that asks for more years than that most, or whose title is out of reach, is
at most "weak" however well the work matches - applying is a wasted
application. One that needs a degree the person does not have (a Master's or
a PhD, say) is at most "possible". Read the description for these; a minimum
stated there counts even if the summary line says "not stated".

The "why" is the whole point. A list of matched keywords is useless - the
caller already computed that. Name the WORK.

BAD:  "Matches Python and AWS"
BAD:  "Strong alignment with the candidate's background"
GOOD: "Wants a payments API rebuilt for scale; the profile shows the same
       rebuild of an order service, but no Kubernetes"
GOOD: "Asks for an internal ML platform taken to production; the profile did
       that for an analytics team in under six months"
GOOD: "Mobile checkout work matches the profile's, but it asks for 10+ years
       against four" (fit: weak)

Any "why" that would read identically for a different posting is wrong."""


def profile_digest(store: dict[str, Any]) -> str:
    """Every work and project entry. Facts only - no adjectives.

    This was capped at 9 entries, sorted work-first. With 8 work entries,
    exactly one project survived and a whole ML portfolio - a fine-tune and
    two trained models - never reached the model. It duly judged a
    machine-learning posting as "lacks PyTorch/transformer fine-tuning
    experience", which was a correct inference from a profile that had been
    silently truncated.

    A partial profile does not produce a slightly worse ranking. It produces a
    confident wrong one. So: no cap. Trim facts per entry instead, which costs
    detail rather than whole capabilities.
    """
    entries = [
        e for e in (store.get("entries") or [])
        if e.get("type") in ("work", "project")
        and e.get("confidence") != "low"
        and e.get("status") != "parked"
    ]
    order = {"work": 0, "project": 1}
    entries.sort(key=lambda e: (order.get(e.get("type"), 9), e.get("id", "")))

    lines = []
    for e in entries:
        lines.append(f"- {e.get('headline')} ({e.get('where')}, {e.get('period') or 'n/a'})")
        for f in (e.get("facts") or [])[:3]:
            lines.append(f"    - {f}")
        for m in (e.get("metrics") or []):
            if m.get("measured"):
                lines.append(f"    - measured: {m.get('claim')}")
        tech = ", ".join(e.get("tech") or [])
        if tech:
            lines.append(f"    - tech: {tech}")
    return "\n".join(lines)


# A postgraduate degree, by the words a degree line uses for one.
_POSTGRAD = re.compile(r"\b(?:masters?|master's|m\.\s?tech|mtech|m\.\s?e\.|m\.\s?s\.|msc|m\.\s?sc|mba|mca|"
                       r"ph\.?\s?d|doctorate)", re.I)


def candidate_level(prof: Profile, store: dict[str, Any]) -> tuple[str, int, int]:
    """(the line the model reads about level, years of experience, the most
    years a posting may ask). Years, level and the titles out of reach come
    from search-profile.yaml, the same values pass 1 filters on - so the model
    and the filter can't disagree."""
    path = prof.file("search-profile.yaml")
    search = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}
    sen = search.get("seniority") or {}
    years, cap = int(sen.get("years_experience", 0)), int(sen.get("max_years_required", 99))
    level = str(sen.get("level") or "").strip()
    out_of_reach = [str(t) for t in sen.get("reject_titles") or []]

    def job(j: dict[str, Any]) -> str:
        end = str(j.get("end") or "present")
        when = f"since {j.get('start')}" if end == "present" else f"{j.get('start')} to {end}"
        return f"{j.get('title')} at {j.get('employer')} {when}"

    jobs = "; ".join(job(j) for j in (store.get("identity") or {}).get("employment") or [])
    degrees = [str(e.get("headline")) for e in store.get("entries") or [] if e.get("type") == "education"]
    postgrad = any(_POSTGRAD.search(d) for d in degrees)
    line = (f"LEVEL: {years} years of professional experience ({jobs or 'n/a'})"
            + (f", {level}" if level else "") + ". "
            + f"Degree: {', '.join(degrees) or 'n/a'}" + ("" if postgrad else "; no Master's, no PhD") + ". "
            + f"Postings asking for more than {cap} years are out of reach."
            + (f" Titles out of reach: {', '.join(out_of_reach)}." if out_of_reach else ""))
    return line, years, cap


def asked_years(p: dict[str, Any]) -> int | None:
    """The minimum years a posting asks, from its WHOLE description where
    triage recorded it. Recomputed from the stored excerpt, the head of an
    Amazon posting said "not stated" while its qualifications asked for 5."""
    from .triage import required_years
    if "years_required" in p:
        return p["years_required"]
    return required_years(f"{p.get('title')}\n{p.get('description') or ''}\n{p.get('requirements') or ''}")


def _posting_block(p: dict[str, Any], head_chars: int = 600, req_chars: int = 1200) -> str:
    """The posting as the model reads it: the head of the description, then
    its requirements section - where years and degrees are written."""
    head = (p.get("description") or "").strip().replace("\n\n", "\n")[:head_chars]
    req = (p.get("requirements") or "").strip().replace("\n\n", "\n")[:req_chars]
    asked = asked_years(p)
    return (
        f"### id: {p.get('id')}\n"
        f"{p.get('company')} — {p.get('title')} ({p.get('location') or 'location unstated'})\n"
        f"stated minimum experience: {f'{asked} years' if asked else 'not stated'}\n"
        f"keyword coverage {p.get('coverage')}, matched: "
        f"{', '.join(sorted((p.get('matched') or {}))) or 'none'}\n"
        f"gaps: {', '.join(p.get('required_gaps') or []) or 'none'}\n"
        f"{head}\n"
        + (f"REQUIREMENTS:\n{req}\n" if req else "REQUIREMENTS: none found in the posting\n")
    )


BATCH = 4
# A posting the model was shown this many times and never judged stops being
# offered: "read every unread posting" must not become a call every cycle.
MAX_TRIES = 3


def _rank_batch(
    llm: LLM, digest: str, batch: list[dict[str, Any]]
) -> Answer:
    # The profile is the same in every batch: Claude caches it, so only the
    # first batch of a run pays for it in full. Others get it prepended - the
    # same text as before.
    prefix = "PROFILE - everything this engineer can defend:\n" + digest + "\n\nPOSTINGS:\n"
    user = (
        "\n".join(_posting_block(p) for p in batch)
        + '\n\nReturn {"rankings": [{"id": "...", "fit": "...", "why": "..."}]} '
        f"with exactly {len(batch)} entries, one per id above."
    )
    # Reasoning models spend tokens before the answer, so the budget has to
    # cover the thinking as well as the JSON. Too tight and the completion is
    # truncated mid-object, which reads as "reply was not JSON" and silently
    # costs the faster provider.
    return llm.ask_json(SYSTEM, user, max_tokens=700 * len(batch) + 1200, cached_prefix=prefix)


def rank_shortlist(
    prof: Profile, postings: list[dict[str, Any]], llm: LLM | None = None,
    deadline: float | None = None,
    on_batch: Callable[[list[dict[str, Any]], set[str]], None] | None = None,
) -> tuple[list[dict[str, Any]], Answer]:
    """Annotate each posting with an `ai` judgement. Returns (postings, answer).

    `deadline` (a time.monotonic() value) is checked before each request; the
    postings not reached are left as they were, for the next run. A rate limit
    stops the run too - the next request would be refused the same way.
    `on_batch(batch, judged_ids)` runs after each answered request, so a caller
    can save as it goes. `answer.stopped` says why a run ended early."""
    llm = llm or LLM(prof, purpose="rank")
    store = yaml.safe_load(prof.file("master-store.yaml").read_text(encoding="utf-8")) or {}

    if not postings:
        return postings, Answer(
            ok=False, failures=[{"provider": "-", "error": "nothing to rank"}]
        )

    level, years, cap = candidate_level(prof, store)
    digest = level + "\n" + profile_digest(store)
    by_id = {str(p.get("id")): p for p in postings}
    combined = Answer(ok=False)

    # Small batches. One call for everything produced a long reply that models
    # truncate and pad with generic reasons; four at a time keeps each judgement
    # specific and each completion inside the budget.
    for i in range(0, len(postings), BATCH):
        if deadline is not None and time.monotonic() >= deadline:
            combined.stopped = "time budget"
            break
        if stop_requested(prof.shared):
            combined.stopped = "stopped by you"
            break
        batch = postings[i : i + BATCH]
        ans = _rank_batch(llm, digest, batch)
        combined.failures.extend(ans.failures)
        if ans.limited and not ans.ok:
            combined.limited = combined.stopped = ans.limited
            break
        if not ans.ok or not ans.data:
            continue
        combined.ok = True
        combined.provider, combined.model = ans.provider, ans.model
        answered = [str(p.get("id")) for p in batch]
        judged: set[str] = set()

        rankings = ans.data.get("rankings")
        if not isinstance(rankings, list):
            # Some models nest or rename the list. Find the first list of dicts.
            rankings = next(
                (v for v in ans.data.values()
                 if isinstance(v, list) and v and isinstance(v[0], dict)),
                [],
            )
        for r in rankings:
            p = by_id.get(str(r.get("id")))
            if not p:
                continue
            fit = str(r.get("fit", "")).lower().strip()
            if fit not in FIT_ORDER:
                continue
            why = str(r.get("why", ""))
            # The model is told the level; this makes it so. A posting asking
            # more years than the profile allows is never better than weak,
            # whatever the model says - one 10+ years posting came back "strong".
            asked = asked_years(p)
            if asked and asked > cap and FIT_ORDER[fit] > FIT_ORDER["weak"]:
                fit, why = "weak", f"Asks for {asked}+ years against {years}. {why}"
            p["ai"] = {"fit": fit, "why": why[:220], "by": ans.attribution, "prompt": PROMPT_VERSION}
            judged.add(str(p.get("id")))
        # Seen and not judged - the model dropped it from its list. A failed
        # request is not counted: that was the key or the network, not it.
        for pid in answered:
            p = by_id[pid]
            if pid in judged:
                p.pop("ai_tries", None)
            else:
                p["ai_tries"] = int(p.get("ai_tries") or 0) + 1
        if on_batch:
            on_batch(batch, judged)
    return postings, combined


# Which model a judgement can be trusted from, best first. A judgement by a
# weaker one than the model now ranking is read again: in a blind trial of 40
# postings, Groq agreed with Claude on 18, rated 14 "strong" against Claude's 5, and twice credited the profile
# with work it doesn't have. A model not listed here is taken as current.
TRUST = {"claude": 2, "groq": 1, "openrouter": 1}
# Which of a weaker model's verdicts are worth paying to read again. In the
# trial none of Groq's 13 "weak" came back better than "possible" (5 did),
# while 10 of its 14 "strong" came back lower - and Claude's budget buys about
# eight readings a day. So "weak" stands until the posting changes.
REREAD = ("strong", "possible")


def ranking_lead(prof: Profile | None = None) -> str:
    """The provider that judges postings now: the first in the "rank" route of
    data/models.yaml, else the first in LLM_PROVIDERS."""
    if prof:
        from .llm import load_dotenv, load_routes
        load_dotenv(prof.env)
        route = load_routes(prof.root).get("rank")
        if route:
            return next(iter(route))
    import os
    order = [x.strip().lower() for x in os.environ.get("LLM_PROVIDERS", "groq,openrouter").split(",")]
    return next((x for x in order if x), "")


def judged_by(ai: dict[str, Any]) -> str:
    """"openai/gpt-oss-120b via groq" -> "groq"."""
    return str(ai.get("by") or "").rsplit(" via ", 1)[-1].strip()


def superseded(ai: dict[str, Any], lead: str) -> bool:
    prov = judged_by(ai)
    return (prov in TRUST and TRUST[prov] < TRUST.get(lead, 0)
            and ai.get("fit") in REREAD)


def floor_for(prof: Profile) -> int:
    """The offline match score a posting needs for a model to read it
    (scoring.AI_MIN, or the profile's scoring.ai_min)."""
    from .scoring import ai_min
    from .triage import load_profile
    try:
        return ai_min(load_profile(prof.dir))
    except (OSError, ValueError):
        from .scoring import AI_MIN
        return AI_MIN


def unread(blob: dict[str, Any], lead: str = "", floor: int = 0) -> list[dict[str, Any]]:
    """Kept, still open, posted in the pool's window, at or above `floor` on
    the offline match score, and not judged under the current rules - never,
    under an older PROMPT_VERSION, or by a model less trusted than `lead`, the
    one ranking now. A posting scored before the match score existed has none,
    and is read.

    Order: what the weaker model called "strong" first - those are the ones
    acted on - then the never-read, then its "possible". Its "weak" stands
    (REREAD). Best keyword score first within each."""
    def stale(p: dict[str, Any]) -> bool:
        ai = p.get("ai") or {}
        return ai.get("prompt") != PROMPT_VERSION or (bool(lead) and superseded(ai, lead))

    def order(p: dict[str, Any]) -> tuple[int, int, int]:
        ai = p.get("ai") or {}
        rank = 1
        if ai and lead and superseded(ai, lead):
            rank = {"strong": 0, "possible": 2}.get(ai.get("fit", ""), 1)
        elif ai.get("fit") == "weak":
            rank = 3                     # judged weak under older rules: last
        return rank, -int(p.get("match") or 0), -int(p.get("score", 0))

    out = [p for p in (blob.get("postings") or {}).values()
           if p.get("verdict") == "keep" and not p.get("closed_at") and within_window(p)
           and (p.get("match") is None or int(p["match"]) >= floor)
           and stale(p) and int(p.get("ai_tries") or 0) < MAX_TRIES]
    return sorted(out, key=order)


def sort_key(p: dict[str, Any]) -> tuple[int, int, int]:
    """Model judgement first, then the offline match score, then the keyword
    score.

    Postings the model never saw keep their offline rank rather than sinking -
    an un-ranked posting is not a bad posting.
    """
    ai = p.get("ai") or {}
    return (-FIT_ORDER.get(ai.get("fit", ""), 0), -int(p.get("match") or 0), -int(p.get("score", 0)))


def apply_to_results(prof: Profile, limit: int | None = None, llm: LLM | None = None,
                     reread: bool = False, budget: float | None = None,
                     first_llm: LLM | None = None) -> dict[str, Any]:
    """Judge every kept posting no model has read yet, four to a request, and
    write the judgements back. `reread` judges all kept postings again - for
    after the profile changes; otherwise a posting is read once.

    It used to take the top ten by keyword score, read or not: the same ten
    were re-judged each time and 37 kept postings were never read at all.

    `budget` is seconds of requests to start (a request already sent finishes,
    so a run can overshoot by one). The cycle passes one: after the PC slept a
    week, 167 postings were waiting, and reading them all in one cycle held the
    watcher and the inbox for as long as it took. Each answered request is
    saved at once, so a run cut short - by the budget, a limit, or the PC
    going to sleep - keeps what it got.

    Two passes when the model ranking now is a paid one (`first_llm`, or
    RANK_FIRST_PASS - Groq when Claude leads): the free model reads what no one
    has, then the paid one re-reads what the free one called strong, then
    possible, within its daily cap. Claude alone reads about eight postings a
    day on its budget, and about forty new ones are kept a day - read by Claude
    only, new postings would pile up unread. The first pass gets half the time."""
    path = prof.var / "triage-results.json"
    if not path.exists():
        return {"ok": False, "error": "no triage results - run `triage` first"}
    blob = json.loads(path.read_text(encoding="utf-8"))
    lead = _lead_of(llm, prof)
    if reread:
        # A closed posting is not worth a model call, however well it fits.
        passes = [(llm, sorted((p for p in (blob.get("postings") or {}).values()
                                if p.get("verdict") == "keep" and not p.get("closed_at")
                                and within_window(p)),
                               key=lambda p: -int(p.get("score", 0))))]
    else:
        floor = floor_for(prof)
        todo = unread(blob, lead=lead, floor=floor)
        first = first_llm or (_first_pass(prof, lead) if llm is None else None)
        if first is not None:
            passes = [(first, [p for p in todo if not superseded(p.get("ai") or {}, lead)]), (llm, None)]
        else:
            passes = [(llm, todo)]
    start = time.monotonic()
    out: dict[str, Any] = {"ok": False, "attribution": [], "ranked": 0, "considered": 0,
                           "unranked": [], "failures": [], "stopped": [], "left": 0}
    for n, (model, top) in enumerate(passes):
        if top is None:                          # what the first pass left for the paid one
            blob = json.loads(path.read_text(encoding="utf-8"))
            top = [p for p in unread(blob, lead=lead, floor=floor_for(prof))
                   if superseded(p.get("ai") or {}, lead)]
        top = top[:limit] if limit else top
        if not top:
            continue
        deadline = None
        if budget:
            deadline = start + (budget / 2 if len(passes) > 1 and n == 0 else budget)
        res = _one_pass(prof, path, top, model, deadline)
        out["ok"] = out["ok"] or res["ok"]
        out["ranked"] += res["ranked"]
        out["considered"] += len(top)
        out["unranked"] += res["unranked"]
        out["failures"] += res["failures"]
        if res["ranked"] and res["attribution"] not in out["attribution"]:
            out["attribution"].append(res["attribution"])
        if res["stopped"]:
            out["stopped"].append(res["stopped"])
    if not out["considered"]:
        out["ok"] = True
    out["attribution"] = " + ".join(out["attribution"]) or "No AI involved"
    out["stopped"] = "; ".join(out["stopped"])
    out["left"] = len(unread(json.loads(path.read_text(encoding="utf-8")), lead=lead, floor=floor_for(prof)))
    return out


def _one_pass(prof: Profile, path: Path, top: list[dict[str, Any]], llm: LLM | None,
              deadline: float | None) -> dict[str, Any]:
    judged_now: set[str] = set()
    answered: set[str] = set()

    def save(batch: list[dict[str, Any]], judged: set[str]) -> None:
        # Read again: the file is the one the watcher's triage writes too.
        fresh = json.loads(path.read_text(encoding="utf-8"))
        for p in batch:
            fresh["postings"][str(p["id"])] = p      # a judgement, or a try counted
        _write(path, fresh)
        judged_now.update(judged)
        answered.update(str(p["id"]) for p in batch)

    ranked, ans = rank_shortlist(prof, top, llm=llm, deadline=deadline, on_batch=save)

    # Models drop entries from a list. Name the ones that got no judgement
    # rather than reporting "7 of 8" and leaving the 8th to be wondered about -
    # among the postings this run reached, not the ones it never got to.
    missed = [f"{p.get('company')} — {p.get('title')}" for p in ranked
              if str(p.get("id")) in answered - judged_now]
    blob = json.loads(path.read_text(encoding="utf-8"))
    blob["ai"] = {
        "attribution": ans.attribution,
        "ranked": sum(1 for p in ranked if p.get("ai")),
        "unranked": missed,
        "failures": ans.failures,
        "stopped": ans.stopped,
    }
    _write(path, blob)
    return {"ok": ans.ok, "attribution": ans.attribution, "ranked": len(judged_now),
            "unranked": missed, "failures": ans.failures, "stopped": ans.stopped}


def _first_pass(prof: Profile, lead: str) -> LLM | None:
    """The free model that reads new postings before the paid one checks them -
    RANK_FIRST_PASS, else Groq when the lead is trusted above it. None when
    there is no such model, or it has no key."""
    import os
    name = (os.environ.get("RANK_FIRST_PASS") or ("groq" if TRUST.get(lead, 0) > TRUST["groq"] else "")).strip()
    if not name or name == lead:
        return None
    first = LLM(prof, providers=[name], purpose="rank")
    return first if first.available else None


def _lead_of(llm: LLM | None, prof: Profile) -> str:
    chain = getattr(llm, "chain", None)
    if llm is not None and chain:
        return chain[0].name
    if llm is not None:                 # a stand-in with no chain: no provider to compare
        return ""
    return ranking_lead(prof)


def _write(path: Path, blob: dict[str, Any]) -> None:
    """Whole or not at all: the dashboard may read the file mid-run. Windows
    refuses to replace a file another process has open, so a reader caught
    mid-read gets a moment to finish, then a plain write."""
    tmp = path.with_suffix(".tmp")
    text = json.dumps(blob, indent=2, sort_keys=True)
    tmp.write_text(text, encoding="utf-8")
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.2)
    path.write_text(text, encoding="utf-8")
    tmp.unlink(missing_ok=True)
