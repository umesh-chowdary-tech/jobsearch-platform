"""What a person changed on their resume by hand: hidden, always in, order, wording.

All of it is for the resume only. Hiding never deletes: the entry stays on the
Profile page, and Unhide brings it back. A reworded bullet changes the page,
not the fact - the Profile keeps the original, and a reword stops applying if
the fact itself is later changed there (it is keyed by the original's text).

A new number in a reworded bullet is the one thing that needs more than the
person's say-so: only a measured result is ever printed as a number, so the
reword is refused when the number is an estimate (render.py, onboard.py).

Kept in the profile (`resume-edits.yaml`), so it goes with the data export.
"""
from __future__ import annotations

import copy
from typing import Any

import yaml

from .grounding import numbers_grounded
from .profile import Profile

EDITS_FILE = "resume-edits.yaml"
MAX_WORDING = 400                   # one bullet, not a paragraph


class EditError(ValueError):
    pass


def load(prof: Profile) -> dict[str, Any]:
    try:
        d = yaml.safe_load((prof.dir / EDITS_FILE).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    return {"hidden": [str(x) for x in d.get("hidden") or []],
            "pinned": [str(x) for x in d.get("pinned") or []],
            "rank": {str(k): int(v) for k, v in (d.get("rank") or {}).items() if str(v).lstrip("-").isdigit()},
            "wording": {str(k): {str(a): str(b) for a, b in (v or {}).items()}
                        for k, v in (d.get("wording") or {}).items() if isinstance(v, dict)}}


def _save(prof: Profile, d: dict[str, Any]) -> dict[str, Any]:
    prof.dir.mkdir(parents=True, exist_ok=True)
    (prof.dir / EDITS_FILE).write_text(yaml.safe_dump(d, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return d


def _entries(store: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(e.get("id")): e for e in store.get("entries") or [] if e.get("id")}


def _entry(store: dict[str, Any], eid: str) -> dict[str, Any]:
    e = _entries(store).get(str(eid))
    if e is None:
        raise EditError("no such piece of work - rebuild your resume and try again")
    return e


def set_flag(prof: Profile, store: dict[str, Any], eid: str, flag: str, on: bool) -> dict[str, Any]:
    """Hide an entry from resumes, or keep it on them always. One excludes the
    other: hiding something unpins it, and pinning unhides it."""
    if flag not in ("hidden", "pinned"):
        raise EditError("flag is hidden or pinned")
    _entry(store, eid)
    d = load(prof)
    other = "pinned" if flag == "hidden" else "hidden"
    d[flag] = [x for x in d[flag] if x != eid] + ([eid] if on else [])
    if on:
        d[other] = [x for x in d[other] if x != eid]
    return _save(prof, d)


def set_order(prof: Profile, store: dict[str, Any], ids: list[str]) -> dict[str, Any]:
    """The order of one list on the page - a job's projects, or one project's
    pieces - as the person arranged it."""
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise EditError("order must be a list of ids")
    for i in ids:
        _entry(store, i)
    d = load(prof)
    d["rank"].update({i: n for n, i in enumerate(ids)})
    return _save(prof, d)


def bullets_of(e: dict[str, Any]) -> list[str]:
    """What may be reworded: the entry's facts and its measured results."""
    return [str(f) for f in e.get("facts") or []] + [
        str(m.get("claim")) for m in e.get("metrics") or [] if m.get("measured") and m.get("claim")]


def reword(prof: Profile, store: dict[str, Any], eid: str, original: str, text: str,
           measured: bool | None = None) -> dict[str, Any]:
    """The person's own wording for one bullet, on resumes only. Empty text
    puts the original back."""
    e = _entry(store, eid)
    if original not in bullets_of(e):
        raise EditError("that bullet has changed on your Profile - rebuild your resume and try again")
    text = " ".join(str(text or "").split())
    d = load(prof)
    mine = d["wording"].setdefault(eid, {})
    if not text or text == original:
        mine.pop(original, None)
        if not mine:
            d["wording"].pop(eid, None)
        return _save(prof, d)
    if len(text) > MAX_WORDING:
        raise EditError(f"keep a bullet under {MAX_WORDING} characters")
    ok, why = numbers_grounded(text, [original])
    if not ok:
        if measured is None:
            raise EditError(f"new number: {why}. Was it measured, or is it your estimate?")
        if not measured:
            raise EditError("only measured results are printed as numbers - say it without the number,"
                            " or add it on your Profile as an estimate")
    mine[original] = text
    return _save(prof, d)


def apply(store: dict[str, Any], d: dict[str, Any]) -> dict[str, Any]:
    """The store as this person's resume should read it: hidden entries out
    (a hidden project takes its pieces with it), wording swapped in, and the
    person's order and pins marked for selection and layout."""
    out = copy.deepcopy(store)
    hidden = set(d.get("hidden") or [])
    hidden |= {str(e.get("id")) for e in out.get("entries") or [] if str(e.get("part_of") or "") in hidden}
    entries = []
    for e in out.get("entries") or []:
        eid = str(e.get("id"))
        if eid in hidden:
            continue
        words = (d.get("wording") or {}).get(eid) or {}
        if words:
            e["facts"] = [words.get(str(f), f) for f in e.get("facts") or []]
            e["metrics"] = [{**m, "claim": words.get(str(m.get("claim")), m.get("claim"))}
                            for m in e.get("metrics") or []]
        if eid in (d.get("rank") or {}):
            e["_rank"] = d["rank"][eid]
        if eid in (d.get("pinned") or []):
            e["_pinned"] = True
        entries.append(e)
    out["entries"] = entries
    return out
