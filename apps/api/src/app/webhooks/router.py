"""Receives and authenticates webhooks from Hunar.

The handler is deliberately paranoid, and each precaution addresses a
specific way this endpoint could go wrong.

**Raw bytes before parsing.** The HMAC covers the body exactly as sent,
so the body is read as bytes before any JSON handling, and no
body-rewriting middleware may sit on this path.

**Persist before interpreting.** The payload shapes for these events are
undocumented. Storing the raw body first means an unexpected shape is
diagnosable rather than merely lost, and gives an audit trail of what
actually arrived.

**Deduplicate, because retries are certain.** Hunar retries at one, two,
four and eight minutes, so the same event arrives repeatedly. A unique
key over the event, token and body collapses them.

**Do not trust the payload for state.** After authenticating a delivery,
the handler refetches the call from the API and applies that instead.
The webhook is treated as a signal that something changed, not as the
description of what it changed to. This makes the whole flow correct
regardless of what the undocumented body actually contains.

**Answer quickly.** Verification and persistence happen inline; the
refetch happens in a background task. A slow handler causes retries,
which causes duplicates, which causes load.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Request, Response, status
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.models import WebhookEvent
from app.deps import DbSession, SettingsDep
from app.hiring.services.call_service import apply_call_snapshot, resolve_by_token
from app.hiring.services.job_service import get_job
from hunar_sdk import HunarError
from hunar_sdk.webhooks import verify_webhook

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks/hunar", tags=["webhooks"])

_EVENT_SLUGS = frozenset({"status", "recording", "result", "summary"})


@router.post("/{callback_token}/{event_slug}", status_code=status.HTTP_200_OK)
async def receive(
    callback_token: str,
    event_slug: str,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    session: DbSession,
    settings: SettingsDep,
) -> dict[str, str]:
    """Authenticate one webhook delivery and schedule a state refresh."""
    if event_slug not in _EVENT_SLUGS:
        response.status_code = status.HTTP_404_NOT_FOUND
        return {"status": "unknown_event"}

    # Raw bytes, before anything touches the body.
    body = await request.body()

    verification = verify_webhook(
        signature_header=request.headers.get("X-Hunar-Signature"),
        timestamp_header=request.headers.get("X-Hunar-Timestamp"),
        body=body,
        trusted_keys=settings.trusted_webhook_keys,
        max_skew_seconds=settings.webhook_max_skew_seconds,
    )

    if not verification.ok:
        # 401 rather than 400, so Hunar retries. If the rejection was
        # caused by a transient misconfiguration such as a key mid-rotation,
        # a retry is exactly what should happen.
        logger.warning("webhook.rejected", reason=verification.reason, event=event_slug)
        response.status_code = status.HTTP_401_UNAUTHORIZED
        return {"status": "rejected", "reason": verification.reason or "invalid"}

    try:
        payload: Any = json.loads(body) if body else {}
    except json.JSONDecodeError:
        logger.warning("webhook.malformed_json", event=event_slug)
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"status": "malformed_json"}

    dedupe_key = hashlib.sha256(
        b"|".join([event_slug.encode(), callback_token.encode(), body])
    ).hexdigest()

    attempt = await resolve_by_token(session, callback_token)

    statement = (
        insert(WebhookEvent)
        .values(
            dedupe_key=dedupe_key,
            event_type=str(payload.get("event_type", event_slug))
            if isinstance(payload, dict)
            else event_slug,
            callback_token=callback_token,
            call_attempt_id=attempt.id if attempt else None,
            signature_valid=True,
            hunar_timestamp=verification.timestamp,
            payload=payload if isinstance(payload, dict) else {"raw": payload},
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(WebhookEvent.id)
    )
    event_id = (await session.execute(statement)).scalar_one_or_none()
    await session.commit()

    if event_id is None:
        # A retry of something already handled. Answering 200 stops Hunar
        # retrying further.
        return {"status": "duplicate_ignored"}

    if attempt is None:
        # Authentic but unrecognised. Worth recording rather than dropping,
        # since it usually means a webhook arrived for a call created by a
        # different deployment sharing the key.
        logger.warning("webhook.unknown_token", event=event_slug)
        return {"status": "accepted_unmatched"}

    background.add_task(
        _refresh_from_upstream,
        session_factory=request.app.state.session_factory,
        client=request.app.state.hunar_client,
        attempt_id=attempt.id,
    )
    return {"status": "accepted"}


async def _refresh_from_upstream(
    *, session_factory: async_sessionmaker[Any], client: Any, attempt_id: Any
) -> None:
    """Refetch the call and apply its authoritative state.

    Runs after the response so the acknowledgement is fast. Deliberately
    ignores the webhook body: the API's own view of the call is the
    source of truth, which keeps this correct even though the payload
    shape is undocumented.
    """
    from app.core.models import CallAttempt

    async with session_factory() as session:
        attempt = await session.get(CallAttempt, attempt_id)
        if attempt is None or attempt.hunar_call_id is None or attempt.job_id is None:
            return

        try:
            snapshot = await client.get_call(attempt.hunar_call_id)
        except HunarError as exc:
            logger.warning("webhook.refresh_failed", attempt_id=str(attempt_id), error=exc.message)
            return

        job = await get_job(session, attempt.job_id)
        await apply_call_snapshot(session, job, attempt, snapshot)
        await session.commit()

        logger.info("webhook.applied", attempt_id=str(attempt_id), status=attempt.status)


__all__ = ["router"]
