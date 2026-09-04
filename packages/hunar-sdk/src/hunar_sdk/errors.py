"""Typed exception hierarchy for the Hunar Voice Agents API.

Each documented HTTP status maps to a distinct exception so that callers
can branch on meaning rather than on a number, and so the API layer can
translate them into stable error codes the frontend reacts to. The two
that matter most in this product:

* :class:`HunarAuthError` (401) and :class:`HunarQuotaError` (402) are what
  a revoked or exhausted key looks like. Both trigger the automatic
  degrade to demo mode, so they must be distinguishable from a transient
  failure.
* :attr:`HunarError.retryable` marks the failures that are safe to retry.
  Note that safety here is about the *transport*, not the operation:
  a read may be retried freely, a call creation never is.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "HunarAuthError",
    "HunarError",
    "HunarNotFoundError",
    "HunarQuotaError",
    "HunarServerError",
    "HunarTransportError",
    "HunarValidationError",
    "WebhookVerificationError",
]


class HunarError(Exception):
    """Base class for every Hunar failure.

    Args:
        message: Human-readable description, safe to surface in the UI.
        status_code: The HTTP status, when the failure came from a response.
        payload: The parsed error body, for logging and field-level detail.
        retryable: Whether retrying the same transport call could succeed.
        error_code: Stable machine-readable code sent to the frontend.
    """

    error_code: str = "hunar_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload
        self.retryable = retryable

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"[{self.status_code}] {self.message}"


class HunarTransportError(HunarError):
    """Connection reset, DNS failure or timeout. No response was received.

    For reads this is safely retryable. For a call creation it is the
    dangerous case: the request may have been processed anyway, so the
    attempt is parked as ``SubmitState.UNKNOWN`` instead of retried.
    """

    error_code = "hunar_transport_error"

    def __init__(self, message: str, *, payload: Any = None) -> None:
        super().__init__(message, status_code=None, payload=payload, retryable=True)


class HunarValidationError(HunarError):
    """400 or 422. The request was rejected on business or schema grounds.

    A 400 also covers telephony refusals, such as an unroutable number,
    which is why the message is surfaced to the operator verbatim.
    """

    error_code = "hunar_validation_error"

    def __init__(self, message: str, *, status_code: int = 422, payload: Any = None) -> None:
        super().__init__(message, status_code=status_code, payload=payload, retryable=False)

    @property
    def field_errors(self) -> dict[str, list[str]]:
        """Best-effort extraction of per-field messages from a 422 body."""
        errors: dict[str, list[str]] = {}
        detail = self.payload.get("detail") if isinstance(self.payload, dict) else None
        if not isinstance(detail, list):
            return errors
        for item in detail:
            if not isinstance(item, dict):
                continue
            loc = item.get("loc")
            field = ".".join(str(p) for p in loc[1:]) if isinstance(loc, list) else "_"
            errors.setdefault(field or "_", []).append(str(item.get("msg", "invalid")))
        return errors


class HunarAuthError(HunarError):
    """401. The API key is missing, malformed, revoked or expired.

    Expected in this project: the assignment key is time-limited, so this
    is the signal that latches the app into demo mode rather than an
    error to shout about.
    """

    error_code = "hunar_auth_error"

    def __init__(self, message: str = "Hunar API key rejected", *, payload: Any = None) -> None:
        super().__init__(message, status_code=401, payload=payload, retryable=False)


class HunarQuotaError(HunarError):
    """402. Subscription expired or call minutes exhausted.

    Fatal mid-demo, so call minutes are budgeted tightly and this also
    latches the app into demo mode.
    """

    error_code = "hunar_quota_error"

    def __init__(
        self,
        message: str = "Hunar subscription expired or call minutes exhausted",
        *,
        payload: Any = None,
    ) -> None:
        super().__init__(message, status_code=402, payload=payload, retryable=False)


class HunarNotFoundError(HunarError):
    """404. The agent or call does not exist."""

    error_code = "hunar_not_found"

    def __init__(self, message: str = "Hunar resource not found", *, payload: Any = None) -> None:
        super().__init__(message, status_code=404, payload=payload, retryable=False)


class HunarServerError(HunarError):
    """5xx, or 429. Hunar's side failed or throttled us. Safe to retry reads."""

    error_code = "hunar_server_error"

    def __init__(self, message: str, *, status_code: int = 500, payload: Any = None) -> None:
        super().__init__(message, status_code=status_code, payload=payload, retryable=True)


class WebhookVerificationError(HunarError):
    """An inbound webhook failed signature, timestamp or shape validation.

    Carries a short machine-readable ``reason`` so rejections can be
    counted by cause without logging the payload.
    """

    error_code = "webhook_verification_failed"

    def __init__(self, reason: str) -> None:
        super().__init__(f"Webhook verification failed: {reason}", status_code=401)
        self.reason = reason
