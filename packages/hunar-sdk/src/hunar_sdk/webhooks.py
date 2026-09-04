"""Inbound webhook signature verification.

Hunar signs each webhook with two headers:

* ``X-Hunar-Signature`` — base64 HMAC-SHA256. May carry SEVERAL
  comma-separated signatures, one per currently-active API key, which is
  how Hunar supports key rotation without dropping deliveries.
* ``X-Hunar-Timestamp`` — unix seconds, included in the signed message so
  a captured request cannot be replayed indefinitely.

The signing construction is::

    message = f"{timestamp.strip()}.".encode() + raw_body
    signature = base64(hmac_sha256(api_key, message))

Two details are easy to get wrong and both are load-bearing:

1. **The secret is the API key itself**, not a separate webhook secret.
   That is also why the key must never leave the backend: it doubles as
   the credential proving a webhook is genuine.
2. **The body must be the raw bytes exactly as received.** Any middleware
   that decompresses, re-encodes or pretty-prints the body invalidates
   every signature. The webhook route therefore reads ``await
   request.body()`` before touching JSON, and no body-rewriting
   middleware may be mounted on that path.

A note on the replay window: Hunar retries failed deliveries at 1, 2, 4
and 8 minutes. An 8-minute retry is 480 seconds old, which exceeds the
conventional 300-second tolerance. If Hunar re-signs each attempt with a
fresh timestamp this never matters; if it reuses the original timestamp,
a 300-second window silently drops the final retry. Because that is
unverified, the tolerance is a parameter rather than a constant, and
:data:`DEFAULT_MAX_SKEW_SECONDS` errs on the documented convention while
``WEBHOOK_MAX_SKEW_SECONDS`` lets it be widened in one deploy.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "DEFAULT_MAX_SKEW_SECONDS",
    "VerificationResult",
    "compute_signature",
    "verify_webhook",
]

#: Replay tolerance in seconds. See the module docstring on the 8-minute retry.
DEFAULT_MAX_SKEW_SECONDS = 300


def compute_signature(api_key: str, timestamp: str, body: bytes) -> str:
    """Return the base64 HMAC-SHA256 signature for one key.

    Args:
        api_key: The Hunar API key, which is the HMAC secret.
        timestamp: The ``X-Hunar-Timestamp`` value, verbatim. It is
            stripped but otherwise used exactly as sent, because the
            signature covers its literal text.
        body: The raw request body bytes, unmodified.
    """
    message = f"{timestamp.strip()}.".encode() + body
    digest = hmac.new(api_key.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Outcome of verifying one inbound webhook.

    Attributes:
        ok: Whether the request is authentic and fresh.
        reason: Short machine-readable failure cause, ``None`` on success.
            Suitable for metrics labels; never contains payload data.
        timestamp: The parsed timestamp, when it parsed at all.
    """

    ok: bool
    reason: str | None = None
    timestamp: int | None = None


def verify_webhook(
    *,
    signature_header: str | None,
    timestamp_header: str | None,
    body: bytes,
    trusted_keys: Sequence[str],
    max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS,
    now: float | None = None,
) -> VerificationResult:
    """Verify a Hunar webhook's signature and freshness.

    Accepts a *sequence* of trusted keys and a *list* of provided
    signatures, so both key rotation on our side and Hunar's
    multiple-active-key signing work without special cases.

    Args:
        signature_header: Raw ``X-Hunar-Signature``, possibly comma-separated.
        timestamp_header: Raw ``X-Hunar-Timestamp``.
        body: Raw request body bytes, exactly as received.
        trusted_keys: Current key first, then any recently rotated keys.
        max_skew_seconds: Replay tolerance in either direction.
        now: Injectable clock for deterministic tests.

    Returns:
        A :class:`VerificationResult`. Callers should reject with 401 on
        failure, which prompts Hunar to retry, and should log ``reason``
        without logging the body.
    """
    if not signature_header or not timestamp_header:
        return VerificationResult(False, "missing_headers")

    try:
        parsed_timestamp = int(timestamp_header.strip())
    except (TypeError, ValueError):
        return VerificationResult(False, "malformed_timestamp")

    current = time.time() if now is None else now
    if abs(current - parsed_timestamp) > max_skew_seconds:
        return VerificationResult(False, "timestamp_out_of_window", parsed_timestamp)

    provided = [part.strip() for part in signature_header.split(",") if part.strip()]
    if not provided:
        return VerificationResult(False, "empty_signature", parsed_timestamp)

    usable_keys = [key for key in trusted_keys if key]
    if not usable_keys:
        return VerificationResult(False, "no_trusted_keys", parsed_timestamp)

    # Compute every expected signature up front, then compare all pairs
    # without short-circuiting, so total runtime does not depend on which
    # key or which signature matched.
    expected = [compute_signature(key, timestamp_header, body) for key in usable_keys]

    matched = False
    for candidate in provided:
        for reference in expected:
            if hmac.compare_digest(candidate, reference):
                matched = True

    if not matched:
        return VerificationResult(False, "signature_mismatch", parsed_timestamp)
    return VerificationResult(True, None, parsed_timestamp)
