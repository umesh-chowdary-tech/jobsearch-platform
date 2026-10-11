"""Applications: every job a person applied to, however they applied, and
what the inbox says happened.

pipeline.json holds the person's decisions about postings the watcher found.
But most applications start elsewhere - in one two-month sample, about three
in forty came from the watcher. So the inbox keeps its own ledger here, links
an application to a tracked posting when there is one, and moves that
posting's stage too.

Matching, most reliable first:
  1. the requisition id    - one employer had three applications in flight;
                             only the id told the rejection of one from the
                             two still open
  2. the mail thread       - a reply belongs to what it replies to
  3. company + role        - two roles at one company are two applications
  4. company alone         - only when one application there is still open,
                             and flagged for a look
Anything the rules were unsure of, or a company name guessed from a display
name, is flagged - never silently trusted. An unsure reading doesn't set the
status either: until the model or the person settles it, the record says
"checking". A job-board digest's "53 roles awaiting your response" read as an
interview on the weak words "next steps", and showed as one while the model's
correct answer waited.

Stored per event: date, kind, subject, sender, message-id and the one sentence
that decided it. Never the body.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import pipeline
from .busy import locked_edit
from .profile import Profile
from .mail_rules import Facts, Verdict

STATUS_OF = {
    "received": "applied", "viewed": "applied", "interview": "interviewing",
    "rejected": "rejected", "cancelled": "cancelled", "offer": "offer",
    "on_hold": "on_hold", "portal": "update_waiting", "inbound": "approached",
}
# Same-day tie-break: the later stage of a process wins.
RANK = {"approached": 0, "applied": 1, "update_waiting": 2, "on_hold": 3,
        "interviewing": 4, "rejected": 5, "cancelled": 5, "offer": 6}
# "checking": every email on it so far is an unsure reading - see status_of.
OPEN = {"approached", "applied", "update_waiting", "on_hold", "interviewing", "checking"}
LEDGER_KINDS = set(STATUS_OF)

# What a status means for a tracked posting's stage. Forward only; an outcome
# closes it. on_hold / update_waiting / approached change nothing.
STAGE_OF = {"applied": "applied", "interviewing": "interviewing", "offer": "offer",
            "rejected": "closed", "cancelled": "closed"}
STAGE_ORDER = {"new": 0, "shortlisted": 1, "applied": 2, "interviewing": 3, "offer": 4}


# ── storage ──────────────────────────────────────────────────────────────────

def path(prof: Profile) -> Path:
    return prof.var / "applications.json"


def load(prof: Profile) -> dict[str, Any]:
    p = path(prof)
    if not p.exists():
        return {"apps": {}, "seen": [], "naukri": {"applied": {}, "status_pings": [], "neo": {}}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # An unreadable ledger must not look empty: the next sync would
        # rebuild it from sixty days of mail and lose every manual correction.
        raise ValueError(f"{p} is unreadable - fix or remove it by hand")
    d.setdefault("apps", {}); d.setdefault("seen", [])
    d.setdefault("naukri", {"applied": {}, "status_pings": [], "neo": {}})
    return d


def save(prof: Profile, ledger: dict[str, Any]) -> None:
    p = path(prof)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix="applications.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, p)


# ── matching ─────────────────────────────────────────────────────────────────

_CO_NOISE = re.compile(r"\b(?:inc|ltd|llc|llp|pvt|private|limited|co|corp|corporation|company|"
                       r"technologies|technology|careers|india|the)\b")


def norm_company(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())
    return " ".join(_CO_NOISE.sub(" ", s).split())


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t and t not in {"the", "a", "an", "of", "and"}}


def role_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def _same_company(a: str, b: str) -> bool:
    na, nb = norm_company(a), norm_company(b)
    return bool(na and nb and (na == nb or na.startswith(nb + " ") or nb.startswith(na + " ")))


def _is_company(app: dict[str, Any], name: str) -> bool:
    """The app's company, or a name it was known by before the person
    corrected it - otherwise the next email signed by a recruiter's first name
    would start a new record."""
    return any(_same_company(n, name) for n in [app.get("company", "")] + list(app.get("aliases") or []))


def match(ledger: dict[str, Any], f: Facts, thread: str) -> tuple[str | None, str]:
    """(application id or None, how it matched)."""
    apps = {k: a for k, a in ledger["apps"].items() if not a.get("ignored")}
    if f.req_id:
        for k, a in apps.items():
            if a.get("req_id") == f.req_id and (not f.company or not a.get("company")
                                                 or _is_company(a, f.company)):
                return k, "requisition id"
        # Two requisitions are two applications, whatever else they share.
        # One employer's three receipts had one subject, so the mail client put
        # them in one thread, and the thread match merged them into one.
        apps = {k: a for k, a in apps.items() if not a.get("req_id")}
    if thread:
        for k, a in apps.items():
            # Gmail threads by subject, so two receipts titled "We've received
            # your application" for two roles at one company shared one thread.
            if f.role and a.get("role") and role_similarity(f.role, a["role"]) < 0.6:
                continue
            if any(e.get("thread") == thread for e in a["events"]) and (
                    not f.company or _is_company(a, f.company)):
                return k, "email thread"
    if not f.company:
        return None, ""
    same = {k: a for k, a in apps.items() if _is_company(a, f.company)}
    if f.role:
        scored = sorted(((role_similarity(f.role, a.get("role", "")), k) for k, a in same.items()), reverse=True)
        if scored and scored[0][0] >= 0.6:
            return scored[0][1], "company and role"
        # An application there with no role yet (its receipt named none) is
        # the likely one - but only if it is the only such.
        blank = [k for k, a in same.items() if not a.get("role")]
        if len(blank) == 1:
            return blank[0], "company (role filled in)"
        return None, ""
    still_open = [k for k, a in same.items() if a.get("status") in OPEN]
    if len(still_open) == 1:
        return still_open[0], "company only"
    return None, ""


# ── recording ────────────────────────────────────────────────────────────────

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40] or "x"


def _new_id(ledger: dict[str, Any], f: Facts, mail_date: str) -> str:
    base = "-".join(p for p in (_slug(f.company or "unknown"), _slug(f.req_id or f.role or mail_date[:10])) if p)
    k, n = base, 2
    while k in ledger["apps"]:
        k, n = f"{base}-{n}", n + 1
    return k


def status_of(events: list[dict[str, Any]], confirmed_at: str = "") -> str:
    """The status of the most recent event; on the same day, the later stage.

    An unsure reading (confidence "low") counts only once confirmed - by the
    person, or by the model quoting the email - so a guess never shows as an
    interview, a rejection or an offer. A record with nothing but guesses is
    "checking"."""
    real = [e for e in events if e.get("status")
            and (e.get("confidence") != "low" or e["date"] <= (confirmed_at or ""))]
    if not real:
        return "checking" if any(e.get("status") for e in events) else "applied"
    last = max(real, key=lambda e: (e["date"][:10], RANK.get(e["status"], 0), e["date"]))
    return last["status"]


def record(ledger: dict[str, Any], mail: Any, v: Verdict, f: Facts) -> dict[str, Any] | None:
    """Apply one email to the ledger. Returns what changed, or None."""
    mid = mail.message_id or f"uid:{mail.uid}"
    if mid in ledger["seen"]:
        return None
    ledger["seen"].append(mid)
    day = mail.date[:10]

    if v.kind == "naukri_applied":
        ledger["naukri"]["applied"][day] = v.extra
        return {"naukri": "applied", "count": v.extra.get("count", 0), "date": day}
    if v.kind == "naukri_status":
        ledger["naukri"]["status_pings"].append(day)
        return {"naukri": "status"}
    if v.kind == "naukri_neo":
        ledger["naukri"]["neo"][day] = v.extra
        return {"naukri": "neo"}
    if v.kind not in LEDGER_KINDS:
        return None                                   # unclear: read, and let go

    app_id, how = match(ledger, f, mail.thread)
    created = app_id is None
    if created:
        app_id = _new_id(ledger, f, mail.date)
        ledger["apps"][app_id] = {
            "id": app_id, "company": f.company, "company_source": f.company_source,
            "role": f.role, "req_id": f.req_id, "source": mail.domain,
            "posting_id": None, "status": "", "first_seen": day, "updated": day,
            "events": [], "look": [], "ignored": False,
        }
    app = ledger["apps"][app_id]
    before = app["status"]
    # Fill in what the first email did not say.
    if f.role and not app.get("role"):
        app["role"] = f.role
    if f.req_id and not app.get("req_id"):
        app["req_id"] = f.req_id
    if f.company and (not app.get("company") or app.get("company_source") == "display"
                      and f.company_source in ("sender", "subject")):
        app["company"], app["company_source"] = f.company, f.company_source

    app["events"].append({
        "date": mail.date, "kind": v.kind, "status": STATUS_OF[v.kind], "confidence": v.confidence,
        "subject": mail.subject[:200], "sender": mail.from_addr, "message_id": mid,
        "thread": mail.thread, "evidence": v.evidence[:180], "matched_by": how or "new",
    })
    app["events"].sort(key=lambda e: e["date"])
    app["status"] = status_of(app["events"], app.get("confirmed_at", ""))
    app["updated"] = max(app["updated"], day)

    app["look"] = look_reasons(app)
    return {"app": app_id, "new": created, "from": before, "to": app["status"], "how": how or "new",
            "kind": v.kind, "date": day, "company": app["company"], "role": app["role"]}


def look_reasons(app: dict[str, Any]) -> list[str]:
    """Why a human should check this one - worked out from its current state,
    so a reason goes away when a later email fixes it (a company's name came
    from its display name first, then from a subject that spelled it)."""
    if app.get("ignored"):
        return []
    since = app.get("confirmed_at") or ""
    out = []
    for e in app["events"]:
        if e["date"] <= since:
            continue
        if e.get("confidence") == "low":
            out.append(f"unsure what “{e['subject'][:60]}” means")
        if e.get("matched_by") == "company only":
            out.append(f"matched by company alone: “{e['subject'][:60]}”")
    if not since:
        if not app.get("company"):
            out.append("no company name in the email")
        elif app.get("company_source") == "display":
            out.append(f"company taken from the sender’s name “{app['company']}”")
    return out


# ── tracked postings ─────────────────────────────────────────────────────────

def posting_req(pid: str) -> str | None:
    """The requisition number inside a posting id, where the id carries one:
    Workday's wf-R-123456 -> R-123456, Oracle's acme-210000001 -> 210000001,
    Atlassian's (iCIMS) atlassian-27391 -> 27391."""
    m = re.search(r"R-\d{5,7}", pid)
    if m:
        return m.group(0)
    m = re.fullmatch(r"[a-z0-9]+-(\d{5,10})", pid)
    return m.group(1) if m else None


def link_postings(prof: Profile, ledger: dict[str, Any]) -> list[tuple[str, str]]:
    """Attach applications to postings the watcher tracks. Requisition id
    first - Workday posting ids carry it ("wf-R-123456") - then company and
    title. Returns (application id, posting id) for each new link."""
    from .outreach import company_name, load_postings
    postings = load_postings(prof)
    linked = []
    taken = {a["posting_id"] for a in ledger["apps"].values() if a.get("posting_id")}
    for a in ledger["apps"].values():
        if a.get("posting_id") or a.get("ignored"):
            continue
        hit = None
        if a.get("req_id"):
            hit = next((pid for pid in postings if pid not in taken and
                        (pid == a["req_id"] or pid.endswith("-" + a["req_id"]))), None)
        if not hit and a.get("company") and a.get("role"):
            best = 0.0
            for pid, p in postings.items():
                if pid in taken:
                    continue
                # Both carry a requisition id and they differ: never the same
                # job, however alike the titles. The first dry run linked one
                # requisition to another's posting on a matching title - and
                # would have closed the wrong posting. Oracle's plain numbers
                # did it again, and closed a strong fit.
                pid_req = posting_req(pid)
                if a.get("req_id") and pid_req and pid_req != a["req_id"]:
                    continue
                name, _ = company_name(prof, str(p.get("board") or ""), p)
                if not _same_company(name, a["company"]):
                    continue
                s = role_similarity(a["role"], str(p.get("title") or ""))
                if s >= 0.75 and s > best:
                    best, hit = s, pid
        if hit:
            a["posting_id"] = hit
            taken.add(hit)
            linked.append((a["id"], hit))
    return linked


def sync_pipeline(prof: Profile, ledger: dict[str, Any]) -> list[dict[str, Any]]:
    """Move each linked posting's stage to match its application. Forward
    only, and never over the person's own "skipped" or "closed"."""
    from .outreach import variant_for
    data = pipeline.load(prof)
    changes = []
    for a in ledger["apps"].values():
        pid = a.get("posting_id")
        target = STAGE_OF.get(a.get("status", ""))
        if not pid or not target or a.get("ignored"):
            continue
        current = pipeline.status(data, pid)
        if current in pipeline.DONE:
            continue                                   # the person's call stands
        if target != "closed" and STAGE_ORDER.get(target, 0) <= STAGE_ORDER.get(current, 0):
            continue                                   # never backwards
        last = a["events"][-1]
        note = f"from email {last['date'][:10]}: {last['subject'][:80]}"
        if target == "closed":
            note = f"{a['status']} - " + note
        v = variant_for(prof, pid) if target == "applied" else None
        pipeline.set_status(prof, pid, target, note, variant=v.id if v else None)
        data = pipeline.load(prof)
        changes.append({"posting": pid, "from": current, "to": target, "note": note})
    return changes


@locked_edit
def relink(prof: Profile) -> list[dict[str, Any]]:
    """Link and move postings now, after a change outside the sync. Razorpay's
    receipt named no company, so the sync couldn't link it; Groq named it
    minutes later, but the posting stayed under "new" until the next sync."""
    ledger = load(prof)
    if link_postings(prof, ledger):
        save(prof, ledger)
    return sync_pipeline(prof, ledger)


# ── the person's corrections ──────────────────────────────────────────────────────

@locked_edit
def set_status(prof: Profile, app_id: str, status: str) -> dict[str, Any]:
    if status not in RANK:
        raise ValueError(f"status must be one of {', '.join(RANK)}")
    ledger = load(prof)
    a = ledger["apps"][app_id]
    a["events"].append({"date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "kind": "manual", "status": status, "confidence": "high",
                        "subject": "set by hand", "sender": "", "message_id": "", "thread": "",
                        "evidence": "", "matched_by": "manual"})
    a["confirmed_at"] = _now()
    a["status"] = status_of(a["events"], a["confirmed_at"])
    a["look"] = look_reasons(a)
    save(prof, ledger)
    relink(prof)
    return a


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@locked_edit
def confirm(prof: Profile, app_id: str, company: str = "", role: str = "") -> dict[str, Any]:
    """"Looks right" - optionally with the company or role corrected. Only an
    uncertain email arriving after this flags it again."""
    ledger = load(prof)
    a = ledger["apps"][app_id]
    if company.strip() and company.strip() != a.get("company"):
        if a.get("company"):
            a.setdefault("aliases", []).append(a["company"])
        a["company"], a["company_source"] = company.strip(), "manual"
    if role.strip():
        a["role"] = role.strip()
    a["confirmed_at"] = _now()
    a["look"] = look_reasons(a)
    save(prof, ledger)
    relink(prof)
    return a


@locked_edit
def merge(prof: Profile, from_id: str, into_id: str) -> dict[str, Any]:
    """"Same as..." - two records the rules could not join (emails from one
    company that named no role). Events move over; the empty one is removed."""
    if from_id == into_id:
        raise ValueError("an application cannot be merged into itself")
    ledger = load(prof)
    src, dst = ledger["apps"][from_id], ledger["apps"][into_id]
    dst["events"] = sorted(dst["events"] + src["events"], key=lambda e: e["date"])
    for k in ("role", "req_id", "posting_id"):
        if not dst.get(k) and src.get(k):
            dst[k] = src[k]
    # The removed record's names still arrive on email (an agency's name for
    # the company), and must keep finding this one.
    names = [*src.get("aliases", []), src.get("company") or ""]
    for n in names:
        if n and not _same_company(n, dst.get("company") or "") and n not in dst.get("aliases", []):
            dst.setdefault("aliases", []).append(n)
    dst["status"] = status_of(dst["events"], dst.get("confirmed_at", ""))
    dst["updated"] = max(dst["updated"], src["updated"])
    dst["first_seen"] = min(dst["first_seen"], src["first_seen"])
    dst["look"] = look_reasons(dst)
    del ledger["apps"][from_id]
    save(prof, ledger)
    return dst


@locked_edit
def ignore(prof: Profile, app_id: str, ignored: bool = True) -> dict[str, Any]:
    """Not an application (a marketplace's nudge, a newsletter that got through)."""
    ledger = load(prof)
    ledger["apps"][app_id]["ignored"] = ignored
    ledger["apps"][app_id]["look"] = look_reasons(ledger["apps"][app_id])
    save(prof, ledger)
    return ledger["apps"][app_id]


def _use(a: dict[str, Any], s: dict[str, Any], how: str) -> str:
    """Apply Groq's reading to the record in place. Returns the id it says
    this is the same job as, or ""."""
    if s.get("not_an_application"):
        a["ignored"] = True
        a["status"] = status_of(a["events"], a.get("confirmed_at", ""))
        return ""
    for o in s.get("outcomes") or []:
        for e in a["events"]:
            if e["message_id"] == o["message_id"]:
                e.update(kind=o["outcome"], status=STATUS_OF[o["outcome"]], confidence="high",
                         evidence=o["quote"][:180], read_by=f"{s.get('by', 'model')}, {how}")
    name = (s.get("company") or {}).get("value")
    if name and name != a.get("company"):
        if a.get("company"):
            a.setdefault("aliases", []).append(a["company"])
        a["company"], a["company_source"] = name, "model"
    if (s.get("role") or {}).get("value"):
        a["role"] = s["role"]["value"]
    a["confirmed_at"] = _now()
    a["status"] = status_of(a["events"], a["confirmed_at"])
    return (s.get("same_as") or {}).get("id", "")


@locked_edit
def answer_suggestion(prof: Profile, app_id: str, use: bool) -> dict[str, Any]:
    """Groq's reading (mail_ai.py), used or dismissed. Either way these emails
    are not asked about again, and the text sent for them is forgotten."""
    from .mail_ai import forget_sent
    ledger = load(prof)
    a = ledger["apps"][app_id]
    s = a.pop("suggestion", None)
    if s is None:
        raise ValueError("there is no suggestion to answer")
    a["asked"] = sorted(set(a.get("asked") or []) | set(s.get("message_ids") or []))
    into = _use(a, s, "confirmed") if use else ""
    a["look"] = look_reasons(a)
    save(prof, ledger)
    forget_sent(prof, app_id)
    if into and into in ledger["apps"]:
        merged = merge(prof, app_id, into)
        return confirm(prof, merged["id"])
    relink(prof)
    return a


# ── Groq's reading, applied without a click ──────────────────────────────────
#
# Groq's answers waited for a click, and meanwhile the rules' guess showed. Now
# a reading is applied as soon as it arrives when every part of it is backed
# by words quoted from the email (ground() in mail_ai.py has already dropped
# what wasn't) and it answers every question the record was flagged with. It
# can be undone; what it replaced is kept on the record until then.
# Merging two records stays a click: it is the one change that is hard to
# take back by hand. Learning a rule stays a click too (mail_learn.py).

AUTO_FIELDS = ("events", "status", "ignored", "company", "company_source", "role", "aliases",
               "confirmed_at", "look", "asked")


def auto_blocker(a: dict[str, Any], s: dict[str, Any]) -> str:
    """Why this reading needs the person - or "" when it can be applied as it is."""
    if s.get("dropped"):
        return "Groq said something it couldn't show in the email"
    if s.get("same_as"):
        return "Groq says it is the same job as another record"
    read = set(s.get("message_ids") or [])
    if s.get("not_an_application"):
        if any(e.get("message_id") not in read for e in a["events"]):
            return "the record has emails Groq didn't read"
        return ""
    since = a.get("confirmed_at") or ""
    answered = {o["message_id"] for o in s.get("outcomes") or []}
    unsure = {e["message_id"] for e in a["events"]
              if e.get("message_id") and e["date"] > since and e.get("confidence") == "low"}
    if unsure - answered:
        return "Groq didn't settle every unsure email"
    look = " ".join(a.get("look") or [])
    if "matched by company alone" in look:
        return "an email was matched to it by company alone"
    if ("no company name" in look or "company taken from" in look) and not s.get("company"):
        return "Groq couldn't name the company"
    if not (answered or s.get("company") or s.get("role")):
        return "nothing to apply"
    return ""


def apply_auto(a: dict[str, Any], s: dict[str, Any]) -> None:
    """Apply a reading auto_blocker() passed, keeping what it replaced."""
    before = {k: copy.deepcopy(a[k]) for k in AUTO_FIELDS if k in a}
    a.pop("suggestion", None)
    a["asked"] = sorted(set(a.get("asked") or []) | set(s.get("message_ids") or []))
    _use(a, s, "applied automatically")
    a["look"] = look_reasons(a)
    a["auto"] = {"at": _now(), "by": s.get("by", ""), "summary": s.get("summary", ""),
                 "before": before, "suggestion": s}


@locked_edit
def undo_auto(prof: Profile, app_id: str) -> dict[str, Any]:
    """Put the record back as it was, with Groq's reading waiting for a click."""
    ledger = load(prof)
    a = ledger["apps"][app_id]
    auto = a.pop("auto", None)
    if not auto:
        raise ValueError("nothing was applied automatically")
    for k in AUTO_FIELDS:
        if k in auto["before"]:
            a[k] = auto["before"][k]
        else:
            a.pop(k, None)
    a["suggestion"] = {**auto["suggestion"], "undone": True}     # never applied again on its own
    save(prof, ledger)
    return a
