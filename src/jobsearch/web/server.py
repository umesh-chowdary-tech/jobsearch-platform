"""`jobsearch serve` - the local app, for one profile. Standard library only.

One person, one machine, and the process holds API keys and personal data, so:

  - binds 127.0.0.1 only; nothing else on the network can connect
  - the Host header must name this server, which stops DNS rebinding (a web
    page that points its own hostname at 127.0.0.1 to reach in)
  - anything that changes state is a POST with a JSON body and a same-origin
    Origin, so a page on another site cannot submit a form here
  - files are served only from the resume folders, by resolved path
  - keys never leave the process: the browser gets provider names, not keys
  - an uploaded resume must come from this page (its Origin), as a PDF, Word
    or text file, at most 5 MB

Every action is the same function the CLI runs, captured by web/runner.py and
held under the cross-process `busy` lock the scheduled cycle also takes.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .. import onboard, outreach, pipeline
from ..busy import EditTimeout, busy, holder, request_stop
from ..profile import Profile
from ..registry import resolve_escalation
from . import state
from .runner import Runner

STATIC = Path(__file__).parent / "static"
DEFAULT_PORT = 8765


# ── actions: the CLI's own functions ─────────────────────────────────────────

def _ns(**kw: Any) -> argparse.Namespace:
    return argparse.Namespace(**kw)


def _cli():
    from .. import cli           # late: cli imports everything
    return cli


def _cycle(prof: Profile, o: dict) -> int:
    from ..cycle import run_cycle
    # This person's cycle: the boards are polled for everyone, but only this
    # profile is triaged and ranked - the app never spends on someone else.
    return run_cycle(prof.prof, quiet=False, lock=False, profiles=[prof])   # already holding busy


def _watch(prof: Profile, o: dict) -> int:
    return _cli().cmd_watch(_ns(provider=o.get("provider"), prof=prof.prof))


def _verify(prof: Profile, o: dict) -> int:
    return _cli().cmd_verify(_ns(provider=o.get("provider"), prof=prof.prof))


def _search(prof: Profile, o: dict) -> int:
    names = [c.strip() for c in re.split(r"[,\n]", str(o.get("companies") or "")) if c.strip()]
    if not names:
        print("give at least one company name")
        return 2
    return _cli().cmd_search(_ns(companies=names, force=bool(o.get("force")), prof=prof.prof))


def _triage(prof: Profile, o: dict) -> int:
    from ..triage_cli import run_triage
    run_triage(prof, _ns(all=False, board=o.get("board") or None, quiet=False, recheck=False))
    return 0


def _rank(prof: Profile, o: dict) -> int:
    return _cli().cmd_rank(_ns(limit=int(o["limit"]) if o.get("limit") else None, all=bool(o.get("all")),
                               prof=prof))


def _assemble(prof: Profile, o: dict) -> int:
    from ..assemble_cli import run_assemble
    if not o.get("posting"):
        print("assemble needs a posting")
        return 2
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


def _under_lock(name: str, fn: Callable[[Profile, dict], int]) -> Callable[[Profile, dict], int]:
    def run(prof: Profile, opts: dict) -> int:
        with busy(prof.shared, f"app: {name}") as got:
            if not got:
                who = holder(prof.shared) or {}
                print(f"busy: {who.get('what')} since {who.get('since')} - try again when it ends")
                return 3
            return fn(prof, opts)
    return run


# ── the handler ──────────────────────────────────────────────────────────────

class App:
    def __init__(self, prof: Profile, port: int) -> None:
        self.prof = prof
        self.port = port
        self.runner = Runner(prof)
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{h}" for h in self.hosts}


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


# Under the profile's var/<id>/.
FILE_DIRS = {"resumes": ("resumes",), "manual": ("manual", "resumes")}
# What a resume upload may be, by the type the page sends.
UPLOAD_TYPES = {"application/pdf": ".pdf", "text/plain": ".txt",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx"}

# Scripts are same-origin files only - no inline script, no eval. Inline styles
# are allowed because the gap chart's bar widths are style attributes; with
# every value inserted as text there is no markup-injection path to abuse it.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; base-uri 'none'; "
       "form-action 'none'")
# A resume's HTML carries its own inline <style> and no script.
FILE_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'"


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:

    class Handler(BaseHTTPRequestHandler):
        server_version = "jobsearch"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:
            pass                    # the job console is the log that matters

        # ── plumbing ────────────────────────────────────────────────────────
        def _send(self, status: int, body: bytes, ctype: str, csp: str = CSP,
                  cache: str = "no-store") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", csp)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, data: Any) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in app.hosts

        def _body(self) -> dict[str, Any]:
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise ApiError(415, "send JSON")
            origin = self.headers.get("Origin")
            if origin is not None and origin not in app.origins:
                raise ApiError(403, "cross-origin request refused")
            n = int(self.headers.get("Content-Length") or 0)
            if n > 64_000:
                raise ApiError(413, "request too large")
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                raise ApiError(400, "bad JSON")
            if not isinstance(data, dict):
                raise ApiError(400, "send a JSON object")
            return data

        def _upload(self) -> tuple[bytes, str]:
            """A resume, as the raw body. Stricter than _body: the Origin must
            be present, so no other site's page can send a file here."""
            if self.headers.get("Origin") not in app.origins:
                raise ApiError(403, "uploads are accepted only from this page")
            ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ctype not in UPLOAD_TYPES:
                raise ApiError(415, "upload a PDF, a Word file (.docx) or a plain text file")
            n = int(self.headers.get("Content-Length") or 0)
            if n > onboard.MAX_BYTES:
                raise ApiError(413, "that file is larger than 5 MB")
            if n <= 0:
                raise ApiError(400, "the file is empty")
            return self.rfile.read(n), f"resume{UPLOAD_TYPES[ctype]}"

        def _guard(self, fn: Callable[[], None]) -> None:
            if not self._host_ok():
                self._send(421, b"wrong host", "text/plain; charset=utf-8")
                return
            try:
                fn()
            except ApiError as e:
                self._json(e.status, {"error": e.message, **e.extra})
            except (outreach.OutreachError, pipeline.PipelineError) as e:
                self._json(409, {"error": str(e)})
            except onboard.SetupError as e:
                self._json(400, {"error": str(e)})
            except EditTimeout:
                self._json(409, {"error": "still saving another change - try again in a moment"})
            except Exception as e:  # noqa: BLE001 - a screen, not a crash
                self._json(500, {"error": f"{type(e).__name__}: {e}"})

        # ── GET ─────────────────────────────────────────────────────────────
        def do_GET(self) -> None:
            self._guard(self._get)

        def do_HEAD(self) -> None:
            self._guard(self._get)

        def _get(self) -> None:
            u = urlparse(self.path)
            p, q = u.path, parse_qs(u.query)
            prof = app.prof
            if p in ("/", "/index.html"):
                return self._static("index.html")
            if p.startswith("/static/"):
                return self._static(p[len("/static/"):])
            if p.startswith("/files/"):
                return self._file(p[len("/files/"):])
            routes: dict[str, Callable[[], Any]] = {
                "/api/overview": lambda: state.overview(prof),
                "/api/today": lambda: state.today(prof),
                "/api/postings": lambda: state.postings(prof),
                "/api/resumes": lambda: state.resumes(prof),
                "/api/outreach": lambda: state.outreach_view(prof),
                "/api/market": lambda: state.market(prof),
                "/api/system": lambda: state.system(prof),
                "/api/profile": lambda: state.profile(prof),
                "/api/applications": lambda: state.applications(prof),
                "/api/actions": lambda: {k: v[0] for k, v in ACTIONS.items()},
                "/api/setup": lambda: onboard.state(prof),
                "/api/job": lambda: app.runner.snapshot(int((q.get("since") or ["0"])[0])),
            }
            if p in routes:
                return self._json(200, routes[p]())
            m = re.fullmatch(r"/api/postings/([\w.-]+)", p)
            if m:
                data = state.posting(prof, m.group(1))
                if data is None:
                    raise ApiError(404, "no such posting")
                return self._json(200, data)
            m = re.fullmatch(r"/api/postings/([\w.-]+)/leads", p)
            if m:
                return self._json(200, outreach.find_leads(prof, outreach.find_posting(prof, m.group(1))))
            m = re.fullmatch(r"/api/applications/([\w.-]+)/sent", p)
            if m:
                from ..mail_ai import sent_for
                return self._json(200, sent_for(prof, m.group(1)))
            m = re.fullmatch(r"/api/postings/([\w.-]+)/kit", p)
            if m:
                from ..apply_kit import build_kit
                return self._json(200, build_kit(prof, m.group(1)))
            raise ApiError(404, "not found")

        def _static(self, rel: str) -> None:
            if rel == "base.css":
                # The dashboard's validated stylesheet, served as-is: one set
                # of colours, pills and chart styles for both surfaces.
                from ..dashboard import _CSS
                return self._send(200, _CSS.encode("utf-8"), "text/css; charset=utf-8",
                                  cache="no-cache")
            f = (STATIC / rel).resolve()
            if not f.is_relative_to(STATIC.resolve()) or not f.is_file():
                raise ApiError(404, "not found")
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, f.read_bytes(), ctype, cache="no-cache")

        def _file(self, rel: str) -> None:
            kind, _, name = rel.partition("/")
            if kind not in FILE_DIRS or not name or "/" in name or "\\" in name:
                raise ApiError(404, "not found")
            base = app.prof.var.joinpath(*FILE_DIRS[kind]).resolve()
            f = (base / name).resolve()
            if not f.is_relative_to(base) or not f.is_file():
                raise ApiError(404, "not found")
            ext = f.suffix.lower()
            ctype = {".pdf": "application/pdf", ".html": "text/html; charset=utf-8"}.get(
                ext, "text/plain; charset=utf-8")
            self._send(200, f.read_bytes(), ctype, csp=FILE_CSP)

        # ── POST ────────────────────────────────────────────────────────────
        def do_POST(self) -> None:
            self._guard(self._post)

        def _post(self) -> None:
            p = urlparse(self.path).path
            prof = app.prof
            if p == "/api/setup/upload":
                onboard.upload(prof, *self._upload())
                return self._json(200, onboard.state(prof) | {"will_send": onboard.to_send(prof)})
            body = self._body()

            # ── setup from a resume: each step returns the whole setup state ──
            if p.startswith("/api/setup/"):
                step = p[len("/api/setup/"):]
                if step == "draft":
                    onboard.draft(prof)
                elif step == "item":
                    onboard.update_item(prof, str(body.get("id") or ""), body.get("state"),
                                        body.get("fields") if isinstance(body.get("fields"), dict) else None)
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
                    return self._json(200, onboard.state(prof) | {"result": res})
                else:
                    raise ApiError(404, "not found")
                return self._json(200, onboard.state(prof))

            if p == "/api/run":
                name = str(body.get("action") or "")
                if name not in ACTIONS:
                    raise ApiError(400, f"unknown action {name!r}")
                who = holder(prof.shared)
                if who or app.runner.busy:
                    raise ApiError(409, "busy", holder=who or {"what": "the app"})
                label, fn = ACTIONS[name]
                job = app.runner.start(name, label, _under_lock(name, fn), body)
                if job is None:
                    raise ApiError(409, "busy", holder={"what": "the app"})
                return self._json(202, {"job": job.id})

            if p == "/api/stop":
                who = request_stop(prof.shared)
                if who is None:
                    raise ApiError(409, "nothing is running")
                return self._json(202, {"stopping": who})

            if p == "/api/schedule/pause":
                from ..cycle import pause
                hours = body.get("hours")
                if hours is not None and (isinstance(hours, bool) or not isinstance(hours, (int, float))
                                          or not 0 < hours <= 24 * 30):
                    raise ApiError(400, "hours must be between 0 and 720, or null for until resumed")
                return self._json(200, pause(prof.shared, hours))

            if p == "/api/schedule/resume":
                from ..cycle import resume
                resume(prof.shared)
                return self._json(200, {"paused": None})

            m = re.fullmatch(r"/api/postings/([\w.-]+)/status", p)
            if m:
                pid = m.group(1)
                if state.posting(prof, pid) is None:
                    raise ApiError(404, "no such posting")
                new = str(body.get("status") or "")
                variant = outreach.variant_for(prof, pid)
                entry = pipeline.set_status(prof, pid, new, str(body.get("note") or ""),
                                            variant=variant.id if variant else None)
                return self._json(200, entry)

            m = re.fullmatch(r"/api/postings/([\w.-]+)/(favorite|later)", p)
            if m:
                pid, what = m.groups()
                if state.posting(prof, pid) is None:
                    raise ApiError(404, "no such posting")
                if what == "favorite":
                    on = body.get("on")
                    if not isinstance(on, bool):
                        raise ApiError(400, "send {\"on\": true} or {\"on\": false}")
                    return self._json(200, pipeline.set_favorite(prof, pid, on))
                if body.get("clear") is True:
                    return self._json(200, pipeline.clear_later(prof, pid))
                if "until" in body and body["until"] is not None:
                    # The calendar. The page sends local midnight of the day
                    # picked, with its offset, so "back on Oct 3" means the
                    # morning of Oct 3 where the person is, not 05:30.
                    try:
                        until = datetime.fromisoformat(str(body["until"]).replace("Z", "+00:00"))
                    except ValueError:
                        raise ApiError(400, "until must be an ISO date-time")
                    return self._json(200, pipeline.save_for_later(prof, pid, until=until))
                return self._json(200, pipeline.save_for_later(prof, pid, body.get("days")))

            m = re.fullmatch(r"/api/mail-rules/([0-9a-f]{10})/(approve|reject)", p)
            if m:
                from .. import mail_learn
                try:
                    fn = mail_learn.approve if m.group(2) == "approve" else mail_learn.reject
                    return self._json(200, fn(prof, m.group(1)))
                except ValueError as exc:
                    raise ApiError(404, str(exc))

            m = re.fullmatch(r"/api/applications/([\w.-]+)/(confirm|status|ignore|merge|suggestion|undo-auto)", p)
            if m:
                from .. import applications as A
                app_id, what = m.groups()
                if app_id not in A.load(prof)["apps"]:
                    raise ApiError(404, "no such application")
                try:
                    if what == "confirm":
                        out = A.confirm(prof, app_id, str(body.get("company") or ""), str(body.get("role") or ""))
                    elif what == "status":
                        out = A.set_status(prof, app_id, str(body.get("status") or ""))
                    elif what == "ignore":
                        out = A.ignore(prof, app_id, bool(body.get("ignored", True)))
                    elif what == "suggestion":
                        out = A.answer_suggestion(prof, app_id, body.get("use") is True)
                    elif what == "undo-auto":
                        out = A.undo_auto(prof, app_id)
                    else:
                        into = str(body.get("into") or "")
                        if into not in A.load(prof)["apps"]:
                            raise ApiError(404, "no application to merge into")
                        out = A.merge(prof, app_id, into)
                except ValueError as exc:
                    raise ApiError(400, str(exc))
                return self._json(200, out)

            if p == "/api/outreach/draft":
                d = outreach.draft(prof, str(body.get("posting") or ""), str(body.get("to") or ""),
                                   str(body.get("how") or ""), str(body.get("context") or ""),
                                   use_model=not body.get("no_model"), force=bool(body.get("force")),
                                   employer=str(body.get("employer") or ""))
                return self._json(200, d.to_dict())

            m = re.fullmatch(r"/api/outreach/([\w.-]+)/mark", p)
            if m:
                return self._json(200, outreach.mark(prof, m.group(1), str(body.get("status") or ""),
                                                     str(body.get("note") or "")))

            if p == "/api/escalations/resolve":
                provider, slug = str(body.get("provider") or ""), str(body.get("slug") or "")
                note = str(body.get("note") or "").strip()
                if not (provider and slug and note):
                    raise ApiError(400, "provider, slug and a note are all required")
                resolve_escalation(prof.shared / "escalations.jsonl", provider, slug, note)
                return self._json(200, {"resolved": f"{provider}:{slug}"})

            raise ApiError(404, "not found")

    return Handler


def serve(prof: Profile, port: int = DEFAULT_PORT, open_browser: bool = True, page: str = "") -> None:
    app = App(prof, port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{port}/{page}"
    print(f"job search app for {prof.id} on {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
