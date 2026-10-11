"""The `triage` subcommand. Kept out of cli.py so that file stays readable."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from dataclasses import dataclass

from .http import default_fetcher
from .profile import Profile
from .providers import DETAIL_PROVIDERS, fetch_details, probe
from .skills import load_index
from .triage import (
    KEEP,
    NO_MATCH,
    REJECT,
    RecheckReport,
    angle_for,
    load_profile,
    recheck_results,
    top_gaps,
    triage_posting,
    update_gap_ledger,
    write_results,
)
from .watch import load_boards, posting_live


@dataclass
class _Ephemeral:
    """A one-off run: results are shown, but nothing is marked as triaged."""

    all: bool = True


def _load_new_postings(var: Path) -> list[dict[str, Any]]:
    p = var / "new-postings.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _seen_path(var: Path) -> Path:
    return var / "triaged.json"


def _already(var: Path) -> set[str]:
    p = _seen_path(var)
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def _remember(var: Path, ids: set[str]) -> None:
    _seen_path(var).write_text(json.dumps(sorted(ids), indent=0), encoding="utf-8")


def _board_postings(var: Path, board: str) -> list[dict[str, Any]]:
    """Every posting live on one board, right now. `var` is the shared state.

    The watcher's queue only ever holds what changed. This is for the other
    question - "what is on this company's board today that fits me" - and it
    is also how the filters get exercised against real data, not a trickle.
    """
    provider, slug = board.split(":", 1)
    # A Workday slug like "wf.wd1.SomeBankJobs@India" is no name to show.
    names = {f"{b.get('provider')}:{b.get('slug')}": b.get("company") for b in load_boards(var)}
    company = names.get(board) or slug
    p = probe(default_fetcher(var), provider, slug, company, with_board_name=False)
    return [
        {"board": board, "company": company, "first_seen": None, **post.to_dict()}
        for post in p.postings
    ]


def refresh_results(prof: Profile, fetcher: Any = None, say=print) -> dict[str, int]:
    """Fetch each stored, open posting's WHOLE description again and triage it
    anew - for when triage changes what it reads or keeps.

    The stored record holds a 2,000-character head, and the requirements (the
    years, the degree) are usually past it: Amazon's come 5,000 characters in.
    A recheck can only re-read what was kept; this re-reads the source. A
    posting whose description can't be fetched is left exactly as it was -
    triaged on an empty text, it would read as "nothing backed" and drop off.
    The model's judgement and the watcher's closed mark are carried over by
    write_results; the gap ledger is not re-counted."""
    var = prof.var
    fetcher = fetcher or default_fetcher(prof.shared)
    index, profile = load_index(prof), load_profile(prof.dir)
    blob = json.loads((var / "triage-results.json").read_text(encoding="utf-8"))
    todo = [p for p in blob["postings"].values() if not p.get("closed_at")]
    lists: dict[str, dict[str, str]] = {}          # board -> id -> description
    fresh, skipped = [], 0
    for rec in todo:
        board = str(rec.get("board") or "")
        provider, _, slug = board.partition(":")
        if provider in DETAIL_PROVIDERS:
            text = fetch_details(fetcher, provider, slug, rec).get("description") or ""
        elif provider in ("lever", "ashby", "amazon", "atlassian"):
            if board not in lists:
                got = probe(fetcher, provider, slug, rec.get("company"), with_board_name=False)
                lists[board] = {post.id: post.description for post in got.postings}
            text = lists[board].get(str(rec.get("id"))) or ""
        else:
            text = ""
        if not text.strip():
            skipped += 1
            continue
        fresh.append({k: rec.get(k) for k in ("id", "title", "company", "location", "url", "board")}
                     | {"description": text})
    results = [triage_posting(index, profile, p) for p in fresh]
    write_results(var, results)
    report = {"refreshed": len(results), "skipped": skipped,
              "kept": sum(1 for t in results if t.verdict not in (REJECT, NO_MATCH)),
              "rejected": sum(1 for t in results if t.verdict == REJECT)}
    say(f"refreshed {report['refreshed']} (kept {report['kept']}, now rejected {report['rejected']}); "
        f"left as they were, no description fetched: {report['skipped']}")
    return report


def run_recheck(prof: Profile, write: bool) -> int:
    """Pass 1 again over the stored triage results, from disk alone."""
    var = prof.var
    if not (var / "triage-results.json").exists():
        print("no stored results - run `triage` first")
        return 1
    rep = recheck_results(
        var, load_profile(prof.dir),
        lambda board, pid, since: posting_live(prof.shared, board, pid, since),
        write=write,
    )
    _print_recheck(rep, write)
    return 0


def _print_recheck(rep: RecheckReport, write: bool) -> None:
    def line(rec: dict[str, Any], note: str = "") -> str:
        return (f"    {str(rec.get('company', ''))[:18]:<18} "
                f"{str(rec.get('title', '')).strip()[:44]:<46} {note}").rstrip()

    state = ("written" if rep.written else
             "nothing to write" if write else "dry run - nothing written")
    print(f"rechecked {rep.checked} stored posting(s) against the current profile"
          f" ({state})\n")
    if rep.changed:
        print(f"  verdict changes ({len(rep.changed)}):")
        for rec, was, now, reasons in rep.changed:
            print(line(rec, f"{was} -> {now}: {reasons[-1]}"))
        print()
    if rep.closed:
        print(f"  closed on their board - hidden from the dashboard, kept for outreach"
              f" ({len(rep.closed)}):")
        for rec in rep.closed:
            print(line(rec, str(rec.get("id", ""))))
        print()
    if rep.reopened:
        print(f"  back on their board ({len(rep.reopened)}):")
        for rec in rep.reopened:
            print(line(rec, str(rec.get("id", ""))))
        print()
    if rep.unsure:
        # Said out loud, because "unchanged" and "could not tell" look alike.
        print(f"  left as they were - the stored excerpt cannot settle it"
              f" ({len(rep.unsure)}):")
        for rec, why in rep.unsure:
            print(line(rec, why))
        print()
    if rep.unknown:
        print(f"  open or closed unknown - board not watched, or not polled since"
              f" triage ({len(rep.unknown)}):")
        for rec in rep.unknown:
            print(line(rec, str(rec.get("board", ""))))
        print()
    if not (rep.changed or rep.closed or rep.reopened):
        print("  nothing to change\n")

    print("  The gap ledger and the reject tallies are not touched by a recheck.")
    if rep.written:
        print("  Run `dashboard` to redraw it now, or wait for the next cycle.")
    elif not write and (rep.changed or rep.closed or rep.reopened):
        print("  Run `triage --recheck --write` to apply.")


def run_triage(prof: Profile, args) -> int:
    """New postings from the shared queue, against one person's profile. Which
    of them this person has seen is theirs (triaged.json), so a profile added
    later reads the whole queue on its first run."""
    var = prof.var
    var.mkdir(parents=True, exist_ok=True)
    if getattr(args, "refresh", False):
        refresh_results(prof)
        return 0
    if getattr(args, "recheck", False):
        if getattr(args, "board", None) or getattr(args, "all", False):
            print("--recheck works on stored results; it takes no --board or --all")
            return 2
        return run_recheck(prof, write=getattr(args, "write", False))
    if getattr(args, "write", False):
        print("--write goes with --recheck")
        return 2
    index = load_index(prof)
    profile = load_profile(prof.dir)
    store = yaml.safe_load(prof.file("master-store.yaml").read_text(encoding="utf-8")) or {}

    if getattr(args, "board", None):
        if ":" not in args.board:
            print("--board takes provider:slug, e.g. greenhouse:druva")
            return 2
        postings = _board_postings(prof.shared, args.board)
        print(f"{len(postings)} posting(s) live on {args.board}")
        args = _Ephemeral(all=True)          # never touch the triaged-ids state
    else:
        postings = _load_new_postings(prof.shared)
    if not postings:
        if getattr(args, "quiet", False):
            return 0
        print("nothing new to triage - run `watch` first")
        if top_gaps(var):
            _print_gaps(var)
        return 0

    done = set() if args.all else _already(var)
    queue = [p for p in postings if p.get("id") not in done]
    if not queue:
        if getattr(args, "quiet", False):
            return 0
        print(f"all {len(postings)} known posting(s) already triaged "
              f"(--all to re-run)")
        _print_gaps(var)
        return 0

    # Greenhouse and Workday withhold descriptions from their list endpoints
    # (Workday the full location list too), so fetch them here - once, for new
    # postings only.
    need = [p for p in queue if not p.get("description")
            and str(p.get("board", "")).split(":", 1)[0] in DETAIL_PROVIDERS]
    if need:
        if not getattr(args, "quiet", False):
            print(f"fetching {len(need)} description(s) ...")
        fetcher = default_fetcher(prof.shared)
        for p in need:
            provider, slug = p["board"].split(":", 1)
            p.update(fetch_details(fetcher, provider, slug, p))

    results = [triage_posting(index, profile, p) for p in queue]
    kept = [t for t in results if t.verdict not in (REJECT, NO_MATCH)]
    kept.sort(key=lambda t: -t.score)

    path = update_gap_ledger(var, index, results)
    write_results(var, results)
    if not args.all:
        _remember(var, done | {p.get("id") for p in queue if p.get("id")})

    # A scheduled run has no one reading stdout. It returns the count instead,
    # and the cycle logs one line.
    if getattr(args, "quiet", False):
        return len(kept)

    _print_results(results, kept, store, index, profile)
    print(f"\n  gap ledger: {path}")
    _print_gaps(var)
    return 0


def _print_results(results, kept, store, index, profile) -> None:
    rejected = [t for t in results if t.verdict == REJECT]
    no_match = [t for t in results if t.verdict == NO_MATCH]
    print(f"\ntriaged {len(results)} posting(s): {len(kept)} kept, "
          f"{len(no_match)} no-match, {len(rejected)} rejected\n")

    shortlist = int((profile.get("scoring") or {}).get("shortlist", 10))
    for t in kept[:shortlist]:
        angle = angle_for(store, index, t.coverage.matched) if t.coverage else None
        loc = t.posting.get("location") or "-"
        print(f"  {t.company} — {t.title}")
        print(f"    {loc}   coverage {t.coverage.fraction}   score {t.score}"
              + (f"   angle {angle}" if angle else ""))
        if t.coverage and t.coverage.matched:
            strong = sorted(t.coverage.matched.items(), key=lambda kv: kv[0])
            print("    strong  " + " ".join(f"{k}({v[0]})" for k, v in strong))
        if t.required_gaps:
            print("    gap     " + ", ".join(sorted(t.required_gaps)))
        if t.verdict != KEEP:
            print(f"    NOTE    {'; '.join(t.reasons)}")
        print(f"    {t.posting.get('url', '')}")
        print()

    if rejected:
        print(f"  rejected ({len(rejected)}):")
        for t in rejected[:12]:
            print(f"    {t.company:<18} {t.title[:46]:<48} {t.reasons[-1]}")
        if len(rejected) > 12:
            print(f"    ... and {len(rejected) - 12} more")


def _print_gaps(var: Path) -> None:
    rows = top_gaps(var)
    if not rows:
        return
    print("\n  what the postings keep asking for that nothing backs:\n")
    print(f"    {'skill':<24} {'required':>8} {'nice':>6}   status")
    for bucket, skill, req, nice in rows:
        note = "deepen it" if bucket == "shallow" else "from zero"
        print(f"    {skill:<24} {req:>8} {nice:>6}   {bucket} — {note}")
    print("\n    Sorted by required_count. This says what the market asks for most,")
    print("    not what makes you worth hiring - see docs/triage.md.")
