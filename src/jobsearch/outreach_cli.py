"""The `outreach` subcommand: find, draft, list, show, mark."""
from __future__ import annotations

import argparse
from typing import Callable

from .llm import LLM
from .outreach import (
    NOTE_LIMIT,
    RELATIONSHIPS,
    STATUSES,
    OutreachError,
    draft,
    due,
    find_leads,
    find_posting,
    follow_up,
    load_ledger,
    mark,
    passed,
    posting_live,
    resolve_id,
)
from .profile import Profile


def _checks(checks: list[dict]) -> None:
    """Failures and warnings only - fifteen green lines hide the one that matters."""
    shown = 0
    for c in checks:
        if not c["ok"]:
            tag = "FAIL" if c["level"] == "fail" else "warn"
            print(f"    {tag}  {c['check']:<24} {c['detail']}")
            shown += 1
    blocking = [c for c in checks if c["level"] == "fail"]
    if all(c["ok"] for c in blocking):
        print(f"    all {len(blocking)} blocking checks pass")


def _print_draft(d: dict) -> None:
    print(f"\n  {d['role']} at {d['company']}  ->  {d['to']} ({d['how']})")
    print(f"  id {d['id']}   status {d['status']}   written by {d['by']}")
    if d.get("rejected"):
        print(f"  model version rejected - {d['rejected'][:160]}")
    if d.get("note"):
        print(f"\n  -- connection note ({len(d['note'])}/{NOTE_LIMIT} chars) --\n")
        print("  " + d["note"])
    print("\n  -- message --\n")
    print("\n".join("  " + line for line in d["message"].splitlines()))
    print(f"\n  -- email subject --\n\n  {d['subject']}")
    print(f"\n  -- blurb for their referral form --\n\n  {d['blurb']}")
    print("\n  -- checks --")
    _checks(d.get("checks") or [])


def cmd_find(prof: Profile, a: argparse.Namespace) -> int:
    r = find_leads(prof, find_posting(prof, a.posting))
    print(f"\n  {r['role']} at {r['company']}\n  {r['url']}\n")
    if r["already_asked"]:
        print("  already asked here:")
        for x in r["already_asked"]:
            print(f"    {x['to']} ({x['status']}) for {x['role']}")
        print()
    print("  Who to ask, strongest connection first. These open LinkedIn in your")
    print("  own browser - nothing here fetches them.\n")
    for i, lead in enumerate(r["leads"], 1):
        extra = f' --employer "{lead["employer"]}"' if lead.get("employer") else ""
        print(f"  {i}. {lead['who']}   (--how {lead['how']}{extra})")
        print(f"     {lead['why']}")
        print(f"     {lead['url']}\n")
    print(f'  then:  jobsearch outreach draft {r["posting_id"]} --to "Name" --how <kind>')
    return 0


def cmd_draft(prof: Profile, a: argparse.Namespace) -> int:
    llm = LLM(prof, purpose="outreach")
    if a.no_model:
        llm.chain = []
    d = draft(prof, a.posting, a.to, a.how, a.context or "", llm=llm,
              use_model=not a.no_model, force=a.force, employer=a.employer or "")
    _print_draft(d.to_dict())
    print(f"\n  wrote {prof.var / 'outreach' / (d.id + '.md')}")
    print(f"  after you send it:  jobsearch outreach mark {d.id} sent")
    return 0 if passed(d.checks) else 1


def cmd_list(prof: Profile, a: argparse.Namespace) -> int:
    rows = list(load_ledger(prof).values())
    if not a.all:
        rows = [d for d in rows if d.get("status") not in ("declined", "closed")]
    if not rows:
        print("no outreach yet - start with `jobsearch outreach find <posting>`")
        return 0
    # What needs doing today first, then oldest first.
    rows.sort(key=lambda d: (due(d) is None, d.get("created", "")))
    print(f"\n  {'needs':<30} {'status':<12} {'to':<18} {'role at company':<44} id")
    for d in rows:
        where = f"{d['role']} at {d['company']}"
        print(f"  {(due(d) or '')[:30]:<30} {d['status']:<12} {d['to'][:18]:<18} "
              f"{where[:44]:<44} {d['id']}")
    return 0


def cmd_show(prof: Profile, a: argparse.Namespace) -> int:
    ledger = load_ledger(prof)
    d = ledger[resolve_id(ledger, a.draft)]
    _print_draft(d)
    if d.get("status") in ("sent", "followed-up"):
        live = posting_live(prof, d.get("board", ""), d.get("posting_id", ""))
        print("\n  -- follow-up, if it comes to that --\n")
        print("\n".join("  " + line for line in follow_up(d, live).splitlines()))
    print("\n  -- history --")
    for h in d.get("history") or []:
        print(f"    {h['at'][:16].replace('T', ' ')}  {h['status']}"
              + (f"  - {h['note']}" if h.get("note") else ""))
    return 0


def cmd_mark(prof: Profile, a: argparse.Namespace) -> int:
    d = mark(prof, a.draft, a.status, a.note or "")
    print(f"  {d['id']} -> {d['status']}")
    nxt = due(d)
    if nxt:
        print(f"  next: {nxt}")
    return 0


def add_parser(sub, prof_of: Callable[[argparse.Namespace], Profile]) -> None:
    o = sub.add_parser("outreach", help="Module 5 - draft referral requests; you send them")
    osub = o.add_subparsers(dest="ocmd", required=True)

    f = osub.add_parser("find", help="who to ask at this posting's company")
    f.add_argument("posting", help="posting id, or a unique prefix of one")
    f.set_defaults(ofn=cmd_find)

    d = osub.add_parser("draft", help="draft a note, message, subject and referral blurb")
    d.add_argument("posting", help="posting id, or a unique prefix of one")
    d.add_argument("--to", required=True, help="the person's name")
    d.add_argument("--how", required=True, choices=RELATIONSHIPS,
                   help="how you are connected to them")
    d.add_argument("--context", help="one line on how you know them, e.g. "
                                     "'we were on the same team in 2024'")
    d.add_argument("--employer", help="with --how ex-colleague: the employer you share "
                                      "(default: your current one)")
    d.add_argument("--no-model", action="store_true",
                   help="use the deterministic draft only")
    d.add_argument("--force", action="store_true",
                   help="redraft even though this one is already marked sent")
    d.set_defaults(ofn=cmd_draft)

    ls = osub.add_parser("list", help="every ask, with what each needs from you today")
    ls.add_argument("--all", action="store_true", help="include declined and closed")
    ls.set_defaults(ofn=cmd_list)

    s = osub.add_parser("show", help="print one draft, its checks and its follow-up")
    s.add_argument("draft", help="draft id, or a unique prefix of one")
    s.set_defaults(ofn=cmd_show)

    m = osub.add_parser("mark", help="record what you did - the agent never sends")
    m.add_argument("draft", help="draft id, or a unique prefix of one")
    m.add_argument("status", choices=STATUSES)
    m.add_argument("--note", help="anything worth remembering, e.g. 'referred, ID 4471'")
    m.set_defaults(ofn=cmd_mark)

    def run(a: argparse.Namespace) -> int:
        try:
            return a.ofn(prof_of(a), a)
        except OutreachError as exc:
            print(f"  {exc}")
            return 2

    o.set_defaults(fn=run)
