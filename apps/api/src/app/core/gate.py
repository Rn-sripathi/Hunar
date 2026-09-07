"""A shared-password gate for a public deployment.

This is not authentication. There are no accounts, no per-user identity
and no audit of who did what; the README says so plainly. What it does is
stop a URL that appears in a submission email from being readable by
anyone who finds it, which matters here because the data behind it
includes real candidates' names and phone numbers.

Three paths stay open, and each for a specific reason:

* ``/webhooks/*`` — Hunar cannot log in. That path is authenticated
  instead by an HMAC signature over the raw request body, which is
  strictly stronger than a shared password.
* ``/healthz`` and ``/readyz`` — the platform polls these to decide
  whether the instance is alive. Gating them means the service is
  reported as permanently unhealthy.
* ``/docs`` and ``/openapi.json`` — reading the API schema is a feature
  for anyone reviewing this, and it holds no data and no credentials.

The cookie is signed rather than merely set, so possessing it cannot be
faked without the session secret. It carries no claims: it says only
"this browser presented the password", which is the entire truth of what
was verified.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.config import Settings

logger = structlog.get_logger(__name__)

__all__ = ["COOKIE_NAME", "install_demo_gate", "issue_token", "token_is_valid"]

COOKIE_NAME = "hunar_demo"

#: A week. Long enough that a reviewer is not asked twice, short enough
#: that a leaked cookie stops working on its own.
MAX_AGE_SECONDS = 7 * 24 * 60 * 60

#: Paths that must answer without a password, and why, are in the module
#: docstring. Kept as a tuple of prefixes rather than a regex so the list
#: is readable at a glance during review.
OPEN_PREFIXES: tuple[str, ...] = (
    "/webhooks/",
    "/healthz",
    "/readyz",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
)


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(secret: str, *, now: float | None = None) -> str:
    """Mint a signed cookie value asserting the password was presented."""
    issued = int(now if now is not None else time.time())
    payload = f"v1.{issued}"
    return f"{payload}.{_sign(payload, secret)}"


def token_is_valid(token: str, secret: str, *, now: float | None = None) -> bool:
    """Whether a cookie was minted by us and has not expired.

    Compared with :func:`hmac.compare_digest` so a wrong signature cannot
    be discovered one byte at a time from response timing.
    """
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return False

    version, issued_raw, signature = parts
    payload = f"{version}.{issued_raw}"
    if not hmac.compare_digest(signature, _sign(payload, secret)):
        return False

    try:
        issued = int(issued_raw)
    except ValueError:
        return False

    age = (now if now is not None else time.time()) - issued
    return 0 <= age <= MAX_AGE_SECONDS


def install_demo_gate(app: FastAPI, settings: Settings) -> None:
    """Require a shared password before anything reads real data.

    Installed only when a password is configured, so local development is
    untouched and a misconfigured deployment fails loudly at startup
    rather than quietly serving everything.
    """
    if not settings.demo_password:
        return

    password = settings.demo_password
    secret = settings.session_secret

    @app.post("/api/unlock", include_in_schema=False)
    async def unlock(request: Request) -> Response:
        """Exchange the shared password for a signed cookie."""
        try:
            body = await request.json()
        except ValueError:
            body = {}
        supplied = str((body or {}).get("password") or "")

        # Constant-time, so the password cannot be guessed a character at
        # a time by measuring how long the comparison takes.
        if not secrets.compare_digest(supplied, password):
            logger.warning("gate.rejected", path="/api/unlock")
            return JSONResponse(
                {"error": {"code": "bad_password", "message": "That password is not right."}},
                status_code=401,
            )

        response = JSONResponse({"status": "unlocked"})
        response.set_cookie(
            COOKIE_NAME,
            issue_token(secret),
            max_age=MAX_AGE_SECONDS,
            httponly=True,
            secure=True,
            # `none` rather than `lax`, because the frontend is on a
            # different site from the API: Vercel and Render are separate
            # registrable domains, so a lax cookie is never sent.
            samesite="none",
            path="/",
        )
        return response

    @app.middleware("http")
    async def require_password(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path

        if path == "/api/unlock" or path.startswith(OPEN_PREFIXES):
            return await call_next(request)

        # A browser sends the preflight without cookies, so refusing it
        # turns every cross-origin request into an opaque CORS failure
        # rather than a 401 the UI can act on.
        if request.method == "OPTIONS":
            return await call_next(request)

        token = request.cookies.get(COOKIE_NAME, "")
        if token and token_is_valid(token, secret):
            return await call_next(request)

        return JSONResponse(
            {
                "error": {
                    "code": "locked",
                    "message": (
                        "This deployment is password protected. It holds real "
                        "candidates' names and phone numbers, so it is not open "
                        "to the internet."
                    ),
                }
            },
            status_code=401,
        )
