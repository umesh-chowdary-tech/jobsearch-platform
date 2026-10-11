"""One inbox sync: read what is new, record it, move tracked postings.

Order of writes is deliberate: the ledger first, then the pipeline, then the
mailbox state last. A crash anywhere before the state is saved means the next
sync reads the same mail again - and the ledger's message-id set turns that
into a no-op. Saving the state first would lose mail instead.

`dry_run` reads everything and writes nothing - for the first run, and for
checking a rule change against real mail.
"""
from __future__ import annotations

from typing import Any

from .profile import Profile
from . import applications as apps
from . import mail_rules as rules
from .busy import editing
from .mailbox import Inbox, load_state, save_state
from .models import utcnow


def sync(prof: Profile, first_days: int = 60, dry_run: bool = False,
         say=print) -> dict[str, Any]:
    data = prof                    # the shared senders and this person's learned rules
    state = load_state(prof)

    with Inbox(prof) as box:
        uids = box.new_uids(state, first_days)
        heads = box.headers(uids)
        wanted = [m for m in heads if rules.is_candidate(m.from_addr, m.subject, data, me=box.user)]
        bodies = box.bodies([m.uid for m in wanted]) if wanted else {}
        new_state = {"uidvalidity": box.uidvalidity,
                     "last_uid": max(uids) if uids else int(state.get("last_uid") or 0),
                     "synced_at": utcnow()}

    # Loaded only now, after the slow network part, and saved under the same
    # lock the app's corrections take - so a click can't land in between.
    with editing(prof.var):
        return _record(prof, data, state, wanted, bodies, heads, new_state, dry_run, say)


def _record(prof: Profile, data: Profile, state: dict[str, Any], wanted: list, bodies: dict,
            heads: list, new_state: dict[str, Any], dry_run: bool, say) -> dict[str, Any]:
    ledger = apps.load(prof)
    changes = []
    for m in sorted(wanted, key=lambda m: m.date):
        m.body = bodies.get(m.uid, "")
        v = rules.classify(m.from_addr, m.subject, m.body, data)
        f = rules.facts(m.from_addr, m.from_name, m.subject, m.body, data)
        ch = apps.record(ledger, m, v, f)
        if ch:
            changes.append(ch)
    linked = apps.link_postings(prof, ledger)

    report: dict[str, Any] = {
        "read": len(heads), "candidates": len(wanted),
        "events": sum(1 for c in changes if "app" in c),
        "new_apps": sum(1 for c in changes if c.get("new")),
        "status_changes": [c for c in changes if "app" in c and c["from"] != c["to"]],
        "naukri": sum(c.get("count", 0) for c in changes if c.get("naukri") == "applied"),
        "linked": linked, "pipeline": [], "dry_run": dry_run,
        "look": sum(1 for a in ledger["apps"].values() if a.get("look") and not a.get("ignored")),
    }
    if dry_run:
        say(f"dry run - nothing written. read {report['read']}, relevant {report['candidates']}")
        return report

    apps.save(prof, ledger)
    report["pipeline"] = apps.sync_pipeline(prof, ledger)
    save_state(prof, new_state)                        # last, so a crash re-reads
    say(f"read {report['read']}, relevant {report['candidates']}, events {report['events']}, "
        f"new applications {report['new_apps']}, needs a look {report['look']}")
    for c in report["pipeline"]:
        say(f"  posting {c['posting']}: {c['from']} -> {c['to']}  ({c['note']})")
    return report
