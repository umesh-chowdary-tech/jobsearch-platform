"""Who is asking: a Supabase sign-in token, checked on every request.

Supabase signs each person's access token. Projects made since 2025 sign with
an asymmetric key published at <SUPABASE_URL>/auth/v1/.well-known/jwks.json;
older ones with a shared secret (SUPABASE_JWT_SECRET). Either way a token must
be unexpired, issued by this project, meant for signed-in users
("authenticated") and carry a user id. The algorithm decides the key - a
shared-secret token is checked only against the secret, an asymmetric one only
against the published keys - so a token can't pick an easier check for
itself. The email comes from the token, never from a request body.

Local development without a Supabase project: dev mode signs its own tokens
with a random secret made when the process starts, and the page offers "sign
in as" any email. The service refuses dev mode when Supabase is configured or
when it listens on anything but this machine.
"""
from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt

AUDIENCE = "authenticated"
DEV_ISSUER = "jobsearch-dev"
DEV_TOKEN_HOURS = 12
ASYMMETRIC = ("ES256", "RS256", "EdDSA")


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Who:
    sub: str
    email: str


class Verifier:
    def __init__(self, supabase_url: str = "", jwt_secret: str = "", dev: bool = False,
                 jwks_client: Any = None) -> None:
        url = supabase_url.rstrip("/")
        if dev and (url or jwt_secret):
            raise ValueError("dev sign-in can't be used with a Supabase project configured")
        if not dev and not url:
            raise ValueError("set SUPABASE_URL (or use dev sign-in on this machine)")
        self.dev = dev
        self.issuer = DEV_ISSUER if dev else f"{url}/auth/v1"
        self.secret = secrets.token_urlsafe(32) if dev else jwt_secret
        self.jwks = None if dev else (jwks_client or jwt.PyJWKClient(f"{self.issuer}/.well-known/jwks.json",
                                                                     cache_keys=True, lifespan=3600))

    def verify(self, token: str) -> Who:
        return self.verify_claims(token)[0]

    def verify_claims(self, token: str, any_audience: bool = False) -> tuple[Who, dict[str, Any]]:
        """The person and the token's claims. `any_audience`: a token Supabase
        issued to an app the person approved (its OAuth server - the Claude
        connector) may name that app as its audience; it is still this
        project's, signed by its keys, unexpired, and a signed-in user's."""
        try:
            alg = jwt.get_unverified_header(token).get("alg")
            if alg == "HS256" and self.secret:
                key: Any = self.secret
            elif alg in ASYMMETRIC and self.jwks is not None:
                key = self.jwks.get_signing_key_from_jwt(token).key
            else:
                raise AuthError("this sign-in token isn't accepted here")
            claims = jwt.decode(token, key, algorithms=[alg], issuer=self.issuer,
                                audience=None if any_audience else AUDIENCE,
                                options={"require": ["exp", "sub", "iss"] if any_audience else ["exp", "sub", "aud", "iss"],
                                         "verify_aud": not any_audience})
        except AuthError:
            raise
        except (jwt.PyJWTError, ValueError) as exc:
            raise AuthError("sign in again") from exc
        sub = str(claims.get("sub") or "")
        if not sub:
            raise AuthError("sign in again")
        if any_audience:
            # Not the project's own keys (anon, service_role), not an anonymous
            # user, and either a sign-in's audience or an app's grant.
            if claims.get("role", "authenticated") != "authenticated" or claims.get("is_anonymous"):
                raise AuthError("this sign-in token isn't accepted here")
            if claims.get("aud") != AUDIENCE and AUDIENCE not in (claims.get("aud") or []) and not claims.get("client_id"):
                raise AuthError("this sign-in token isn't accepted here")
        return Who(sub=sub, email=str(claims.get("email") or "").strip().lower()), claims

    def mint_dev(self, email: str) -> str:
        """A token for `email`, in dev mode only. The same email always gets
        the same user id, so a restart signs a person back into their profile."""
        if not self.dev:
            raise AuthError("dev sign-in is off")
        email = email.strip().lower()
        now = int(time.time())
        return jwt.encode({"sub": str(uuid.uuid5(uuid.NAMESPACE_URL, f"jobsearch-dev:{email}")), "email": email,
                           "aud": AUDIENCE, "iss": self.issuer, "iat": now, "exp": now + DEV_TOKEN_HOURS * 3600,
                           "role": "authenticated"}, self.secret, algorithm="HS256")
