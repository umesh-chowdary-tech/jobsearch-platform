"""Jev against the inbox: how often it reads an email the way we know it reads.

The known answers come from the ledger, graded by how they were settled:
  confirmed  - the person confirmed the record, Groq quoted the email for it, an
               approved rule re-read it, or the record was set aside as "not
               an application"
  rules      - the rules were sure (a strong pattern), nothing more
  unsure     - the rules guessed: no answer to score against; Jev's reading
               is listed for the person to judge
plus a sample of mail the rules dropped unread (job alerts, newsletters,
account notices) as "not an application".

**The comparison is lopsided.** Most known answers came from the rules, so the
rules can't be scored fairly on them - only Jev can. Where they disagree, the
email is listed, because either may be the one that's wrong.

What is sent per email: the sender's domain (never the address), the subject
and the body cut to 3,000 characters, redacted as for Groq (mail_ai.redact).
Nothing else. What is kept, in var/mail/jev-eval.json: message ids, subjects,
the answers, and each sent text's size and SHA-256 - never a body.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .profile import Profile
from . import applications as A
from . import mail_rules as R
from .jev import Jev
from .mail_ai import me_words, redact

MAX_CHARS = 3000
DAYS = 80

QUESTION = {"kind": {
    "type": "choice",
    "instructions": ("This is an email in a job seeker's inbox. What does it say about a job "
                     "application they submitted?"),
    "criteria": {
        "received": "The employer or its hiring system confirms that their application was received.",
        "interview": "An invitation to, or the scheduling of, an interview, assessment, test or next round.",
        "rejected": "Their application was turned down.",
        "offer": "A job offer.",
        "cancelled_or_on_hold": "The role they applied for was withdrawn, closed or paused.",
        "recruiter_outreach": "A recruiter or company approaches them about a role they did not apply to.",
        "portal_update": "It only says to log in to a portal to see an update on their application.",
        "not_an_application": ("Not about an application they submitted: job alerts, job-match lists, "
                               "newsletters, marketing, account or security notices, sign-up nudges, "
                               "or an application left unfinished."),
    }}}

LABEL = {"received": "received", "interview": "interview", "rejected": "rejected", "offer": "offer",
         "cancelled": "cancelled_or_on_hold", "on_hold": "cancelled_or_on_hold",
         "inbound": "recruiter_outreach", "portal": "portal_update"}


def known_answers(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """message id -> {truth, source, app}. Unscorable kinds (LinkedIn's
    "viewed", a status set by hand) are left out."""
    out = {}
    for a in ledger["apps"].values():
        since = a.get("confirmed_at") or ""
        for e in a["events"]:
            mid = e.get("message_id")
            if not mid:
                continue
            if a.get("ignored"):
                truth, source = "not_an_application", "confirmed"
            elif e["kind"] not in LABEL:
                continue
            else:
                truth = LABEL[e["kind"]]
                if e.get("read_by") or e.get("reread") or e["date"] <= since:
                    source = "confirmed"
                elif e.get("confidence") == "low":
                    source = "unsure"
                else:
                    source = "rules"
            out[mid] = {"truth": truth, "source": source, "app": a["id"]}
    return out


def state_of(m: Any, me: tuple[set[str], str]) -> str:
    text = f"From: {m.from_name} (@{m.domain})\nSubject: {m.subject}\n\n{R.flat(m.body)}"
    return redact(text, me)[:MAX_CHARS]


def rules_say(m: Any, data_dir: str, me: str) -> str:
    if not R.is_candidate(m.from_addr, m.subject, data_dir, me=me):
        return "not_an_application"
    k = R.classify(m.from_addr, m.subject, m.body, data_dir).kind
    return LABEL.get(k, "not_an_application" if k in ("marketing", "unclear") else k)


def gather(prof: Profile, negatives: int) -> tuple[list[tuple[Any, dict[str, Any]]], str]:
    """(email, what we know about it) for every recorded email and a spread
    sample of dropped ones. Read-only; bodies stay in memory."""
    from .mailbox import Inbox
    data_dir = prof
    known = known_answers(A.load(prof))
    with Inbox(prof) as box:
        heads = box.headers(box.new_uids({}, DAYS))
        me = box.user
        mine = [m for m in heads if m.message_id in known]
        dropped = [m for m in heads if m.message_id not in known and m.from_addr.lower() != me.lower()
                   and not R.is_candidate(m.from_addr, m.subject, data_dir, me=me)]
        step = max(1, len(dropped) // negatives) if negatives else 0
        sample = dropped[::step][:negatives] if negatives else []
        bodies = box.bodies([m.uid for m in mine + sample])
    for m in mine + sample:
        m.body = bodies.get(m.uid, "")
    rows = [(m, known[m.message_id]) for m in mine]
    rows += [(m, {"truth": "not_an_application", "source": "dropped", "app": ""}) for m in sample]
    return rows, me


def run(prof: Profile, negatives: int = 40, dry_run: bool = False, say=print) -> dict[str, Any]:
    jev = Jev(prof)
    if not dry_run and not jev.available:
        return {"error": "JEV_API_KEY not set in .env"}
    rows, me_addr = gather(prof, negatives)
    me = me_words(prof)
    data_dir = prof
    states = [state_of(m, me) for m, _ in rows]
    if dry_run:
        chars = sum(len(s) for s in states)
        say(f"{len(rows)} emails would be sent ({sum(1 for _, k in rows if k['source'] != 'dropped')} recorded, "
            f"{sum(1 for _, k in rows if k['source'] == 'dropped')} dropped unread), "
            f"about {chars // 4:,} tokens (~${chars / 4 / 1e6 * 0.042:.4f}). Nothing was sent.")
        return {"dry_run": True, "emails": len(rows), "chars": chars}
    out = []
    for (m, k), state in zip(rows, states, strict=True):
        ans = jev.ask(state, QUESTION)
        row = {"message_id": m.message_id, "date": m.date[:10], "domain": m.domain, "subject": m.subject[:120],
               "truth": k["truth"], "source": k["source"], "app": k["app"],
               "rules": rules_say(m, data_dir, me_addr),
               "chars": len(state), "sha256": hashlib.sha256(state.encode("utf-8")).hexdigest()}
        if not ans.ok:
            row["error"] = ans.error
            out.append(row)
            if ans.limited or ans.error.startswith("HTTP 401"):
                say(f"  stopped: {ans.limited or ans.error}")
                break
            continue
        got = ans.answers.get("kind") or {}
        row.update(jev=got.get("choice"), confidence=got.get("confidence"),
                   probabilities=got.get("probabilities"), model=ans.model, tokens=ans.tokens)
        out.append(row)
    path = prof.var / "mail" / "jev-eval.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    result = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": out,
              "summary": summarise(out)}
    path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if r.get("jev") and r["source"] != "unsure"]

    def acc(rs: list[dict[str, Any]], who: str = "jev") -> str:
        return f"{sum(r[who] == r['truth'] for r in rs)}/{len(rs)}" if rs else "-"

    by_source = {s: acc([r for r in scored if r["source"] == s])
                 for s in ("confirmed", "rules", "dropped")}
    sure = [r for r in scored if (r.get("confidence") or 0) > 0.9]
    by_truth = {}
    for t in sorted({r["truth"] for r in scored}):
        by_truth[t] = acc([r for r in scored if r["truth"] == t])
    return {
        "jev_overall": acc(scored),
        "jev_by_source": by_source,
        "jev_by_truth": by_truth,
        "jev_confident_over_0.9": {"emails": len(sure), "right": acc(sure)},
        "rules_on_confirmed": acc([r for r in scored if r["source"] == "confirmed"], "rules"),
        "wrong": [{k: r.get(k) for k in ("date", "domain", "subject", "truth", "source", "rules", "jev", "confidence")}
                  for r in scored if r["jev"] != r["truth"]],
        "unsure": [{k: r.get(k) for k in ("date", "domain", "subject", "rules", "jev", "confidence")}
                   for r in rows if r["source"] == "unsure" and r.get("jev")],
        "errors": sum(1 for r in rows if r.get("error")),
        "tokens": sum(r.get("tokens") or 0 for r in rows),
    }
