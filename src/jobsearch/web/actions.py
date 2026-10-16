"""What "Run" can do: the CLI's own functions, each under the `busy` lock.

Every action is the same function the CLI runs, captured by web/runner.py and
held under the cross-process `busy` lock the scheduled cycle also takes. The
local app offers all of them; the hosted service only HOSTED_ACTIONS - a
person there builds their resumes and has their shortlist read, and the
watcher's cycle is the service's own business.
"""
from __future__ import annotations

import argparse
import re
from typing import Any, Callable

import threading

from ..busy import busy, holder
from ..profile import Profile

# How many people's actions are running in this process. The AI queue steps
# aside while any is (ai_queue.drain): a resume being built goes first.
_running = 0
_running_lock = threading.Lock()


def someone_working() -> bool:
    return _running > 0


def _ns(**kw: Any) -> argparse.Namespace:
    return argparse.Namespace(**kw)


def _cli():
    from .. import cli           # late: cli imports everything
    return cli


def _cycle(prof: Profile, o: dict) -> int:
    from ..cycle import run_cycle
    # This person's cycle: the boards are polled for everyone, but only this
    # profile is triaged and ranked - the app never spends on someone else.
    return run_cycle(prof.root, quiet=False, lock=False, profiles=[prof])   # already holding busy


def _watch(prof: Profile, o: dict) -> int:
    return _cli().cmd_watch(_ns(provider=o.get("provider"), root=prof.root))


def _verify(prof: Profile, o: dict) -> int:
    return _cli().cmd_verify(_ns(provider=o.get("provider"), root=prof.root))


def _search(prof: Profile, o: dict) -> int:
    names = [c.strip() for c in re.split(r"[,\n]", str(o.get("companies") or "")) if c.strip()]
    if not names:
        print("give at least one company name")
        return 2
    return _cli().cmd_search(_ns(companies=names, force=bool(o.get("force")), root=prof.root))


def _triage(prof: Profile, o: dict) -> int:
    from ..triage_cli import run_triage
    run_triage(prof, _ns(all=False, board=o.get("board") or None, quiet=False, recheck=False))
    return 0


def _rank(prof: Profile, o: dict) -> int:
    from ..ai_queue import view
    q = view(prof)
    if q and q.get("state") == "running" and not o.get("all"):
        print(f"The AI is already reading your jobs in the background: {q.get('done') or 0} of "
              f"{q.get('total') or 0} done. Nothing to start.")
        return 0
    return _cli().cmd_rank(_ns(limit=int(o["limit"]) if o.get("limit") else None, all=bool(o.get("all")),
                               prof=prof))


def _assemble(prof: Profile, o: dict) -> int:
    from ..assemble_cli import run_assemble
    from .state import check_open
    if not o.get("posting"):
        print("assemble needs a posting")
        return 2
    if check_open(prof, str(o["posting"])) is False:
        print("This job has closed on the company's board - no resume built for it.")
        return 1
    return run_assemble(prof, _ns(posting=str(o["posting"]), top=1,
                                  no_model=bool(o.get("no_model")), no_pdf=False))


def _dashboard(prof: Profile, o: dict) -> int:
    return _cli().cmd_dashboard(_ns(open=False, prof=prof))


def _mail(prof: Profile, o: dict) -> int:
    from ..mail_sync import sync
    from ..mailbox import MailError, configured
    if not configured(prof):
        print(f"GMAIL_USER / GMAIL_APP_PASSWORD are not set in .env, or MAIL_PROFILE is not {prof.id}"
              " - see docs/mail.md")
        return 2
    try:
        sync(prof)
        from ..mail_ai import review
        r = review(prof)
        if r["skipped"]:
            print(f"Groq not asked: {r['skipped']}")
        elif r["asked"]:
            print(f"asked Groq about {r['asked']} flagged record(s): {r['suggested']} suggestion(s), "
                  f"{r['proposals']} rule(s) proposed")
    except MailError as exc:
        print(str(exc))
        return 2
    return 0


ACTIONS: dict[str, tuple[str, Callable[[Profile, dict], int]]] = {
    "cycle": ("Run a full cycle", _cycle),
    "watch": ("Poll every board", _watch),
    "triage": ("Triage what is new", _triage),
    "rank": ("Have a model read the shortlist", _rank),
    "verify": ("Re-verify every board", _verify),
    "search": ("Look for new companies", _search),
    "assemble": ("Build the resume", _assemble),
    "dashboard": ("Regenerate the static dashboard", _dashboard),
    "mail": ("Check the inbox", _mail),
}
HOSTED_ACTIONS = ("assemble", "rank")


# Actions that touch only the person's own files, under the short edit lock
# where they share one: a job update in progress must not refuse them.
OWN_FILES = ("assemble",)


def under_lock(name: str, fn: Callable[[Profile, dict], int]) -> Callable[[Profile, dict], int]:
    def run(prof: Profile, opts: dict) -> int:
        global _running
        with _running_lock:
            _running += 1
        try:
            if name in OWN_FILES:
                return fn(prof, opts)
            with busy(prof.shared, f"app: {name}") as got:
                if not got:
                    who = holder(prof.shared) or {}
                    print(f"busy: {who.get('what')} since {who.get('since')} - try again when it ends")
                    return 3
                return fn(prof, opts)
        finally:
            with _running_lock:
                _running -= 1
    return run
