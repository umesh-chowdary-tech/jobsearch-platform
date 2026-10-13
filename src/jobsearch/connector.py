"""The agent as an MCP connector, so a normal Claude chat can drive it.

    python -m jobsearch.cli --profile <id> connector   # serves /<secret>/mcp on $PORT

Here one connector serves one profile: the secret URL is the only lock, so it
is one person's. The hosted service serves the same tools to every member,
each behind their own sign-in (OAuth; hosted/mcp.py and docs/connector.md).

Claude in the chat is the ranker here. The server finds, filters and
remembers; it hands Claude the shortlist with the same profile and rules the
Groq pass reads, and stores what Claude judged with `by` naming Claude. The
store's rules hold the same way they do in rank.py: Claude never writes a
claim, only a fit and one sentence, and the years cap is applied here, not
trusted to the model.

Every tool is a method on `Tools`, which knows nothing about MCP - the tests
call it directly. `build_app` wraps it for the wire.

Auth: claude.ai's custom connectors take OAuth or nothing, not a static
header. So the endpoint lives under a long random path segment
(CONNECTOR_SECRET) and everything else is a 404. Anyone with the URL can use
it, so the URL is the password: keep it out of chats and screenshots.
"""
from __future__ import annotations

import functools
import hmac
import os
import threading
import traceback
from contextlib import nullcontext
from typing import Any, Callable, Literal

import yaml
from pydantic import BaseModel, Field

from . import outreach, pipeline, rank, statestore
from .busy import editing, holder
from .models import utcnow
from .profile import Profile, var_dir
from .web import state

DESC_CHARS = 6000          # a posting's text, as much as a chat can usefully read
JUDGED_BY = "Claude (in chat) via claude"   # judged_by() -> "claude", the most trusted


class Judgement(BaseModel):
    id: str = Field(description="The posting id exactly as given by jobs_to_rank")
    fit: Literal["strong", "possible", "weak"]
    why: str = Field(description="One sentence, under 25 words: specific work in THIS posting "
                                 "against specific work in the profile")


class Tools:
    def __init__(self, prof: Profile, remote: statestore.Remote | None = None) -> None:
        self.prof = prof
        self.var = prof.var
        self.var.mkdir(parents=True, exist_ok=True)
        # The database mirrors the whole of var/: this profile's and the shared
        # board state.
        self.mirror = var_dir(prof.root)
        self.remote = remote
        self._refresh: dict[str, Any] = {"running": False}
        self._sync_lock = threading.Lock()

    # ── sync ─────────────────────────────────────────────────────────────────

    def pull(self) -> None:
        if self.remote is None:
            return
        with self._sync_lock:
            statestore.pull(self.mirror, self.remote)

    def push(self) -> None:
        if self.remote is None:
            return
        with self._sync_lock:
            statestore.push(self.mirror, self.remote)

    # ── reading ──────────────────────────────────────────────────────────────

    def _blob(self) -> dict[str, dict[str, Any]]:
        return state._postings(self.prof)

    def find_jobs(self, status: str = "new", fit: list[str] | None = None,
                  search: str = "", limit: int = 25) -> dict[str, Any]:
        self.pull()
        blob = self._blob()
        rows = [r for r in state.postings(self.prof)
                if r["verdict"] == "keep" and not r["closed"]]
        if status != "any":
            rows = [r for r in rows if r["status"] == status]
        if status == "new":
            # Saved for later stays off until its date, as on the Today screen.
            rows = [r for r in rows if (r["later"] or {}).get("state") in (None, "due")]
        if fit:
            want = set(fit)
            rows = [r for r in rows if (r["fit"] or "unranked") in want]
        if search:
            s = search.lower()
            rows = [r for r in rows if s in r["title"].lower() or s in r["company"].lower()
                    or s in r["location"].lower()]
        out = []
        for r in rows[:max(1, min(limit, 100))]:
            ai = (blob.get(r["id"]) or {}).get("ai") or {}
            out.append({k: r[k] for k in ("id", "company", "title", "location", "url",
                                          "coverage", "status", "favorite")}
                       | {"fit": r["fit"] or "unranked", "why": ai.get("why") or "",
                          "judged_by": ai.get("by") or ""})
        return {"total": len(rows), "shown": len(out), "jobs": out,
                "note": "fit 'unranked' means no model has read it yet - use jobs_to_rank. "
                        "Coverage is backed/asked skills, never a percentage."}

    def get_job(self, posting_id: str) -> dict[str, Any]:
        self.pull()
        p = outreach.find_posting(self.prof, posting_id)
        row = state.posting(self.prof, str(p.get("id"))) or {}
        desc = row.get("description") or ""
        row["description"] = desc[:DESC_CHARS] + (" [...]" if len(desc) > DESC_CHARS else "")
        row.pop("files", None)
        return row

    def today(self) -> dict[str, Any]:
        self.pull()
        t = state.today(self.prof)
        brief = lambda rows: [{k: r[k] for k in ("id", "company", "title", "fit", "status")}  # noqa: E731
                              for r in rows[:20]]
        return {
            "decide_count": len(t["decide"]),
            "decide": brief(t["decide"]),
            "back_from_later": brief(t["back_from_later"]),
            "in_progress": brief(t["in_progress"]),
            "saved_for_later": t["saved_for_later"],
            "unranked": t["unread"],
            "stages": t["stages"],
            "referrals_due": t["outreach_due"],
            "open_escalations": len(t["escalations"]),
            "last_refresh": self.refresh_status()["last_cycle"],
        }

    def resume_store(self) -> dict[str, Any]:
        """The profile as the ranker reads it, plus what Skills may claim."""
        self.pull()
        shown = state.profile(self.prof)
        store = self._store()
        level, _, _ = rank.candidate_level(self.prof, store)
        return {"identity": shown["identity"], "level": level,
                "profile": rank.profile_digest(store),
                "skills_by_tier": {t: [c["skill"] for c in cs] for t, cs in shown["tiers"].items()},
                "rules": "Never invent a number. Never claim a skill not listed here. "
                         "Coverage is a count like 8/11, never a percentage."}

    def application_kit(self, posting_id: str) -> dict[str, Any]:
        from .apply_kit import build_kit
        self.pull()
        return build_kit(self.prof, posting_id)

    # ── ranking: Claude in the chat reads, the server records ───────────────

    def jobs_to_rank(self, limit: int = 8) -> dict[str, Any]:
        self.pull()
        blob = {"postings": self._blob()}
        todo = rank.unread(blob, lead="claude")[:max(1, min(limit, 12))]
        store = self._store()
        level, _, _ = rank.candidate_level(self.prof, store)
        return {
            "left_after_these": max(0, len(rank.unread(blob, lead="claude")) - len(todo)),
            "rules": rank.SYSTEM,
            "level": level,
            "profile": rank.profile_digest(store),
            "postings": [{"id": str(p.get("id")), "text": rank._posting_block(p)} for p in todo],
            "next": "Judge every posting above against the profile, then call save_rankings once.",
        }

    def _store(self) -> dict[str, Any]:
        return yaml.safe_load(self.prof.file("master-store.yaml").read_text(encoding="utf-8")) or {}

    def save_rankings(self, judgements: list[Judgement]) -> dict[str, Any]:
        self.pull()
        store = self._store()
        _, years, cap = rank.candidate_level(self.prof, store)
        path = self.var / "triage-results.json"
        saved, unknown, capped = [], [], []
        with editing(self.var):
            blob = state._read(path, {}) or {}
            postings = blob.get("postings") or {}
            for j in judgements:
                p = postings.get(j.id)
                if p is None:
                    unknown.append(j.id)
                    continue
                fit, why = j.fit, " ".join(j.why.split())
                # Same rule as rank.py: the years cap is enforced, not asked for.
                asked = rank.asked_years(p)
                if asked and asked > cap and rank.FIT_ORDER[fit] > rank.FIT_ORDER["weak"]:
                    fit, why = "weak", f"Asks for {asked}+ years against {years}. {why}"
                    capped.append(j.id)
                p["ai"] = {"fit": fit, "why": why[:220], "by": JUDGED_BY,
                           "prompt": rank.PROMPT_VERSION, "at": utcnow()}
                p.pop("ai_tries", None)
                saved.append(j.id)
            if saved:
                rank._write(path, blob)
        self.push()
        return {"saved": len(saved), "unknown_ids": unknown, "capped_to_weak_for_years": capped}

    # ── decisions ────────────────────────────────────────────────────────────

    def _pid(self, posting_id: str) -> str:
        return str(outreach.find_posting(self.prof, posting_id).get("id"))

    def set_status(self, posting_id: str, status: str, note: str = "") -> dict[str, Any]:
        self.pull()
        pid = self._pid(posting_id)
        entry = pipeline.set_status(self.prof, pid, status, note)
        self.push()
        return {"id": pid, "status": entry["status"]}

    def save_for_later(self, posting_id: str, days: int | None = 7) -> dict[str, Any]:
        self.pull()
        pid = self._pid(posting_id)
        entry = pipeline.save_for_later(self.prof, pid, days=days)
        self.push()
        return {"id": pid, "later": entry.get("later")}

    def set_favorite(self, posting_id: str, on: bool = True) -> dict[str, Any]:
        self.pull()
        pid = self._pid(posting_id)
        pipeline.set_favorite(self.prof, pid, on)
        self.push()
        return {"id": pid, "favorite": on}

    # ── refresh: the scheduled cycle, on demand ──────────────────────────────

    def refresh_jobs(self) -> dict[str, Any]:
        """Start a cycle in the background: a poll of ~34 boards takes minutes,
        longer than a chat waits for one tool call."""
        if self._refresh["running"] or holder(self.prof.shared):
            return {"started": False, "why": "a refresh is already running"} | self.refresh_status()
        self._refresh = {"running": True, "started": utcnow()}
        threading.Thread(target=self._run_refresh, daemon=True, name="refresh").start()
        return {"started": True, "check_with": "refresh_status, in a minute or two"}

    def _run_refresh(self) -> None:
        from .cycle import run_cycle
        try:
            with (self.remote.cycle_lock() if self.remote else nullcontext(True)) as got:
                if not got:
                    self._refresh.update(ok=False, error="the PC's scheduled cycle is running - "
                                                         "its results arrive when it finishes")
                    return
                self.pull()
                code = run_cycle(self.prof.root, quiet=True, profiles=[self.prof])
                self._refresh.update(ok=code == 0)
        except Exception as exc:  # noqa: BLE001 - reported through refresh_status
            self._refresh.update(ok=False, error=f"{type(exc).__name__}: {exc}",
                                 trace=traceback.format_exc()[-1500:])
        finally:
            try:
                self.push()
            except Exception as exc:  # noqa: BLE001
                self._refresh.update(ok=False, error=f"saving failed: {exc}")
            self._refresh.update(running=False, finished=utcnow())

    def refresh_status(self) -> dict[str, Any]:
        log = state.cycle_log(self.prof, 1)
        r = {k: v for k, v in self._refresh.items() if k != "trace"}
        return {"refresh": r, "busy": holder(self.prof.shared),
                "last_cycle": log[0] if log else None}


# ── the wire ─────────────────────────────────────────────────────────────────

INSTRUCTIONS = """A job-search agent for one person. It watches company job boards,
filters postings against their profile, and remembers what they decided.

Typical flow: refresh_jobs (then refresh_status) to poll the boards; find_jobs to
list what is worth deciding; jobs_to_rank + save_rankings when postings are
'unranked'; get_job for the full text; set_status / save_for_later / set_favorite
to record their decisions.

Rules that always hold: never invent a number; never claim a skill the
resume store does not back; coverage is a count like 8/11, never a percentage.
Never apply, message anyone, or send anything on their behalf - only record
what they say they did."""

HOSTED_INSTRUCTIONS = """SwitchKaro, a job-search service, for the person signed in. It watches
company job boards a few times a day, filters postings against their
confirmed profile, and remembers what they decided.

Typical flow: today for what needs them; find_jobs to list what is worth
deciding; jobs_to_rank + save_rankings when postings are 'unranked'; get_job
for the full text; application_kit for a form; set_status / save_for_later /
set_favorite to record their decisions.

Rules that always hold: never invent a number; never claim a skill the
resume store does not back; coverage is a count like 8/11, never a
percentage. Never apply, message anyone, or send anything on their behalf -
only record what they say they did."""

# Polling every board for one person: on the hosted service the schedule does it.
LOCAL_ONLY = {"refresh_jobs", "refresh_status"}


def build_server(tools: Tools | Callable[[], Tools], hosted: bool = False, **server_kw: Any) -> Any:
    """`tools`: one profile's, or - on the hosted service - a function that
    gives the signed-in person's, asked for on every call. `server_kw` goes to
    the MCP server (its sign-in settings, there)."""
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    from mcp.server.mcpserver.exceptions import ToolError
    from .busy import EditTimeout
    from .outreach import OutreachError

    def explained(fn: Any) -> Any:
        """A wrong id or a bad stage says so, so Claude can correct itself;
        anything else stays the SDK's generic error."""
        @functools.wraps(fn)
        def wrapper(*a: Any, **kw: Any) -> Any:
            try:
                return fn(*a, **kw)
            except (OutreachError, pipeline.PipelineError, EditTimeout, ValueError) as exc:
                raise ToolError(str(exc)) from exc
        return wrapper

    srv = MCPServer("switchkaro" if hosted else "jobsearch",
                    instructions=HOSTED_INSTRUCTIONS if hosted else INSTRUCTIONS, **server_kw)
    t: Callable[[], Tools] = (lambda: tools) if isinstance(tools, Tools) else tools

    def tool(**kw: Any) -> Any:
        def add(fn: Any) -> Any:
            return fn if hosted and fn.__name__ in LOCAL_ONLY else srv.tool(**kw)(explained(fn))
        return add
    ro = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    rw = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    @tool(annotations=ro)
    def find_jobs(status: str = "new", fit: list[str] | None = None,
                  search: str = "", limit: int = 25) -> dict[str, Any]:
        """Open postings that passed the filters, best first.
        status: new (undecided, the default) | shortlisted | applied | interviewing |
        offer | skipped | closed | any. fit: any of strong, possible, weak, unranked.
        search: matches title, company or location."""
        return t().find_jobs(status, fit, search, limit)

    @tool(annotations=ro)
    def get_job(posting_id: str) -> dict[str, Any]:
        """One posting in full: description, matched skills, gaps, the ranking's
        reason, decision history and referral drafts. A unique id prefix works."""
        return t().get_job(posting_id)

    @tool(annotations=ro)
    def today() -> dict[str, Any]:
        """What needs the person now: postings to decide, ones back from 'later',
        applications in progress, referral follow-ups due."""
        return t().today()

    @tool(annotations=ro)
    def resume_store() -> dict[str, Any]:
        """The person's defensible profile and the skills they may claim, by
        tier. Use it for fit questions and tailoring; never add to it."""
        return t().resume_store()

    @tool(annotations=ro)
    def application_kit(posting_id: str) -> dict[str, Any]:
        """Everything an application form asks for, for one posting: experience
        rows, skills to add or remove, certifications, education, links."""
        return t().application_kit(posting_id)

    @tool(annotations=ro)
    def jobs_to_rank(limit: int = 8) -> dict[str, Any]:
        """Postings no model has judged yet, with the profile and the ranking
        rules. Read them, judge each, then call save_rankings."""
        return t().jobs_to_rank(limit)

    @tool(annotations=rw)
    def save_rankings(judgements: list[Judgement]) -> dict[str, Any]:
        """Store fit judgements from jobs_to_rank. A posting asking more years
        than the profile allows is recorded as weak whatever is sent."""
        return t().save_rankings(judgements)

    @tool(annotations=rw)
    def set_status(posting_id: str,
                   status: Literal["new", "shortlisted", "applied", "interviewing",
                                   "offer", "closed", "skipped"],
                   note: str = "") -> dict[str, Any]:
        """Record what the person decided or did. Only when they say so - never
        apply on their behalf."""
        return t().set_status(posting_id, status, note)

    @tool(annotations=rw)
    def save_for_later(posting_id: str, days: int | None = 7) -> dict[str, Any]:
        """Off the list until `days` from now (1-90), then back. days=null means
        someday, with no date."""
        return t().save_for_later(posting_id, days)

    @tool(annotations=rw)
    def set_favorite(posting_id: str, on: bool = True) -> dict[str, Any]:
        """Star a posting so it sorts first; on=false removes the star."""
        return t().set_favorite(posting_id, on)

    @tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                          openWorldHint=True))
    def refresh_jobs() -> dict[str, Any]:
        """Poll every board now, mark closed postings, filter what is new. Runs in
        the background for a few minutes; check with refresh_status."""
        return t().refresh_jobs()

    @tool(annotations=ro)
    def refresh_status() -> dict[str, Any]:
        """Whether a refresh is running, and the last finished one's summary."""
        return t().refresh_status()

    return srv


def _secret_gate(app: Any, secret: str, tools: Tools) -> Any:
    """Only /<secret>/... reaches the app, with the prefix stripped. /healthz
    answers for Render's health check. Everything else is a bare 404, so the
    server does not even say it is an MCP server."""
    prefix = f"/{secret}"

    async def gate(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)          # lifespan
            return
        path = scope.get("path", "")
        if path == "/healthz":
            await _plain(send, 200, b"ok")
            return
        head, sep, rest = path[1:].partition("/")
        if not hmac.compare_digest(f"/{head}".encode(), prefix.encode()):
            await _plain(send, 404, b"not found")
            return
        if rest == "refresh" and scope["method"] == "POST":
            # For a scheduler (cron-job.org, a GitHub Action) to wake the
            # server and poll on a timetable - the free tier has no cron.
            res = tools.refresh_jobs()
            await _plain(send, 202 if res["started"] else 409,
                         b"started" if res["started"] else b"already running")
            return
        scope = dict(scope, path="/" + rest if sep else "/",
                     raw_path=("/" + rest if sep else "/").encode())
        await app(scope, receive, send)

    return gate


async def _plain(send: Any, code: int, body: bytes) -> None:
    await send({"type": "http.response.start", "status": code,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
    await send({"type": "http.response.body", "body": body})


def build_app(prof: Profile, secret: str, remote: statestore.Remote | None) -> Any:
    if len(secret) < 24:
        raise SystemExit("CONNECTOR_SECRET must be at least 24 characters - it is the only lock")
    from mcp.server.transport_security import TransportSecuritySettings
    tools = Tools(prof, remote)
    tools.pull()                                     # a fresh disk starts from the database
    srv = build_server(tools)
    # All interfaces: a hosted service is reached through its platform's proxy.
    app = srv.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, host="0.0.0.0",  # nosec B104
        # The Host header is Render's hostname; the secret path is the check.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    return _secret_gate(app, secret, tools)


def serve(prof: Profile) -> int:
    import uvicorn
    from .llm import load_dotenv
    load_dotenv(prof.env)
    secret = os.environ.get("CONNECTOR_SECRET", "").strip()
    app = build_app(prof, secret, statestore.remote_from_env())
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)),  # nosec B104
                proxy_headers=True, forwarded_allow_ips="*")
    return 0
