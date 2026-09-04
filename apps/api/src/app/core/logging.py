"""Structured logging with secret and PII redaction.

Two things must never reach a log line, and both are easy to leak by
accident because the natural debugging instinct is to log the whole
payload:

* **The Hunar API key.** It authenticates outbound requests *and* doubles
  as the HMAC secret for inbound webhooks, so leaking it into a log
  aggregator would let anyone forge webhooks that mutate call records.
* **Candidate phone numbers.** These are personal data belonging to
  people who are being called about a job, often sourced from a broker
  rather than volunteered. Logs are the least controlled place they could
  end up, so numbers are masked to their last four digits, which is
  enough to correlate a support question without retaining the number.

Redaction runs as a structlog processor rather than at each call site,
because a rule enforced in one place cannot be forgotten in another.
"""

from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.types import EventDict, Processor

__all__ = [
    "bind_request_id",
    "configure_logging",
    "get_request_id",
    "redact_processor",
    "register_secret",
]

#: Correlation id for the in-flight request, so every line emitted while
#: handling it can be tied together and matched to a webhook delivery.
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

#: Literal secret values learned at startup. Any log value containing one
#: is masked, which catches the case where a secret is embedded in a URL
#: or an error message rather than passed as a named field.
_secrets: set[str] = set()

_REDACT_KEYS = frozenset(
    {
        "api_key",
        "hunar_api_key",
        "pdl_api_key",
        "anthropic_api_key",
        "x-api-key",
        "authorization",
        "signature",
        "x-hunar-signature",
        "password",
        "demo_password",
        "session_secret",
        "secret",
        "token",
        "callback_token",
        "cookie",
        "set-cookie",
    }
)

_PHONE_KEYS = frozenset(
    {
        "mobile_number",
        "from_phone_number",
        "phone",
        "phone_number",
        "phone_e164",
        "mobile_phone",
        "phone_numbers",
        "personal_emails",
        "e164",
    }
)

_PHONE_IN_TEXT = re.compile(r"\+\d{8,15}")
_MIN_MASKABLE_SECRET_LENGTH = 8


def register_secret(value: str | None) -> None:
    """Mark a literal value as never loggable.

    Called at startup for every credential, so redaction also covers
    secrets that appear inside free text rather than as a named field.
    """
    if value and len(value) >= _MIN_MASKABLE_SECRET_LENGTH:
        _secrets.add(value)


def bind_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str | None:
    return _request_id.get()


def _mask_phone(value: str) -> str:
    """Keep the last four digits, which is enough to correlate a report."""
    digits = re.sub(r"\D", "", value)
    if len(digits) < 4:
        return "***"
    return f"+{'•' * max(len(digits) - 4, 0)}{digits[-4:]}"


def _scrub(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact secrets and personal data from one log value."""
    lowered = key.lower() if key else ""

    if lowered in _REDACT_KEYS:
        return "[redacted]"

    if isinstance(value, str):
        if lowered in _PHONE_KEYS:
            return _mask_phone(value)
        scrubbed = value
        for secret in _secrets:
            if secret in scrubbed:
                scrubbed = scrubbed.replace(secret, "[redacted]")
        # A number embedded in a message, e.g. "call to +919876543210 failed".
        return _PHONE_IN_TEXT.sub(lambda m: _mask_phone(m.group()), scrubbed)

    if isinstance(value, dict):
        return {k: _scrub(v, key=str(k)) for k, v in value.items()}

    if isinstance(value, list | tuple):
        masked = [_scrub(item, key=key) for item in value]
        return type(value)(masked) if isinstance(value, tuple) else masked

    return value


def redact_processor(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """structlog processor applying redaction to the whole event."""
    return {key: _scrub(value, key=str(key)) for key, value in event_dict.items()}


def _add_request_id(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    request_id = _request_id.get()
    if request_id:
        event_dict["request_id"] = request_id
    return event_dict


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Configure structlog and route the standard library through it.

    JSON in deployment because hosting platforms index it; a coloured
    console renderer locally because a human is reading it.
    """
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        # `add_logger_name` is deliberately absent: it reads `logger.name`,
        # which PrintLogger does not have, and the resulting AttributeError
        # only surfaces while handling another exception. Losing the logger
        # name is a fair trade for not masking the error being reported.
        _add_request_id,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # Redaction runs last, so it also covers anything the processors
        # above added, including rendered exception text.
        redact_processor,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=logging.getLevelNamesMapping()[level]
    )
    # uvicorn's access log duplicates our own request logging.
    logging.getLogger("uvicorn.access").disabled = True
