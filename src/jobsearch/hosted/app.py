"""The hosted service: the local app's API behind sign-in, one profile per
account, plus the web app's files (docs/hosted.md).

Every /api request but /api/health, /api/config and - in dev mode only -
/api/dev/sign-in carries a sign-in token (hosted/auth.py). A person who hasn't
joined with an invite reaches only /api/me and /api/invite. Everything else
goes to web/routes.py with the person's own profile and the hosted scope, so
the service answers exactly what the local app does, for that one person.

No cookies: the token travels in the Authorization header, so another site's
page can't make a signed-in request on someone's behalf, and the page may talk
only to this origin and the Supabase project (the CSP below).
"""
from __future__ import annotations

import hmac
import io
import json
import logging
import os
import shutil
import threading
import zipfile
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.routing import Route

from .. import onboard, statestore
from ..cycle import caught_up
from ..models import utcnow
from ..profile import Profile, shared_var, var_dir
from ..web import routes
from ..web.routes import HOSTED, ApiError
from ..web.runner import Runner
from . import accounts, admin, mcp
from .auth import AuthError, Verifier, Who
from .mirror import Mirror

ROOT = Path(__file__).resolve().parents[3]
WEB_DIST = ROOT / "web" / "dist"
JSON_MAX = 64_000
FILE_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'"
RESUME_TYPES = {".pdf": "application/pdf", ".html": "text/html; charset=utf-8", ".txt": "text/plain; charset=utf-8"}
DELETE_WORDS = "delete my account"
log = logging.getLogger("jobsearch.hosted")


def plausible_email(email: str) -> bool:
    """Shaped like an address, checked without a regex that could backtrack
    on a long input. Dev sign-in only; Supabase checks real ones."""
    local, at, domain = email.partition("@")
    return (bool(at) and 0 < len(local) <= 64 and 3 < len(domain) <= 190 and "@" not in domain
            and "." in domain.strip(".") and not any(c.isspace() for c in email))


def _plain(name: str, limit: int) -> str:
    """A setting shown on the pages, as plain words: the privacy notice is
    Markdown, and a setting mustn't add a link or bold to it."""
    text = " ".join(os.environ.get(name, "").split())
    return "".join(c for c in text if c not in "*[]()|<>`_\\#")[:limit].strip()


# The privacy notice's blanks, each filled from a setting of the same meaning.
OPERATOR_SETTINGS = {"name": "JOBSEARCH_OPERATOR", "city": "JOBSEARCH_CITY", "email": "JOBSEARCH_PRIVACY_EMAIL"}


def operator() -> dict[str, str]:
    """Who runs this copy of the service, where, and the address people write
    to about their data - from its settings, never from this repository, which
    is public and anyone's to deploy. A blank one stays [in brackets] on the
    page, and the admin page says which."""
    email = os.environ.get(OPERATOR_SETTINGS["email"], "").strip()
    if not plausible_email(email) or any(c in email for c in "*[]()|<>`\\"):
        email = ""
    return {"name": _plain(OPERATOR_SETTINGS["name"], 80), "city": _plain(OPERATOR_SETTINGS["city"], 60),
            "email": email}


def _csp(supabase_url: str) -> str:
    origin = ""
    if supabase_url:
        u = urlparse(supabase_url)
        origin = f" {u.scheme}://{u.netloc}"
    return ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            f"connect-src 'self'{origin}; frame-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; "
            "form-action 'self'; object-src 'none'")


def _json_response(status: int, data: Any) -> Response:
    return Response(json.dumps(data, ensure_ascii=False, default=str), status_code=status,
                    media_type="application/json")


def _respond(fn: Callable[[], Any]) -> Response:
    """Run a route; whatever it raises becomes the same error the local app
    would show, minus anything about the server's insides."""
    try:
        out = fn()
    except Exception as exc:  # noqa: BLE001 - every failure is an answer, never a stack trace
        return _json_response(*routes.as_error(exc, HOSTED))
    if isinstance(out, Response):
        return out
    status, data = out if isinstance(out, tuple) else (200, out)
    return _json_response(status, data)


async def _read(request: Request, cap: int) -> bytes:
    """The body, refused once it passes `cap` - before it is all in memory,
    whatever Content-Length claimed."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise ApiError(413, "that is too large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            raise ApiError(413, "that is too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _json_body(request: Request) -> dict[str, Any]:
    if "application/json" not in request.headers.get("content-type", ""):
        raise ApiError(415, "send JSON")
    raw = await _read(request, JSON_MAX)
    try:
        data = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise ApiError(400, "bad JSON") from None
    if not isinstance(data, dict):
        raise ApiError(400, "send a JSON object")
    return data


class Service:
    def __init__(self, root: Path, verifier: Verifier, cron_secret: str = "",
                 mirror: Mirror | None = None) -> None:
        self.root = root
        self.verifier = verifier
        self.cron_secret = cron_secret
        self.mirror = mirror
        self.shared = shared_var(root)
        self._runners: dict[str, Runner] = {}
        self._lock = threading.Lock()
        self.cycle: threading.Thread | None = None

    def runner(self, prof: Profile) -> Runner:
        with self._lock:
            return self._runners.setdefault(prof.id, Runner(prof))

    def who(self, request: Request) -> Who:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise ApiError(401, "sign in first")
        try:
            return self.verifier.verify(token.strip())
        except AuthError as exc:
            raise ApiError(401, str(exc)) from exc

    def member(self, request: Request, consented: bool = True) -> tuple[Who, Profile]:
        """The signed-in person and their profile. Nothing is worked on for
        someone who hasn't agreed to the current notice; downloading and
        deleting their data (`consented=False`) stay open to them regardless."""
        who = self.who(request)
        row = accounts.user(self.shared, who.sub)
        if row is None:
            raise ApiError(403, "join with an invite code first", join=True)
        self._seen(who, row)
        if consented and row.get("suspended"):
            raise ApiError(403, "This account is paused. You can still download or delete your data in Settings.",
                           suspended=True)
        if consented and not accounts.consent_current(row):
            raise ApiError(403, "agree to the updated privacy notice and terms to continue", consent=True)
        return who, Profile(self.root, row["profile"])

    def _seen(self, who: Who, row: dict[str, Any]) -> None:
        if row.get("last_seen") != utcnow()[:10]:
            accounts.seen(self.shared, who.sub)

    def admin(self, request: Request) -> Who:
        who = self.who(request)
        if not who.email or who.email not in accounts.admins():
            raise ApiError(403, "admins only")
        return who

    def me(self, who: Who) -> dict[str, Any]:
        row = accounts.user(self.shared, who.sub)
        prof = Profile(self.root, row["profile"]) if row else None
        if row:
            self._seen(who, row)
        return {"email": who.email, "member": row is not None, "suspended": bool((row or {}).get("suspended")),
                "profile_ready": bool(prof and prof.exists()), "admin": bool(who.email and who.email in accounts.admins()),
                "consent": (row or {}).get("consent"), "consent_current": accounts.consent_current(row)}


    def delete_account(self, sub: str, prof: Profile) -> None:
        """Everything kept for a person, and their account. The database's
        copy first: if that fails, nothing is gone here either, and deleting
        again later finishes the job."""
        if self.mirror:
            var = var_dir(self.root).resolve()
            prefixes = [d.resolve().relative_to(var).as_posix() for d in (prof.var, prof.dir)
                        if d.resolve().is_relative_to(var)]
            try:
                self.mirror.forget(prefixes)
            except Exception:  # noqa: BLE001
                log.exception("forgetting %s in the database failed", prof.id)
                raise ApiError(503, "Couldn't delete that account right now - try again in a few minutes.") from None
        for d in (prof.dir, prof.var):
            shutil.rmtree(d, ignore_errors=True)
        accounts.remove(self.shared, sub)
        with self._lock:
            self._runners.pop(prof.id, None)
        log.info("deleted account %s", prof.id)

    def start_boards_check(self) -> tuple[int, dict[str, Any]]:
        """Check every board in data/ats-registry.yaml, in the background, and
        keep the ones that answer for the job updates to watch. A new copy of
        the service knows no boards until this has run once."""
        with self._lock:
            if self.cycle and self.cycle.is_alive():
                return 409, {"error": "a job update or a board check is already running"}

            def run() -> None:
                from .. import discovery
                from ..busy import busy
                from ..http import default_fetcher
                from ..registry import known_boards, load_registry, write_run
                try:
                    with busy(self.shared, "checking job boards") as got:
                        if got:
                            reg = load_registry(self.root / "data" / "ats-registry.yaml")
                            found = discovery.verify_known(default_fetcher(self.shared), known_boards(reg))
                            write_run(self.shared, found.probes, found.escalations, found.summary())
                except Exception:  # noqa: BLE001 - logged; the admin page shows it
                    log.exception("checking job boards failed")
                if self.mirror:
                    self.mirror.touch()
            self.cycle = threading.Thread(target=run, name="boards", daemon=True)
            self.cycle.start()
        return 202, {"started": True}

    def start_company_search(self, rids: list[str]) -> tuple[int, dict[str, Any]]:
        """Look for the job boards of companies people asked for, in the
        background: discovery's few polite requests each. A board that answers
        is watched from the next job update."""
        from .. import company_requests as cr
        with self._lock:
            if self.cycle and self.cycle.is_alive():
                return 409, {"error": "a job update or a board check is already running"}
            reqs = cr.load(self.shared)
            rids = [r for r in rids if r in reqs]
            if not rids:
                return 400, {"error": "no request to look for"}
            cr.mark_checking(self.shared, rids)

            def run() -> None:
                from .. import discovery
                from ..busy import busy
                from ..http import default_fetcher
                from ..registry import write_run
                try:
                    with busy(self.shared, "looking for companies' job boards") as got:
                        if got:
                            fetcher = default_fetcher(self.shared)
                            for rid in rids:
                                found = discovery.search_company(fetcher, reqs[rid]["company"])
                                write_run(self.shared, found.probes, found.escalations, found.summary())
                                cr.record_search(self.shared, rid, found)
                except Exception:  # noqa: BLE001 - logged; the admin page shows it
                    log.exception("looking for job boards failed")
                finally:
                    # Whatever wasn't reached goes back in the queue.
                    cr.release(self.shared, rids, "not looked at yet - try again")
                if self.mirror:
                    self.mirror.touch()
            self.cycle = threading.Thread(target=run, name="company-search", daemon=True)
            self.cycle.start()
        return 202, {"started": True}

    def start_cycle(self, forced: bool = False) -> tuple[int, dict[str, Any]]:
        """The scheduled cycle, in the background. The cron job's tick skips
        while the schedule is paused; `forced` - "Run now" on the admin page -
        is someone asking, so it runs anyway."""
        with self._lock:
            if self.cycle and self.cycle.is_alive():
                return 409, {"error": "a job update is already running"}

            def run() -> None:
                from ..busy import busy
                from ..cycle import run_cycle
                try:
                    if forced:
                        with busy(self.shared, "job update started on the admin page") as got:
                            if got:
                                run_cycle(self.root, quiet=True, lock=False)
                    else:
                        run_cycle(self.root, quiet=True, lock=True)
                except Exception:  # noqa: BLE001 - logged; the next tick tries again
                    log.exception("cycle failed")
                if self.mirror:
                    self.mirror.touch()
            self.cycle = threading.Thread(target=run, name="cycle", daemon=True)
            self.cycle.start()
        return 202, {"started": True}


def _export(prof: Profile) -> Response:
    """Everything kept for this person, as a zip: their profile and what the
    agent worked out for them."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for label, base in (("profile", prof.dir), ("data", prof.var)):
            if base.is_dir():
                for f in sorted(base.rglob("*")):
                    if f.is_file() and not f.name.startswith(".edit"):
                        z.write(f, f"{label}/{f.relative_to(base).as_posix()}")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="jobsearch-export.zip"'})


def _admin_overview(svc: Service) -> dict[str, Any]:
    op = operator()
    return admin.overview(svc, [env for key, env in OPERATOR_SETTINGS.items() if not op[key]])


def create_app(root: Path | None = None, verifier: Verifier | None = None, static_dir: Path | None = None,
               cron_secret: str = "", remote: statestore.Remote | None = None, mirror_delay: float = 3.0) -> FastAPI:
    """`remote`: where var/ is kept between restarts (Postgres in production,
    hosted/mirror.py); without one, var/ is only on this disk."""
    root = Path(root or ROOT)
    if verifier is None:
        verifier = Verifier(os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_JWT_SECRET", ""))
    mirror = Mirror(var_dir(root), remote, delay=mirror_delay) if remote is not None else None
    svc = Service(root, verifier, cron_secret, mirror)
    admin.watch_errors()
    supabase_url = "" if verifier.dev else os.environ.get("SUPABASE_URL", "")
    csp = _csp(supabase_url)
    connector = mcp.build(svc)                  # SwitchKaro inside Claude (hosted/mcp.py)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if mirror:
            await run_in_threadpool(mirror.start)
        async with (connector[0].session_manager.run() if connector else nullcontext()):
            yield
        if mirror:
            await run_in_threadpool(mirror.stop)

    app = FastAPI(title="jobsearch", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.service = svc

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("Content-Security-Policy", csp)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        if request.headers.get("x-forwarded-proto", request.url.scheme) == "https":
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith(("/api/", "/files/")):
            resp.headers["Cache-Control"] = "no-store"
        if mirror and request.method == "POST" and resp.status_code < 400:
            mirror.touch()
        return resp

    if connector:
        # Its own sign-in check: a 401 there names the metadata to sign in by.
        for path in (mcp.PATH, "/.well-known/oauth-protected-resource" + mcp.PATH):
            app.router.routes.insert(0, Route(path, endpoint=connector[1]))

        @app.get("/.well-known/oauth-protected-resource")
        def resource_metadata() -> dict[str, Any]:
            return mcp.metadata(svc)

    # ── open to anyone ──────────────────────────────────────────────────────
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        # The anon key is public by design: it lets the page start a sign-in,
        # nothing more. Every request here is checked by its token.
        return {"auth": "dev" if verifier.dev else "supabase", "supabase_url": supabase_url,
                "consent_version": accounts.CONSENT_VERSION,
                "name": _plain("JOBSEARCH_APP_NAME", 40), "operator": operator(),
                "supabase_anon_key": "" if verifier.dev else os.environ.get("SUPABASE_ANON_KEY", "")}

    if verifier.dev:
        @app.post("/api/dev/sign-in")
        async def dev_sign_in(request: Request) -> Response:
            try:
                body = await _json_body(request)
            except ApiError as exc:
                return _json_response(*routes.as_error(exc, HOSTED))
            email = str(body.get("email") or "").strip().lower()
            if not plausible_email(email):
                return _json_response(400, {"error": "enter an email address"})
            return _json_response(200, {"token": verifier.mint_dev(email)})

    # ── signed in ───────────────────────────────────────────────────────────
    @app.get("/api/me")
    async def me(request: Request) -> Response:
        return await run_in_threadpool(_respond, lambda: svc.me(svc.who(request)))

    @app.post("/api/invite")
    async def invite(request: Request) -> Response:
        try:
            body = await _json_body(request)
        except ApiError as exc:
            return _json_response(*routes.as_error(exc, HOSTED))

        def go() -> Any:
            who = svc.who(request)
            try:
                accounts.join(svc.shared, who.sub, who.email, str(body.get("code") or ""),
                              str(body.get("consent") or ""),
                              admin=bool(who.email and who.email in accounts.admins()))
            except accounts.AccountError as exc:
                raise ApiError(400, str(exc)) from exc
            return svc.me(who)
        return await run_in_threadpool(_respond, go)

    @app.post("/api/consent")
    async def consent(request: Request) -> Response:
        try:
            body = await _json_body(request)
        except ApiError as exc:
            return _json_response(*routes.as_error(exc, HOSTED))

        def go() -> Any:
            who = svc.who(request)
            try:
                accounts.agree(svc.shared, who.sub, str(body.get("consent") or ""))
            except accounts.AccountError as exc:
                raise ApiError(400, str(exc)) from exc
            return svc.me(who)
        return await run_in_threadpool(_respond, go)

    @app.post("/api/setup/upload")
    async def upload(request: Request) -> Response:
        try:
            data = await _read(request, onboard.MAX_BYTES)
        except ApiError as exc:
            return _json_response(exc.status, {"error": "that file is larger than 5 MB"})

        def go() -> Any:
            _, prof = svc.member(request)
            return routes.upload(prof, request.headers.get("content-type", ""), data,
                                 request.headers.get("x-filename", ""))
        return await run_in_threadpool(_respond, go)

    @app.get("/files/resumes/{name}")
    async def resume_file(name: str, request: Request) -> Response:
        def go() -> Any:
            _, prof = svc.member(request)
            f = routes.pick_file(prof.var / "resumes", name)
            if f is None:
                raise ApiError(404, "not found")
            return Response(f.read_bytes(), media_type=RESUME_TYPES.get(f.suffix.lower(), "text/plain; charset=utf-8"),
                            headers={"Content-Security-Policy": FILE_CSP,
                                     "Content-Disposition": f'inline; filename="{f.name}"'})
        return await run_in_threadpool(_respond, go)

    @app.get("/api/account/export")
    async def export(request: Request) -> Response:
        return await run_in_threadpool(_respond, lambda: _export(svc.member(request, consented=False)[1]))

    @app.post("/api/account/delete")
    async def delete(request: Request) -> Response:
        try:
            body = await _json_body(request)
        except ApiError as exc:
            return _json_response(*routes.as_error(exc, HOSTED))

        def go() -> Any:
            who, prof = svc.member(request, consented=False)
            if str(body.get("confirm") or "").strip().lower() != DELETE_WORDS:
                raise ApiError(400, f'type "{DELETE_WORDS}" to confirm')
            svc.delete_account(who.sub, prof)
            return {"deleted": True}
        return await run_in_threadpool(_respond, go)

    # ── admins ──────────────────────────────────────────────────────────────
    @app.get("/api/admin/overview")
    async def admin_overview(request: Request) -> Response:
        def go() -> Any:
            svc.admin(request)
            return _admin_overview(svc)
        return await run_in_threadpool(_respond, go)

    @app.post("/api/admin/do")
    async def admin_do(request: Request) -> Response:
        try:
            body = await _json_body(request)
        except ApiError as exc:
            return _json_response(*routes.as_error(exc, HOSTED))

        def go() -> Any:
            who = svc.admin(request)
            try:
                done = admin.act(svc, who.email, body)
            except (admin.AdminError, accounts.AccountError) as exc:
                raise ApiError(400, str(exc)) from exc
            return {**done, "overview": _admin_overview(svc)}
        return await run_in_threadpool(_respond, go)

    @app.post("/api/admin/invites")
    async def admin_invites(request: Request) -> Response:
        try:
            body = await _json_body(request)
        except ApiError as exc:
            return _json_response(*routes.as_error(exc, HOSTED))

        def go() -> Any:
            svc.admin(request)
            n = body.get("n", 1)
            if isinstance(n, bool) or not isinstance(n, int):
                raise ApiError(400, "n must be a number")
            try:
                return {"codes": accounts.create_invites(svc.shared, n, str(body.get("note") or ""))}
            except accounts.AccountError as exc:
                raise ApiError(400, str(exc)) from exc
        return await run_in_threadpool(_respond, go)

    # ── the scheduled cycle, started by a cron job that knows the secret ────
    @app.post("/api/cron/cycle")
    def cron_cycle(request: Request) -> Response:
        given = request.headers.get("x-cron-secret", "")
        if not svc.cron_secret or not hmac.compare_digest(given.encode(), svc.cron_secret.encode()):
            return _json_response(404, {"error": "not found"})
        return _json_response(*svc.start_cycle())

    # ── everything else: the shared routes, for this person ─────────────────
    @app.api_route("/api/{rest:path}", methods=["GET", "POST"])
    async def api(rest: str, request: Request) -> Response:
        path = "/api/" + rest
        body: dict[str, Any] = {}
        if request.method == "POST":
            try:
                body = await _json_body(request)
            except ApiError as exc:
                return _json_response(*routes.as_error(exc, HOSTED))

        def go() -> Any:
            _, prof = svc.member(request)
            if request.method == "GET":
                return routes.get(prof, path, parse_qs(request.url.query), svc.runner(prof), scope=HOSTED)
            out = routes.post(prof, path, body, svc.runner(prof), scope=HOSTED)
            if path == "/api/setup/save" and not caught_up(prof):
                # A new profile reads what's already open on the boards now,
                # not at the next scheduled update (cycle.py, catching up).
                svc.start_cycle()
            return out
        return await run_in_threadpool(_respond, go)

    static = static_dir if static_dir is not None else WEB_DIST
    if static.is_dir():
        @app.get("/oauth/consent")
        def consent_page() -> Response:
            # Where Supabase sends a person to approve an app (the Claude
            # connector); the web app shows it (pages/Consent.tsx).
            return FileResponse(static / "index.html", headers={"Cache-Control": "no-store"})

        app.mount("/", StaticFiles(directory=static, html=True), name="web")
    else:
        @app.get("/")
        def not_built() -> Response:
            return Response("The web app isn't built yet: cd web && npm ci && npm run build",
                            media_type="text/plain")
    return app


def serve(host: str = "127.0.0.1", port: int = 8000, dev: bool = False) -> None:
    """`jobsearch hosted` - run the service. Profiles live inside var/ there
    (var/_profiles/), so mirroring var/ keeps everything."""
    import uvicorn
    os.environ.setdefault("JOBSEARCH_PROFILES", str(var_dir(ROOT) / "_profiles"))
    if dev:
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise SystemExit("dev sign-in works only on this machine (--host 127.0.0.1)")
        verifier = Verifier(dev=True)
        print("DEV SIGN-IN: anyone who can reach this port can sign in as anyone. This machine only.", flush=True)
    else:
        verifier = Verifier(os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_JWT_SECRET", ""))
    remote = statestore.remote_from_env()
    if remote is None and not dev:
        print("DATABASE_URL is not set: var/ lives only on this disk and is lost when the service restarts.",
              flush=True)
    app = create_app(ROOT, verifier, cron_secret=os.environ.get("CRON_SECRET", ""), remote=remote)
    uvicorn.run(app, host=host, port=port, proxy_headers=not dev,
                forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))
