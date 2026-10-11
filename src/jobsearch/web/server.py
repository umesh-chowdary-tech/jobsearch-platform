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

The routes themselves are in web/routes.py, shared with the hosted service;
this module is the HTTP around them. Every action is the same function the CLI
runs (web/actions.py), captured by web/runner.py.
"""
from __future__ import annotations

import json
import mimetypes
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .. import onboard
from ..profile import Profile
from . import routes
from .actions import ACTIONS  # noqa: F401 - the actions the app offers, for callers and tests
from .routes import ApiError
from .runner import Runner

STATIC = Path(__file__).parent / "static"
DEFAULT_PORT = 8765


# ── the handler ──────────────────────────────────────────────────────────────

class App:
    def __init__(self, prof: Profile, port: int) -> None:
        self.prof = prof
        self.port = port
        self.runner = Runner(prof)
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{h}" for h in self.hosts}


# Under the profile's var/<id>/.
FILE_DIRS = {"resumes": ("resumes",), "manual": ("manual", "resumes")}
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
                raise ApiError(400, "bad JSON") from None
            if not isinstance(data, dict):
                raise ApiError(400, "send a JSON object")
            return data

        def _upload(self) -> dict[str, Any]:
            """A resume, as the raw body. Stricter than _body: the Origin must
            be present, so no other site's page can send a file here."""
            if self.headers.get("Origin") not in app.origins:
                raise ApiError(403, "uploads are accepted only from this page")
            n = int(self.headers.get("Content-Length") or 0)
            if n > onboard.MAX_BYTES:
                raise ApiError(413, "that file is larger than 5 MB")
            return routes.upload(app.prof, self.headers.get("Content-Type", ""), self.rfile.read(n) if n > 0 else b"")

        def _guard(self, fn: Callable[[], None]) -> None:
            if not self._host_ok():
                self._send(421, b"wrong host", "text/plain; charset=utf-8")
                return
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - a screen, not a crash
                self._json(*routes.as_error(e))

        # ── GET ─────────────────────────────────────────────────────────────
        def do_GET(self) -> None:
            self._guard(self._get)

        def do_HEAD(self) -> None:
            self._guard(self._get)

        def _get(self) -> None:
            u = urlparse(self.path)
            p, q = u.path, parse_qs(u.query)
            if p in ("/", "/index.html"):
                return self._static("index.html")
            if p.startswith("/static/"):
                return self._static(p[len("/static/"):])
            if p.startswith("/files/"):
                return self._file(p[len("/files/"):])
            if p.startswith("/api/"):
                return self._json(200, routes.get(app.prof, p, q, app.runner))
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
            if p == "/api/setup/upload":
                return self._json(200, self._upload())
            self._json(*routes.post(app.prof, p, self._body(), app.runner))

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
