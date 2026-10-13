"""SwitchKaro inside Claude: the connector's tools for every member, each
signed in as themselves.

claude.ai's custom connectors sign in with OAuth. Supabase is the
authorization server (its OAuth 2.1 server, with dynamic client
registration): Claude registers itself and sends the person to Supabase,
which sends them to this service's consent page (/oauth/consent,
web/src/pages/Consent.tsx). There they approve as the member they are, and
only for a client whose return address is Claude's own. The token Supabase
then issues is checked here like any sign-in (signature, issuer, expiry, a
signed-in user) and the person must be a member in good standing; the tools
then run on that person's profile alone, within their own limits.

The tools are connector.py's, minus polling every board for one person: the
service updates jobs on its own schedule. Off where it can't work: dev
sign-in, or no public address to name the resource by (JOBSEARCH_PUBLIC_URL,
or Render's RENDER_EXTERNAL_URL).
"""
from __future__ import annotations

import os
from typing import Any

from starlette.concurrency import run_in_threadpool

from ..connector import Tools, build_server
from ..profile import Profile
from . import accounts
from .auth import AuthError

PATH = "/mcp"
# Where Claude's connectors come back to after sign-in (support.claude.com,
# "Building custom connectors via remote MCP servers"). The consent page
# approves no other client.
CLAUDE_CALLBACKS = ("https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback")


def public_url() -> str:
    return (os.environ.get("JOBSEARCH_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")


def metadata(svc: Any) -> dict[str, Any]:
    """RFC 9728: who issues tokens for this resource."""
    return {"resource": public_url() + PATH, "authorization_servers": [svc.verifier.issuer],
            "bearer_methods_supported": ["header"], "resource_name": "SwitchKaro"}


class MemberTokens:
    """The MCP SDK's TokenVerifier: a token, and the member it belongs to."""

    def __init__(self, svc: Any) -> None:
        self.svc = svc

    async def verify_token(self, token: str) -> Any:
        from mcp.server.auth.provider import AccessToken
        try:
            who, claims = await run_in_threadpool(self.svc.verifier.verify_claims, token, True)
        except AuthError:
            return None
        row = accounts.user(self.svc.shared, who.sub)
        if row is None or row.get("suspended") or not accounts.consent_current(row):
            return None                      # the consent page says why, on the next sign-in
        return AccessToken(token=token, client_id=str(claims.get("client_id") or "switchkaro"), scopes=[],
                           expires_at=int(claims["exp"]), subject=who.sub)


def member_tools(svc: Any) -> Tools:
    """The signed-in person's tools, for this one call."""
    from mcp.server.auth.middleware.auth_context import get_access_token
    tok = get_access_token()
    row = accounts.user(svc.shared, tok.subject) if tok is not None and tok.subject else None
    if row is None:
        raise PermissionError("sign in again")
    return Tools(Profile(svc.root, row["profile"]))


def build(svc: Any) -> tuple[Any, Any] | None:
    """(the MCP server, its app), or None where it can't run."""
    base = public_url()
    if svc.verifier.dev or not base:
        return None
    from mcp.server.auth.settings import AuthSettings
    from mcp.server.transport_security import TransportSecuritySettings
    srv = build_server(lambda: member_tools(svc), hosted=True, token_verifier=MemberTokens(svc),
                       auth=AuthSettings(issuer_url=svc.verifier.issuer, resource_server_url=base + PATH,
                                         validate_token_resource=False))
    app = srv.streamable_http_app(
        streamable_http_path=PATH, stateless_http=True, json_response=True,
        # The Host header is the platform's hostname; every request carries its own token.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    return srv, app
