"""The command line. Board commands are shared; the rest work on one profile.

    python -m jobsearch.cli profiles               list the profiles
    python -m jobsearch.cli setup <id>             a new profile from a resume, in your browser
    python -m jobsearch.cli verify                 re-check every known board
    python -m jobsearch.cli verify --provider lever
    python -m jobsearch.cli search "Postman" "Zepto"
    python -m jobsearch.cli escalations            show the open queue
    python -m jobsearch.cli watch                  poll boards, report what is new
    python -m jobsearch.cli --profile <id> triage  rank what is new against one person's store
    python -m jobsearch.cli triage --recheck       re-apply the filters to stored results
    python -m jobsearch.cli dashboard              regenerate var/<id>/dashboard.html
    python -m jobsearch.cli cycle                  watch once, then triage + rank + dashboard
                                                   for every profile (for a schedule)
    python -m jobsearch.cli rank                   triage pass 3 - a model reads the shortlist
    python -m jobsearch.cli assemble --top 3       build a resume for the best postings
    python -m jobsearch.cli outreach find <id>     who to ask for a referral
    python -m jobsearch.cli outreach draft <id> --to "Name" --how cold
    python -m jobsearch.cli outreach list          every ask, and what it needs today
    python -m jobsearch.cli serve                  the local app, in your browser
    python -m jobsearch.cli connector              the agent as an MCP server (hosted)
    python -m jobsearch.cli state push|pull|status mirror var/ to DATABASE_URL

A command that needs a person takes --profile <id>, else JOBSEARCH_PROFILE,
else the only profile there is. See profiles/README.md.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Windows consoles default to cp1252. One em-dash in a board name would
# otherwise raise UnicodeEncodeError and take the run down at the last step.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from .discovery import DiscoveryRun, normalise, search_company, verify_known
from .http import default_fetcher
from .providers import ALL_PROVIDERS
from .models import BLOCKED, EMPTY, ERROR, NOT_FOUND, OK
from .profile import Profile, ProfileError, list_profiles, profiles_dir, resolve, shared_var, var_dir
from .watch import POLLED, SEEDED, SKIPPED, load_boards, record_new, watch_all
from .assemble_cli import run_assemble
from .outreach_cli import add_parser as add_outreach
from .cycle import run_cycle
from .rank import apply_to_results
from .dashboard import render as render_dashboard
from .triage_cli import run_triage
from .registry import (
    do_not_reprobe,
    known_boards,
    load_registry,
    open_escalations,
    resolve_escalation,
    write_run,
)

ROOT = Path(__file__).resolve().parents[2]


def _root(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "root", None) or ROOT)


def _var(args: argparse.Namespace) -> Path:
    """Board state: shared by every profile."""
    return shared_var(_root(args))


def _prof(args: argparse.Namespace) -> Profile:
    """The person this command is for, resolved once."""
    if getattr(args, "prof", None) is None:
        args.prof = resolve(_root(args), getattr(args, "profile", None))
    return args.prof

MARK = {OK: "ok  ", EMPTY: "----", NOT_FOUND: "404 ", BLOCKED: "block", ERROR: "err "}


def _report(run: DiscoveryRun) -> None:
    for p in run.probes:
        detail = ""
        if p.found:
            detail = f"{len(p.postings):>4} postings"
            if p.board_name:
                detail += f"   board: {p.board_name}"
        elif p.error:
            detail = p.error[:70]
        print(f"  {MARK.get(p.status, p.status):<6} {p.provider:<11} {p.slug:<34} {detail}")

    print(f"\n  {run.summary()}")

    if run.escalations:
        print(f"\n  {len(run.escalations)} escalation(s) — a human decides these:")
        for e in run.escalations:
            print(f"    [{e.kind}] {e.provider}:{e.slug}")
            print(f"      {e.reason}")
            for k, v in e.evidence.items():
                print(f"        {k}: {v}")


def cmd_profiles(args: argparse.Namespace) -> int:
    have = list_profiles(_root(args))
    print(f"{len(have)} profile(s) in {profiles_dir(_root(args))}")
    for pid in have:
        print(f"  {pid}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root, var = _root(args), _var(args)
    reg = load_registry(root / "data" / "ats-registry.yaml")
    boards = known_boards(reg)
    if args.provider:
        boards = [b for b in boards if b[0] == args.provider]
    if not boards:
        print("no known boards match", file=sys.stderr)
        return 1

    print(f"verifying {len(boards)} known board(s) at <=1 req/s per host\n")
    fetcher = default_fetcher(var)
    run = verify_known(fetcher, boards)

    # Write first, render second. The first live run crashed on a cp1252
    # console while printing an escalation, and lost the whole result.
    paths = write_run(var, run.probes, run.escalations, run.summary())
    _report(run)
    print(f"\n  wrote {paths['boards']} and {paths['run'].name}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    root, var = _root(args), _var(args)
    reg = load_registry(root / "data" / "ats-registry.yaml")
    skip_names = {normalise(c) for c in do_not_reprobe(reg)}

    fetcher = default_fetcher(var)
    run = DiscoveryRun()
    for company in args.companies:
        if normalise(company) in skip_names and not args.force:
            print(f"  skip  {company} — on the do-not-reprobe list "
                  f"(--force to probe anyway)")
            continue
        print(f"  searching {company} ...")
        r = search_company(fetcher, company)
        run.probes.extend(r.probes)
        run.escalations.extend(r.escalations)

    if not run.probes:
        print("\nnothing probed")
        return 0
    paths = write_run(var, run.probes, run.escalations, run.summary())
    print()
    _report(run)
    print(f"\n  wrote {paths['run']}")
    return 0


def cmd_escalations(args: argparse.Namespace) -> int:
    path = _var(args) / "escalations.jsonl"

    if args.resolve:
        if ":" not in args.resolve:
            print("--resolve takes provider:slug, e.g. greenhouse:purestorage",
                  file=sys.stderr)
            return 2
        provider, slug = args.resolve.split(":", 1)
        resolve_escalation(path, provider, slug, args.note or "")
        print(f"resolved {provider}:{slug}")
        return 0

    rows = open_escalations(path)
    if not rows:
        print("no open escalations")
        return 0
    print(f"{len(rows)} open escalation(s):\n")
    for r in rows:
        print(f"  [{r['kind']}] {r['provider']}:{r['slug']}   raised {r['raised_at']}")
        print(f"    {r['reason']}")
        for k, v in (r.get("evidence") or {}).items():
            print(f"      {k}: {v}")
        print(f'    close with: --resolve {r["provider"]}:{r["slug"]} --note "..."')
        print()
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    var = _var(args)
    boards = load_boards(var)
    if not boards:
        print("no boards known - run `verify` first", file=sys.stderr)
        return 1
    if args.provider:
        boards = [b for b in boards if b["provider"] == args.provider]

    print(f"polling {len(boards)} board(s)\n")
    run = watch_all(default_fetcher(var), var, boards)

    # Persist before rendering, same reason as discovery.
    path = record_new(var, run)

    for b in run.boards:
        if b.status == SEEDED:
            note = f"seeded baseline ({b.total} postings) - nothing reported"
        elif b.status == SKIPPED:
            note = f"skipped: {b.error}"
        else:
            bits = []
            if b.added:
                bits.append(f"+{len(b.added)} new")
            if b.removed:
                bits.append(f"-{len(b.removed)} closed")
            note = ", ".join(bits) if bits else "no change"
            note += f"   ({b.total} live)"
        print(f"  {b.status:<8} {b.key:<40} {note}")

    print(f"\n  {run.summary()}")

    if run.added:
        print(f"\n  {len(run.added)} new posting(s):\n")
        for board, post in run.added:
            loc = post.location or "-"
            print(f"    {board.company:<22} {post.title}")
            print(f"    {'':<22} {loc}   {post.url}")
        print(f"\n  appended to {path}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    out = render_dashboard(_prof(args))
    kb = out.stat().st_size / 1024
    print(f"wrote {out}  ({kb:.0f} KB, self-contained)")
    if args.open:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_rank(args: argparse.Namespace) -> int:
    res = apply_to_results(_prof(args), limit=args.limit, reread=args.all)
    if res.get("error"):
        print(res["error"], file=sys.stderr)
        return 1
    print(f"  considered {res['considered']}, ranked {res['ranked']}")
    print(f"  {res['attribution']}")
    for m in res.get("unranked") or []:
        print(f"    no judgement returned for: {m}")
    for f in res.get("failures") or []:
        print(f"    recovered from {f['provider']}: {f['error'][:90]}")
    if res.get("stopped"):
        print(f"  stopped early ({res['stopped']}); {res.get('left', 0)} left unread - judged so far is saved")
    if not res["ok"]:
        print("  no model answered - keyword ranking stands unchanged")
        return 1
    return 0


def cmd_usage(args: argparse.Namespace) -> int:
    """Today's model use against data/llm-limits.yaml - everyone's, and the
    profile's share when one is named."""
    from .limits import Meter
    who = _prof(args).id if (getattr(args, "profile", None) or os.environ.get("JOBSEARCH_PROFILE")) else ""
    for name, row in Meter(_root(args), who).today().items():
        caps = row["caps"]
        if not caps.get("requests_per_day"):
            print(f"  {name:<11} off")
            continue
        line = (f"  {name:<11} {row['requests']:>4} of {caps['requests_per_day']} requests, "
                f"{row['tokens']:>9,} of {caps.get('tokens_per_day', 0):,} tokens today")
        if "month_tokens" in row:
            line += f"; {row['month_tokens']:,} of {caps['tokens_per_month']:,} this month"
        if "usd_per_day" in caps:
            line += (f"; ${row.get('usd', 0):.2f} of ${float(caps['usd_per_day']):.2f} today,"
                     f" ${row.get('month_usd', 0):.2f} of ${float(caps.get('usd_per_month', 0)):.2f} this month")
        if who and "profile" in row:
            mine = row["profile"]
            line += f"; {who}: {mine['requests']} requests, {mine['tokens']:,} tokens, ${mine['usd']:.2f}"
        print(line)
    return 0


def cmd_kit(args: argparse.Namespace) -> int:
    from .apply_kit import OutreachError, build_kit
    try:
        k = build_kit(_prof(args), args.posting)
    except OutreachError as exc:
        print(f"  {exc}")
        return 2
    print(f"\n  {k['role']} at {k['company']}   (resume: {k['resume'] or 'not built yet'})\n")
    for j in k["experience"]:
        print(f"  EXPERIENCE  {j['title']} | {j['company']} | {j['location']} | from {j['from']}"
              + (" | currently work here" if j["current"] else ""))
    print(f"\n  SKILLS TO ADD ({len(k['skills_add'])})  - type each, pick the form's match")
    for s in k["skills_add"]:
        print(f"    {s['workday']:<50} {'seen' if s['seen'] else 'guess':<6} {s['why']}")
    print("\n  IF SUGGESTED, KEEP:   " + ", ".join(s["workday"] for s in k["skills_keep"]))
    print("  IF SUGGESTED, REMOVE: " + ", ".join(s["workday"] for s in k["skills_remove"]))
    if k["not_backed"]:
        print("  DON'T ADD (not backed): " + ", ".join(k["not_backed"]))
    print("\n  CERTIFICATIONS")
    for c in k["certifications"]:
        print(f"    {c['name']}  ({c['issuer']}, {c['year']})")
    for ed in k["education"]:
        print(f"\n  EDUCATION   {ed['degree']}, {ed['field']} | {ed['school']} | {ed['from']} - {ed['to']}"
              + (f" | CGPA {ed['grade']}" if ed["grade"] else ""))
    print(f"  LANGUAGES   {', '.join(k['languages'])}  (proficiency: your call)")
    print(f"  LINKS       {'  '.join(k['links'])}\n")
    return 0


def cmd_cycle(args: argparse.Namespace) -> int:
    """Every profile, or only --profile. With DATABASE_URL set (in .env), the
    cycle syncs var/ with the hosted connector's database on either side, so
    the PC and the server share one memory. Without it, it runs on local files."""
    from . import statestore
    from .llm import load_dotenv
    root = _root(args)
    load_dotenv(root / ".env")
    who = [_prof(args)] if getattr(args, "profile", None) else None
    var = var_dir(root)
    remote = statestore.remote_from_env()
    if remote is None:
        return run_cycle(root, args.quiet, profiles=who)
    # Only a database failure falls back to a local cycle. A cycle that raises
    # is not retried: running it again would poll every board twice and fail
    # the same way.
    try:
        lock = remote.cycle_lock()
        got = lock.__enter__()
    except Exception as exc:  # noqa: BLE001 - an unreachable database must not stop the watcher
        print(f"state sync unavailable ({exc}); cycling on local files", file=sys.stderr)
        return run_cycle(root, args.quiet, profiles=who)
    try:
        if not got:
            print("the connector is refreshing - skipping this tick", file=sys.stderr)
            return 0
        try:
            statestore.pull(var, remote)
        except Exception as exc:  # noqa: BLE001
            print(f"state pull failed ({exc}); cycling on local files", file=sys.stderr)
        code = run_cycle(root, args.quiet, profiles=who)
        try:
            statestore.push(var, remote)
        except Exception as exc:  # noqa: BLE001
            print(f"state push failed ({exc}); sent next cycle", file=sys.stderr)
            return 1
        return code
    finally:
        lock.__exit__(None, None, None)


def cmd_state(args: argparse.Namespace) -> int:
    from . import statestore
    from .llm import load_dotenv
    load_dotenv(_root(args) / ".env")
    var = var_dir(_root(args))
    remote = statestore.remote_from_env()
    if remote is None:
        print("DATABASE_URL is not set (put it in .env)", file=sys.stderr)
        return 2
    if args.action == "status":
        st = statestore.status(var, remote)
        for k, paths in st.items():
            print(f"  {k}: {len(paths)}")
            for p in paths[:20]:
                print(f"    {p}")
        return 0
    rep = (statestore.push if args.action == "push" else statestore.pull)(var, remote)
    print(f"  {rep.summary()}")
    for p in rep.conflicts:
        print(f"    conflict: {p} - the database copy won; yours is in var/{statestore.CONFLICTS}/")
    return 0


def _connector(args: argparse.Namespace) -> int:
    from .connector import serve
    return serve(_prof(args))


def _serve(args: argparse.Namespace) -> int:
    from .web.server import serve
    serve(_prof(args), port=args.port, open_browser=not args.no_browser)
    return 0


def _setup(args: argparse.Namespace) -> int:
    """The app, on its setup screen, for a profile that may not exist yet."""
    from .web.server import serve
    root = _root(args)
    prof = Profile(root, args.id)
    print(f"setting up '{prof.id}'")
    print(f"  the profile will be written to {prof.dir}")
    print(f"  the uploaded resume and the draft stay in {prof.var / 'setup'}")
    try:
        prof.dir.resolve().relative_to(root.resolve())
        print("  both are inside this checkout (gitignored). To keep a real person's data out of it,")
        print("  set JOBSEARCH_PROFILES and JOBSEARCH_VAR in .env to folders elsewhere.")
    except ValueError:
        pass
    serve(prof, port=args.port, open_browser=not args.no_browser, page="#/setup")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobsearch", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", metavar="ID",
                    help="whose search this is (default: JOBSEARCH_PROFILE, or the only profile)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("profiles", help="list the profiles")
    pr.set_defaults(fn=cmd_profiles)

    v = sub.add_parser("verify", help="re-check every known board")
    v.add_argument("--provider", choices=ALL_PROVIDERS)
    v.set_defaults(fn=cmd_verify)

    s = sub.add_parser("search", help="probe candidate slugs for new companies")
    s.add_argument("companies", nargs="+")
    s.add_argument("--force", action="store_true",
                   help="probe even if the company is on the do-not-reprobe list")
    s.set_defaults(fn=cmd_search)

    e = sub.add_parser("escalations", help="show or close the open queue")
    e.add_argument("--resolve", metavar="PROVIDER:SLUG",
                   help="close an escalation after checking it yourself")
    e.add_argument("--note", help="why it is resolved - kept as the audit trail")
    e.set_defaults(fn=cmd_escalations)

    w = sub.add_parser("watch", help="poll known boards and report what is new")
    w.add_argument("--provider", choices=ALL_PROVIDERS)
    w.set_defaults(fn=cmd_watch)

    t = sub.add_parser("triage", help="rank new postings against the profile's store")
    t.add_argument("--all", action="store_true",
                   help="re-triage everything, not just postings not yet seen")
    t.add_argument("--board", metavar="PROVIDER:SLUG",
                   help="triage everything live on one board instead of the new queue")
    t.add_argument("--refresh", action="store_true",
                   help="re-fetch every stored open posting's full description and triage it again")
    t.add_argument("--recheck", action="store_true",
                   help="re-apply the hard filters to stored results and mark closed "
                        "postings, from disk alone; a dry run unless --write")
    t.add_argument("--write", action="store_true",
                   help="with --recheck: apply the changes it reports")
    t.set_defaults(fn=lambda a: run_triage(_prof(a), a))

    c = sub.add_parser("cycle", help="watch once; triage, rank and redraw for every profile (or --profile)")
    c.add_argument("--quiet", action="store_true", help="log only; no stdout")
    c.set_defaults(fn=cmd_cycle)

    rk = sub.add_parser("rank", help="triage pass 3 - a model reads every kept posting not yet read")
    rk.add_argument("--limit", type=int, default=None, help="at most this many (default: all unread)")
    rk.add_argument("--all", action="store_true", help="re-read every kept posting (after a profile change)")
    rk.set_defaults(fn=cmd_rank)

    asm = sub.add_parser("assemble", help="build a resume variant for a posting")
    asm.add_argument("--posting", help="one posting id; default is the top-ranked")
    asm.add_argument("--top", type=int, default=1, help="how many top postings to assemble for")
    asm.add_argument("--no-model", action="store_true",
                     help="skip the summary line; everything else is deterministic anyway")
    asm.add_argument("--no-pdf", action="store_true",
                     help="skip compiling the .tex (the PDF is checked when it is built)")
    asm.set_defaults(fn=lambda a: run_assemble(_prof(a), a))

    add_outreach(sub, _prof)

    from .mail_cli import add_parser as add_mail
    add_mail(sub, _prof)

    us = sub.add_parser("usage", help="today's model requests and tokens against data/llm-limits.yaml")
    us.set_defaults(fn=cmd_usage)

    kt = sub.add_parser("kit", help="everything an application form asks for, for one posting")
    kt.add_argument("posting", help="posting id, or a unique prefix of one")
    kt.set_defaults(fn=cmd_kit)

    su = sub.add_parser("setup", help="create a profile from a resume: upload, confirm each fact, save")
    su.add_argument("id", help="the new profile's id: lowercase letters, digits and hyphens")
    su.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    su.add_argument("--no-browser", action="store_true", help="start the server only")
    su.set_defaults(fn=_setup)

    sv = sub.add_parser("serve", help="open the local app in your browser")
    sv.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    sv.add_argument("--no-browser", action="store_true", help="start the server only")
    sv.set_defaults(fn=lambda a: _serve(a))

    cn = sub.add_parser("connector", help="serve the agent as an MCP connector on $PORT")
    cn.set_defaults(fn=_connector)

    st = sub.add_parser("state", help="mirror var/ to the database in DATABASE_URL")
    st.add_argument("action", choices=("push", "pull", "status"))
    st.set_defaults(fn=cmd_state)

    d = sub.add_parser("dashboard", help="regenerate the HTML dashboard from var/")
    d.add_argument("--open", action="store_true", help="open it in a browser")
    d.set_defaults(fn=cmd_dashboard)

    args = ap.parse_args(argv)
    # Before anything resolves a profile: JOBSEARCH_PROFILES and JOBSEARCH_VAR
    # may be set there.
    from .llm import load_dotenv
    load_dotenv(_root(args) / ".env")
    try:
        return args.fn(args)
    except ProfileError as exc:
        print(f"jobsearch: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
