"""The `mail` subcommand: sync, list, look, ask, rules."""
from __future__ import annotations

import argparse
from typing import Callable

from .profile import Profile
from . import applications as apps
from .busy import busy, holder
from .mailbox import MailError, configured


def cmd_sync(prof: Profile, a: argparse.Namespace) -> int:
    if not configured(prof):
        print("  GMAIL_USER / GMAIL_APP_PASSWORD are not set in .env, or MAIL_PROFILE is not"
              f" {prof.id} - see docs/mail.md")
        return 2
    from .mail_sync import sync
    if a.dry_run:
        rep = sync(prof, first_days=a.days, dry_run=True)
        for c in rep["status_changes"]:
            print(f"  would set {c['company'][:24]:<24} {c['role'][:36]:<36} {c['from'] or 'new':<12} -> {c['to']}")
        for app_id, pid in rep["linked"]:
            print(f"  would link {app_id} to tracked posting {pid}")
        return 0
    with busy(prof.shared, "mail sync") as got:
        if not got:
            who = holder(prof.shared) or {}
            print(f"  busy: {who.get('what')} since {who.get('since')} - try again when it ends")
            return 3
        sync(prof, first_days=a.days)
    return 0


def cmd_list(prof: Profile, a: argparse.Namespace) -> int:
    ledger = apps.load(prof)
    rows = [x for x in ledger["apps"].values() if a.all or not x.get("ignored")]
    rows.sort(key=lambda x: x["updated"], reverse=True)
    for x in rows:
        flag = " (look)" if x.get("look") else ""
        link = f"  -> {x['posting_id']}" if x.get("posting_id") else ""
        print(f"  {x['updated']}  {x['status']:<14} {x['company'][:24]:<24} {x['role'][:40]:<40} "
              f"{x.get('req_id') or '':<11}{flag}{link}")
    n = ledger["naukri"]["applied"]
    print(f"\n  {len(rows)} applications; Naukri: {sum(d.get('count', 0) for d in n.values())} applies "
          f"over {len(n)} days (counted - Naukri's emails do not list them)")
    return 0


def cmd_look(prof: Profile, a: argparse.Namespace) -> int:
    ledger = apps.load(prof)
    rows = [x for x in ledger["apps"].values() if x.get("look") and not x.get("ignored")]
    if not rows:
        print("  nothing needs a look")
    for x in rows:
        print(f"\n  {x['id']}  {x['status']}  {x['company'] or '?'} - {x['role'] or '?'}")
        for reason in x["look"]:
            print(f"    - {reason}")
        s = x.get("suggestion")
        if s:
            print(f"    Groq: {s.get('summary') or '(no summary)'}")
            for key in ("company", "role"):
                if s.get(key):
                    print(f"      {key}: {s[key]['value']}  - “{s[key]['quote']}”")
            for o in s.get("outcomes") or []:
                print(f"      email {o['n']}: {o['outcome']}  - “{o['quote']}”")
            if s.get("not_an_application"):
                print(f"      not an application - “{s['not_an_application']['quote']}”")
            if s.get("same_as"):
                print(f"      same job as {s['same_as']['id']} - {s['same_as']['why']}")
            for d in s.get("dropped") or []:
                print(f"      dropped: {d}")
    return 0


def cmd_ask(prof: Profile, a: argparse.Namespace) -> int:
    """Ask Groq about flagged records now, instead of at the next cycle."""
    from .mail_ai import review
    with busy(prof.shared, "mail ask") as got:
        if not got:
            who = holder(prof.shared) or {}
            print(f"  busy: {who.get('what')} since {who.get('since')} - try again when it ends")
            return 3
        r = review(prof, limit=a.limit)
    if r["skipped"]:
        print(f"  not asked: {r['skipped']}")
    else:
        print(f"  asked about {r['asked']}, suggestions {r['suggested']}, failed {r['failed']}, "
              f"rules proposed {r['proposals']}")
    return 0


def cmd_rules(prof: Profile, a: argparse.Namespace) -> int:
    from . import mail_learn
    if a.action:
        if not a.id:
            print("  which proposal? give its id")
            return 2
        fn = mail_learn.approve if a.action == "approve" else mail_learn.reject
        try:
            p = fn(prof, a.id)
        except ValueError as exc:
            print(f"  {exc}")
            return 2
        print(f"  {p['status']}: {p.get('phrase') or p.get('sender')}")
        return 0
    rows = sorted(mail_learn.load_proposals(prof).values(), key=lambda p: p["proposed_at"])
    if not rows:
        print("  no rules proposed yet")
    for p in rows:
        rule = (f"“{p['phrase']}” means {p['means']}" if p["type"] == "phrase"
                else f"{p['sender']} is {p['company']}")
        print(f"\n  {p['id']}  {p['status']:<9} {rule}")
        print(f"    from {p['learned_from']['company'] or '?'}: {p['learned_from']['subject']}")
        for c in p["changes"]:
            if p["type"] == "phrase":
                print(f"    {c['date']} {c['company'][:20]:<20} {c['from']}/{c['from_confidence']} -> "
                      f"{c['to']}/{c['to_confidence']}  {c['subject'][:50]}")
            else:
                print(f"    {c['date']} {c['from'] or 'no company'} -> {c['to']}  {c['subject'][:50]}")
    return 0


def cmd_eval_jev(prof: Profile, a: argparse.Namespace) -> int:
    """Score Jev against the emails we know the answers to (mail_eval.py)."""
    from .mail_eval import run
    res = run(prof, negatives=a.negatives, dry_run=a.dry_run)
    if res.get("error"):
        print(f"  {res['error']}")
        return 1
    if res.get("dry_run"):
        return 0
    s = res["summary"]
    print(f"  Jev right on {s['jev_overall']} emails with a known answer"
          f" ({s['errors']} errors, {s['tokens']:,} tokens)")
    print(f"    by how the answer is known: {s['jev_by_source']}")
    print(f"    by answer: {s['jev_by_truth']}")
    c = s["jev_confident_over_0.9"]
    print(f"    when over 0.9 confident: {c['right']} of {c['emails']}")
    print(f"  rules right on {s['rules_on_confirmed']} of the confirmed ones")
    if s["wrong"]:
        print()
        print("  where Jev differs from the known answer:")
        for w in s["wrong"]:
            print(f"    {w['date']} {w['domain'][:22]:<22} {w['subject'][:50]:<50} known {w['truth']} ({w['source']}),"
                  f" Jev {w['jev']} {w['confidence'] or 0:.2f}, rules {w['rules']}")
    if s["unsure"]:
        print()
        print("  the rules were unsure - what Jev says:")
        for w in s["unsure"]:
            print(f"    {w['date']} {w['domain'][:22]:<22} {w['subject'][:50]:<50} Jev {w['jev']} {w['confidence'] or 0:.2f}")
    print()
    print(f"  Saved to {prof.var / 'mail' / 'jev-eval.json'} (no email text).")
    return 0


def add_parser(sub, prof_of: Callable[[argparse.Namespace], Profile]) -> None:
    m = sub.add_parser("mail", help="read the profile's inbox (MAIL_PROFILE) and update applications (read-only)")
    ms = m.add_subparsers(dest="mcmd", required=True)
    s = ms.add_parser("sync", help="read new mail and record what it says")
    s.add_argument("--days", type=int, default=60, help="how far back on the first run")
    s.add_argument("--dry-run", action="store_true", help="read and decide, write nothing")
    s.set_defaults(mfn=cmd_sync)
    ls = ms.add_parser("list", help="every application and where it stands")
    ls.add_argument("--all", action="store_true", help="include ones marked 'not an application'")
    ls.set_defaults(mfn=cmd_list)
    lk = ms.add_parser("look", help="what the rules were unsure of")
    lk.set_defaults(mfn=cmd_look)
    ak = ms.add_parser("ask", help="ask Groq about flagged records now (only Groq; see docs/mail.md)")
    ak.add_argument("--limit", type=int, default=3)
    ak.set_defaults(mfn=cmd_ask)
    rl = ms.add_parser("rules", help="rules Groq proposed: list, approve or reject")
    rl.add_argument("action", nargs="?", choices=["approve", "reject"])
    rl.add_argument("id", nargs="?")
    rl.set_defaults(mfn=cmd_rules)

    ev = ms.add_parser("eval-jev", help="score Jev (TypeSafe) on emails with known answers - see mail_eval.py")
    ev.add_argument("--negatives", type=int, default=40, help="dropped emails (alerts, newsletters) to include")
    ev.add_argument("--dry-run", action="store_true", help="count what would be sent; send nothing")
    ev.set_defaults(mfn=cmd_eval_jev)

    def run(a: argparse.Namespace) -> int:
        try:
            return a.mfn(prof_of(a), a)
        except MailError as exc:
            print(f"  {exc}")
            return 2
    m.set_defaults(fn=run)
