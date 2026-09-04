"""Domain exceptions and the handlers that render them.

Every failure leaves the API in one shape, carrying a *stable* machine
error code alongside a human message:

.. code-block:: json

    {
      "error": {
        "code": "hunar_quota_error",
        "message": "Hunar subscription expired or call minutes exhausted",
        "details": {}
      },
      "request_id": "01J..."
    }

The stability of ``code`` is the point. The frontend needs to react
differently to a quota being exhausted, an expired key and a rejected
phone number, and matching on prose would break the moment the wording
improves. Two codes in particular drive real UI behaviour:
``hunar_auth_error`` and ``hunar_quota_error`` are what an expired
assignment key looks like, and both make the app show its demo-mode
banner rather than an unexplained failure.

Upstream failures are deliberately reported as 502 or 503 rather than
500. A 500 says this application is broken; a 503 says a dependency is
unavailable and the request may succeed later, which is both truthful and
actionable.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_request_id
from hunar_sdk.errors import (
    HunarAuthError,
    HunarError,
    HunarNotFoundError,
    HunarQuotaError,
    HunarServerError,
    HunarTransportError,
    HunarValidationError,
    WebhookVerificationError,
)

logger = structlog.get_logger(__name__)

#: Referenced by the literal number because Starlette renamed its
#: constant, and pinning to either spelling breaks on the other version.
HTTP_422_UNPROCESSABLE = 422

__all__ = [
    "AppError",
    "ConflictError",
    "ConsentError",
    "NotFoundError",
    "ProviderQuotaError",
    "ValidationFailedError",
    "register_exception_handlers",
]


class AppError(Exception):
    """Base class for failures this application raises deliberately."""

    error_code = "app_error"
    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    error_code = "not_found"
    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    """The request contradicts existing state, such as a duplicate candidate."""

    error_code = "conflict"
    status_code = status.HTTP_409_CONFLICT


class ValidationFailedError(AppError):
    error_code = "validation_failed"
    status_code = HTTP_422_UNPROCESSABLE


class ConsentError(AppError):
    """An outbound call was blocked by the consent gate.

    Raised when a number is not on the allowlist, has asked not to be
    contacted, or when the request falls outside permitted calling hours.
    This is not an error to work around; refusing the call is the feature.
    """

    error_code = "consent_required"
    status_code = status.HTTP_403_FORBIDDEN


class ProviderQuotaError(AppError):
    """A people-search provider's credits or our own daily cap are spent."""

    error_code = "provider_quota_exhausted"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS


def _render(
    *, code: str, message: str, status_code: int, details: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {"code": code, "message": message, "details": details or {}},
            "request_id": get_request_id(),
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach every handler to the application."""

    @app.exception_handler(AppError)
    async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        logger.info("app.domain_error", code=exc.error_code, message=exc.message)
        return _render(
            code=exc.error_code,
            message=exc.message,
            status_code=exc.status_code,
            details=exc.details,
        )

    @app.exception_handler(WebhookVerificationError)
    async def _handle_webhook_verification(
        _request: Request, exc: WebhookVerificationError
    ) -> JSONResponse:
        # 401 is deliberate: it prompts Hunar to retry, which is the right
        # outcome if the rejection was caused by a transient misconfiguration
        # such as a key mid-rotation.
        logger.warning("webhook.rejected", reason=exc.reason)
        return _render(
            code=exc.error_code,
            message="Webhook signature verification failed",
            status_code=status.HTTP_401_UNAUTHORIZED,
            details={"reason": exc.reason},
        )

    @app.exception_handler(HunarValidationError)
    async def _handle_hunar_validation(
        _request: Request, exc: HunarValidationError
    ) -> JSONResponse:
        # Surfaced verbatim because a 400 here is often telephony refusing
        # a specific number, and the operator needs to read exactly that.
        return _render(
            code=exc.error_code,
            message=exc.message,
            status_code=status.HTTP_400_BAD_REQUEST,
            details={"field_errors": exc.field_errors},
        )

    @app.exception_handler(HunarAuthError)
    async def _handle_hunar_auth(_request: Request, exc: HunarAuthError) -> JSONResponse:
        logger.error("hunar.key_rejected")
        return _render(
            code=exc.error_code,
            message=(
                "The Hunar API key was rejected. It may have expired. "
                "Demo mode can serve recorded data instead."
            ),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.exception_handler(HunarQuotaError)
    async def _handle_hunar_quota(_request: Request, exc: HunarQuotaError) -> JSONResponse:
        logger.error("hunar.quota_exhausted")
        return _render(
            code=exc.error_code,
            message=(
                "Hunar call minutes are exhausted or the subscription has expired. "
                "Demo mode can serve recorded data instead."
            ),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.exception_handler(HunarNotFoundError)
    async def _handle_hunar_not_found(_request: Request, exc: HunarNotFoundError) -> JSONResponse:
        return _render(
            code=exc.error_code, message=exc.message, status_code=status.HTTP_404_NOT_FOUND
        )

    @app.exception_handler(HunarTransportError)
    async def _handle_hunar_transport(_request: Request, exc: HunarTransportError) -> JSONResponse:
        return _render(
            code=exc.error_code,
            message="Could not reach the Hunar API. Please retry.",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    @app.exception_handler(HunarServerError)
    async def _handle_hunar_server(_request: Request, exc: HunarServerError) -> JSONResponse:
        return _render(
            code=exc.error_code,
            message="The Hunar API is temporarily unavailable. Please retry.",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    @app.exception_handler(HunarError)
    async def _handle_hunar_generic(_request: Request, exc: HunarError) -> JSONResponse:
        """Catch-all so a new upstream failure never leaks a stack trace."""
        logger.error("hunar.unexpected_error", status=exc.status_code, message=exc.message)
        return _render(
            code=exc.error_code,
            message="An unexpected error occurred talking to the Hunar API.",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Reshape FastAPI's default 422 into our envelope."""
        field_errors: dict[str, list[str]] = {}
        for error in exc.errors():
            location = error.get("loc", ())
            field = ".".join(str(part) for part in location[1:]) or "_"
            field_errors.setdefault(field, []).append(str(error.get("msg", "invalid")))
        return _render(
            code="validation_failed",
            message="The request body failed validation.",
            status_code=HTTP_422_UNPROCESSABLE,
            details={"field_errors": field_errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return _render(
            code=f"http_{exc.status_code}",
            message=str(exc.detail),
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        """Last resort. Logs the detail, returns none of it.

        An unhandled exception's message can contain connection strings or
        fragments of a payload, so the client is told nothing beyond the
        correlation id needed to find the log entry.
        """
        # `exc_info=exc` rather than `.exception()`: this is a FastAPI
        # handler, not an `except` block, so the ambient exception context
        # a bare `.exception()` relies on is not guaranteed to be set.
        logger.error("app.unhandled_exception", error_type=type(exc).__name__, exc_info=exc)
        return _render(
            code="internal_error",
            message="An unexpected error occurred.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
