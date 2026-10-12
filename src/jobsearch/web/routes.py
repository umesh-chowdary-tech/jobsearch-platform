"""The app's JSON API, for whoever is asking.

Two hosts serve it: web/server.py, the local app - one person on their own
machine, every screen - and hosted/app.py, the service where each signed-in
person gets their own profile and only the HOSTED_GET / HOSTED_POST routes.
Both hand a request here as a path, a query and a JSON body, and turn what
comes back - or an ApiError - into HTTP. No HTTP lives in this module, so the
local app and the hosted one cannot drift apart.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import feedback, onboard, outreach, pipeline
from ..busy import EditTimeout, holder, request_stop
from ..profile import Profile
from ..registry import resolve_escalation
from . import state
from .actions import ACTIONS, HOSTED_ACTIONS, under_lock
from .runner import Runner

LOCAL, HOSTED = "local", "hosted"
log = logging.getLogger("jobsearch.api")


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


def as_error(exc: Exception, scope: str = LOCAL) -> tuple[int, dict[str, Any]]:
    """The status and body for anything a route raised. On the hosted service
    an unexpected error says nothing about the server's insides."""
    if isinstance(exc, ApiError):
        return exc.status, {"error": exc.message, **exc.extra}
    if isinstance(exc, (outreach.OutreachError, pipeline.PipelineError)):
        return 409, {"error": str(exc)}
    if isinstance(exc, onboard.SetupError):
        return 400, {"error": str(exc)}
    if isinstance(exc, EditTimeout):
        return 409, {"error": "still saving another change - try again in a moment"}
    if scope == HOSTED:
        log.exception("unexpected error")
        return 500, {"error": "Something went wrong on our side. Try again in a moment."}
    return 500, {"error": f"{type(exc).__name__}: {exc}"}


_ID = r"[\w.-]+"
# What a signed-in person may ask the hosted service. Anything else is a 404
# there: the inbox, outreach, the board registry and the schedule belong to
# the local app or to whoever runs the service.
HOSTED_GET = tuple(re.compile(p) for p in (
    r"/api/(overview|today|postings|resumes|profile|setup|market|actions|job)",
    rf"/api/postings/{_ID}", rf"/api/postings/{_ID}/kit"))
HOSTED_POST = tuple(re.compile(p) for p in (
    r"/api/setup/(upload|draft|item|fact|restore|skills|save|link|note|remove|reset|ask|answer|apply|labels)",
    r"/api/run",
    rf"/api/postings/{_ID}/(status|favorite|later|feedback)"))

# What a resume upload may be, by the type the page sends.
UPLOAD_TYPES = {"application/pdf": ".pdf", "text/plain": ".txt",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx"}


def pick_file(folder: Path, name: str) -> Path | None:
    """The file in `folder` named exactly `name`, found by listing the folder,
    so the text of a request never becomes a path to open."""
    if not folder.is_dir():
        return None
    return next((f for f in folder.iterdir() if f.name == name and f.is_file()), None)


def allowed(method: str, path: str, scope: str) -> bool:
    if scope == LOCAL:
        return True
    pats = HOSTED_GET if method in ("GET", "HEAD") else HOSTED_POST
    return any(p.fullmatch(path) for p in pats)


def _overview(prof: Profile, scope: str) -> dict[str, Any]:
    if scope == LOCAL:
        return state.overview(prof)
    # No PC scheduler, pause or stop on the service; and whose run is going
    # on is nobody else's business.
    log_ = state.cycle_log(prof, 1)
    who = holder(prof.shared)
    return {"busy": {"what": "updating", "since": who.get("since")} if who else None,
            "last_cycle": log_[0] if log_ else None,
            "now": datetime.now(timezone.utc).isoformat(timespec="seconds")}


# ── GET ──────────────────────────────────────────────────────────────────────

def get(prof: Profile, path: str, query: dict[str, list[str]], runner: Runner, scope: str = LOCAL) -> Any:
    if not allowed("GET", path, scope):
        raise ApiError(404, "not found")
    if path == "/api/overview":
        return _overview(prof, scope)
    if path == "/api/actions":
        names = ACTIONS if scope == LOCAL else {k: ACTIONS[k] for k in HOSTED_ACTIONS}
        return {k: v[0] for k, v in names.items()}
    routes = {
        "/api/today": lambda: state.today(prof),
        "/api/postings": lambda: state.postings(prof),
        "/api/resumes": lambda: state.resumes(prof),
        "/api/outreach": lambda: state.outreach_view(prof),
        "/api/market": lambda: state.market(prof),
        "/api/system": lambda: state.system(prof),
        "/api/profile": lambda: state.profile(prof),
        "/api/applications": lambda: state.applications(prof),
        "/api/setup": lambda: onboard.state(prof),
        "/api/job": lambda: runner.snapshot(int((query.get("since") or ["0"])[0] or 0)),
    }
    if path in routes:
        return routes[path]()
    m = re.fullmatch(rf"/api/postings/({_ID})", path)
    if m:
        data = state.posting(prof, m.group(1))
        if data is None:
            raise ApiError(404, "no such posting")
        return data
    m = re.fullmatch(rf"/api/postings/({_ID})/leads", path)
    if m:
        return outreach.find_leads(prof, outreach.find_posting(prof, m.group(1)))
    m = re.fullmatch(rf"/api/applications/({_ID})/sent", path)
    if m:
        from ..mail_ai import sent_for
        return sent_for(prof, m.group(1))
    m = re.fullmatch(rf"/api/postings/({_ID})/kit", path)
    if m:
        from ..apply_kit import build_kit
        return build_kit(prof, m.group(1))
    raise ApiError(404, "not found")


# ── POST ─────────────────────────────────────────────────────────────────────

def upload(prof: Profile, ctype: str, data: bytes, filename: str = "") -> dict[str, Any]:
    """A resume, as the raw request body; its name (X-Filename) only labels
    it on the screen - the type decides how it is read. The host has already
    refused a body over onboard.MAX_BYTES before reading it."""
    ctype = ctype.split(";")[0].strip().lower()
    if ctype not in UPLOAD_TYPES:
        raise ApiError(415, "upload a PDF, a Word file (.docx) or a plain text file")
    if not data:
        raise ApiError(400, "the file is empty")
    if len(data) > onboard.MAX_BYTES:
        raise ApiError(413, "that file is larger than 5 MB")
    ext = UPLOAD_TYPES[ctype]
    stem = Path(urllib.parse.unquote(filename or "")).stem[:70] or "resume"
    onboard.upload(prof, data, f"{stem}{ext}")
    return onboard.state(prof) | {"will_send": onboard.to_send(prof)}


def _setup(prof: Profile, step: str, body: dict[str, Any]) -> Any:
    """Each step returns the whole setup state."""
    if step == "draft":
        onboard.draft(prof, source=str(body["source"]) if body.get("source") else None)
    elif step == "item":
        choose = body.get("choose")
        if choose is not None and (isinstance(choose, bool) or not isinstance(choose, int)):
            raise ApiError(400, "choose must be a number")
        onboard.update_item(prof, str(body.get("id") or ""), body.get("state"),
                            body.get("fields") if isinstance(body.get("fields"), dict) else None, choose)
    elif step == "link":
        onboard.add_link(prof, str(body.get("kind") or ""), str(body.get("url") or ""))
    elif step == "note":
        onboard.set_note(prof, str(body.get("text") or ""))
    elif step == "remove":
        onboard.remove_source(prof, str(body.get("id") or ""))
    elif step == "reset":
        onboard.reset(prof)
    elif step == "ask":
        onboard.ask(prof)
    elif step == "answer":
        onboard.answer(prof, str(body.get("id") or ""), str(body.get("text") or ""), body.get("skip") is True)
    elif step == "apply":
        onboard.apply_answers(prof)
    elif step == "labels":
        angles = body.get("angles")
        if angles is not None and not (isinstance(angles, list) and all(isinstance(a, str) for a in angles)):
            raise ApiError(400, "angles must be a list of words")
        cut = body.get("cut_first")
        onboard.set_labels(prof, str(body.get("entry") or ""), angles, cut if isinstance(cut, bool) else None)
    elif step == "fact":
        onboard.add_fact(prof, str(body.get("entry") or ""), str(body.get("text") or ""))
    elif step == "restore":
        if isinstance(body.get("index"), bool) or not isinstance(body.get("index"), int):
            raise ApiError(400, "index must be a number")
        onboard.restore(prof, body["index"])
    elif step == "skills":
        tiers = body.get("tiers")
        if not isinstance(tiers, dict):
            raise ApiError(400, "tiers must be an object")
        onboard.set_tiers(prof, tiers)
    elif step == "save":
        prefs = body.get("prefs") if isinstance(body.get("prefs"), dict) else {}
        res = onboard.save(prof, prefs, replace=body.get("replace") is True)
        return onboard.state(prof) | {"result": res}
    else:
        raise ApiError(404, "not found")
    return onboard.state(prof)


def _posting_action(prof: Profile, pid: str, what: str, body: dict[str, Any]) -> Any:
    if state.posting(prof, pid) is None:
        raise ApiError(404, "no such posting")
    if what == "status":
        new = str(body.get("status") or "")
        variant = outreach.variant_for(prof, pid)
        return pipeline.set_status(prof, pid, new, str(body.get("note") or ""),
                                   variant=variant.id if variant else None)
    if what == "feedback":
        good = body.get("good")
        if not isinstance(good, bool):
            raise ApiError(400, "send {\"good\": true} or {\"good\": false}")
        return feedback.record(prof, state.raw_posting(prof, pid) or {"id": pid}, good, str(body.get("note") or ""))
    if what == "favorite":
        on = body.get("on")
        if not isinstance(on, bool):
            raise ApiError(400, "send {\"on\": true} or {\"on\": false}")
        return pipeline.set_favorite(prof, pid, on)
    if body.get("clear") is True:
        return pipeline.clear_later(prof, pid)
    if "until" in body and body["until"] is not None:
        # The calendar. The page sends local midnight of the day picked, with
        # its offset, so "back on Oct 3" means the morning of Oct 3 where the
        # person is, not 05:30.
        try:
            until = datetime.fromisoformat(str(body["until"]).replace("Z", "+00:00"))
        except ValueError:
            raise ApiError(400, "until must be an ISO date-time") from None
        return pipeline.save_for_later(prof, pid, until=until)
    return pipeline.save_for_later(prof, pid, body.get("days"))


def _run(prof: Profile, body: dict[str, Any], runner: Runner, scope: str) -> tuple[int, Any]:
    name = str(body.get("action") or "")
    if name not in ACTIONS or (scope == HOSTED and name not in HOSTED_ACTIONS):
        raise ApiError(400, f"unknown action {name!r}")
    who = holder(prof.shared)
    if who or runner.busy:
        raise ApiError(409, "busy", holder=({"what": "an update"} if scope == HOSTED else who)
                       or {"what": "the app"})
    label, fn = ACTIONS[name]
    job = runner.start(name, label, under_lock(name, fn), body)
    if job is None:
        raise ApiError(409, "busy", holder={"what": "the app"})
    return 202, {"job": job.id}


def post(prof: Profile, path: str, body: dict[str, Any], runner: Runner, scope: str = LOCAL) -> tuple[int, Any]:
    if not allowed("POST", path, scope) or path == "/api/setup/upload":
        raise ApiError(404, "not found")
    if path.startswith("/api/setup/"):
        return 200, _setup(prof, path[len("/api/setup/"):], body)
    if path == "/api/run":
        return _run(prof, body, runner, scope)
    m = re.fullmatch(rf"/api/postings/({_ID})/(status|favorite|later|feedback)", path)
    if m:
        return 200, _posting_action(prof, m.group(1), m.group(2), body)

    # ── the local app only ──────────────────────────────────────────────────
    if path == "/api/stop":
        who = request_stop(prof.shared)
        if who is None:
            raise ApiError(409, "nothing is running")
        return 202, {"stopping": who}
    if path == "/api/schedule/pause":
        from ..cycle import pause
        hours = body.get("hours")
        if hours is not None and (isinstance(hours, bool) or not isinstance(hours, (int, float))
                                  or not 0 < hours <= 24 * 30):
            raise ApiError(400, "hours must be between 0 and 720, or null for until resumed")
        return 200, pause(prof.shared, hours)
    if path == "/api/schedule/resume":
        from ..cycle import resume
        resume(prof.shared)
        return 200, {"paused": None}
    m = re.fullmatch(r"/api/mail-rules/([0-9a-f]{10})/(approve|reject)", path)
    if m:
        from .. import mail_learn
        try:
            fn = mail_learn.approve if m.group(2) == "approve" else mail_learn.reject
            return 200, fn(prof, m.group(1))
        except ValueError as exc:
            raise ApiError(404, str(exc)) from exc
    m = re.fullmatch(rf"/api/applications/({_ID})/(confirm|status|ignore|merge|suggestion|undo-auto)", path)
    if m:
        return 200, _application(prof, m.group(1), m.group(2), body)
    if path == "/api/outreach/draft":
        d = outreach.draft(prof, str(body.get("posting") or ""), str(body.get("to") or ""),
                           str(body.get("how") or ""), str(body.get("context") or ""),
                           use_model=not body.get("no_model"), force=bool(body.get("force")),
                           employer=str(body.get("employer") or ""))
        return 200, d.to_dict()
    m = re.fullmatch(rf"/api/outreach/({_ID})/mark", path)
    if m:
        return 200, outreach.mark(prof, m.group(1), str(body.get("status") or ""), str(body.get("note") or ""))
    if path == "/api/escalations/resolve":
        provider, slug = str(body.get("provider") or ""), str(body.get("slug") or "")
        note = str(body.get("note") or "").strip()
        if not (provider and slug and note):
            raise ApiError(400, "provider, slug and a note are all required")
        resolve_escalation(prof.shared / "escalations.jsonl", provider, slug, note)
        return 200, {"resolved": f"{provider}:{slug}"}
    raise ApiError(404, "not found")


def _application(prof: Profile, app_id: str, what: str, body: dict[str, Any]) -> Any:
    from .. import applications as A
    if app_id not in A.load(prof)["apps"]:
        raise ApiError(404, "no such application")
    try:
        if what == "confirm":
            return A.confirm(prof, app_id, str(body.get("company") or ""), str(body.get("role") or ""))
        if what == "status":
            return A.set_status(prof, app_id, str(body.get("status") or ""))
        if what == "ignore":
            return A.ignore(prof, app_id, bool(body.get("ignored", True)))
        if what == "suggestion":
            return A.answer_suggestion(prof, app_id, body.get("use") is True)
        if what == "undo-auto":
            return A.undo_auto(prof, app_id)
        into = str(body.get("into") or "")
        if into not in A.load(prof)["apps"]:
            raise ApiError(404, "no application to merge into")
        return A.merge(prof, app_id, into)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from exc
