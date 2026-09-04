"""Placing screening calls, and keeping their state truthful.

Three decisions in this module carry most of its weight.

**A call POST is never retried.** If it times out, the call may or may not
have been placed, and ``GET /calls/`` offers no ``request_id`` filter to
check with. Retrying would risk a second real phone call to a real
person, so the attempt is parked as ``UNKNOWN`` and the reconciler
resolves it by paging the call list and matching on ``request_id``.

**Reconciliation is infrastructure, not a fallback.** Hunar's status
webhook fires only when a call reaches a terminal state, so ringing and
in-progress transitions are never pushed. Live status exists only because
this module polls for it.

**Reconciliation is lazy.** Rather than a background worker, a read of a
job's calls refreshes any non-terminal attempt that has not been synced
recently. That needs no scheduler, survives a host that sleeps between
requests, and cannot drift out of step with what the user is looking at.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.models import CallAttempt
from app.hiring.models import Candidate, CandidateDecision, Job, JobStatus
from app.hiring.schemas import (
    CallAttemptOut,
    FieldSpec,
    LaunchReport,
    LaunchRequest,
    QuestionOut,
    ResultRow,
    ResultsResponse,
)
from app.hiring.services.job_service import ensure_agent
from app.hiring.services.prompt_builder import custom_data_for
from app.hiring.services.result_normalizer import normalize_result
from app.hiring.services.scoring import score_candidate
from hunar_sdk import (
    CallbackConfig,
    CallCreate,
    CallStatus,
    HunarClient,
    HunarError,
    HunarTransportError,
    HunarValidationError,
    RetryConfig,
    SubmitState,
)
from hunar_sdk.enums import TERMINAL_STATUSES
from hunar_sdk.models import Call as HunarCall

logger = structlog.get_logger(__name__)

__all__ = [
    "apply_call_snapshot",
    "build_results",
    "launch_calls",
    "list_attempts",
    "mask_number",
    "reconcile_job",
    "resolve_by_token",
]

#: Fallback staleness window when no setting is supplied. Short enough
#: that a watching user sees movement, long enough that a page of fifty
#: calls does not hammer the upstream on every poll.
_SYNC_INTERVAL = timedelta(seconds=8)

#: Statuses that mean this candidate has already been dialled for this
#: role. NOT_CONNECTED and FAILED are deliberately absent: an unanswered
#: or failed attempt is exactly the case where calling again is right.
_ALREADY_DIALLED = (
    "NOT_STARTED",
    "SCHEDULED",
    "INITIATED",
    "RINGING",
    "IN_PROGRESS",
    "COMPLETED",
)

#: Calls older than this are left alone even if non-terminal. Something
#: that has not moved in hours is stuck, not in flight, and polling it
#: forever would waste quota indefinitely.
_MAX_RECONCILE_AGE = timedelta(hours=6)


def mask_number(number: str) -> str:
    """Mask a phone number for display, keeping the last four digits.

    Recruiters need enough to recognise a row; nobody needs the whole
    number rendered in a browser or copied into a screenshot.
    """
    digits = "".join(character for character in number if character.isdigit())
    if len(digits) <= 4:
        return "•" * len(digits)
    return f"{number[:3]}•••••{digits[-4:]}"


def _new_request_id() -> str:
    """Our idempotency handle, capped at Hunar's 64 characters."""
    return f"scr-{datetime.now(UTC).strftime('%Y%m%d')}-{secrets.token_hex(8)}"


def _as_aware(value: datetime | None) -> datetime | None:
    """Treat a naive timestamp as UTC.

    Not every driver round-trips timezone information. SQLite drops it
    entirely, and some Postgres configurations return naive values for
    ``timestamptz``. Everything here is written as UTC, so assuming UTC on
    the way back is safe, and it avoids a comparison between naive and
    aware datetimes raising at exactly the moment a user is watching a
    call progress.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def launch_calls(
    session: AsyncSession,
    client: HunarClient,
    settings: Settings,
    job: Job,
    payload: LaunchRequest,
) -> LaunchReport:
    """Place a screening call for each selected candidate.

    Candidates who already have a completed call are skipped rather than
    dialled again, because being phoned twice by the same employer about
    the same role is a bad experience and wastes call minutes.
    """
    agent_id = await ensure_agent(session, client, job)

    query = select(Candidate).where(Candidate.job_id == job.id)
    if payload.candidate_ids:
        query = query.where(Candidate.id.in_(payload.candidate_ids))
    candidates = list((await session.execute(query)).scalars())

    if not candidates:
        return LaunchReport(launched=0, skipped=0)

    already_called = set(
        (
            await session.execute(
                select(CallAttempt.candidate_id).where(
                    CallAttempt.job_id == job.id,
                    CallAttempt.status.in_(_ALREADY_DIALLED),
                )
            )
        )
        .scalars()
        .all()
    )

    retry_config = (
        RetryConfig(
            max_retry_count=payload.max_retry_count,
            retry_interval_hours=payload.retry_interval_hours,
        )
        if payload.max_retry_count > 0
        else None
    )

    launched: list[CallAttempt] = []
    blocked: list[dict[str, str]] = []
    skipped = 0

    for candidate in candidates:
        if candidate.id in already_called:
            skipped += 1
            continue

        token = secrets.token_urlsafe(24)
        attempt = CallAttempt(
            job_id=job.id,
            candidate_id=candidate.id,
            request_id=_new_request_id(),
            callback_token=token,
            status=CallStatus.NOT_STARTED.value,
            submit_state=SubmitState.PENDING.value,
            max_retries=payload.max_retry_count,
        )
        session.add(attempt)
        await session.flush()

        request = CallCreate(
            callee_name=candidate.name,
            mobile_number=candidate.mobile_number,
            agent_id=agent_id,
            request_id=attempt.request_id,
            timezone=job.timezone,
            from_phone_number=job.from_phone_number,
            retry_config=retry_config,
            custom_data=custom_data_for(
                candidate_name=candidate.name,
                job_title=job.title,
                company_name=job.company_name,
                persona_name=job.persona_name,
                extra=candidate.extra,
            ),
            callback_config=CallbackConfig(
                call_status_callback_url=settings.webhook_url(token, "status"),
                call_recording_callback_url=settings.webhook_url(token, "recording"),
                call_result_callback_url=settings.webhook_url(token, "result"),
                call_summary_callback_url=settings.webhook_url(token, "summary"),
            ),
        )

        try:
            placed = await client.create_call(request)
        except HunarTransportError as exc:
            # The dangerous case. The request may have been processed even
            # though we never saw a response, so it is parked for the
            # reconciler rather than retried into a duplicate call.
            attempt.submit_state = SubmitState.UNKNOWN.value
            attempt.error_code = exc.error_code
            attempt.error_detail = exc.message
            logger.warning(
                "hiring.call_submit_unknown",
                candidate_id=str(candidate.id),
                request_id=attempt.request_id,
            )
            blocked.append({"candidate": candidate.name, "reason": "submission timed out"})
            continue
        except HunarValidationError as exc:
            # Usually telephony refusing a specific number. Surfaced
            # verbatim, because only the operator can act on it.
            attempt.submit_state = SubmitState.FAILED.value
            attempt.status = CallStatus.FAILED.value
            attempt.error_code = exc.error_code
            attempt.error_detail = exc.message
            blocked.append({"candidate": candidate.name, "reason": exc.message})
            continue
        except HunarError as exc:
            attempt.submit_state = SubmitState.FAILED.value
            attempt.status = CallStatus.FAILED.value
            attempt.error_code = exc.error_code
            attempt.error_detail = exc.message
            blocked.append({"candidate": candidate.name, "reason": exc.message})
            continue

        attempt.submit_state = SubmitState.SUBMITTED.value
        attempt.hunar_call_id = placed.id
        attempt.status = placed.status.value
        attempt.last_synced_at = datetime.now(UTC)
        launched.append(attempt)

    if launched:
        job.status = JobStatus.CALLING.value

    await session.flush()
    logger.info(
        "hiring.calls_launched",
        job_id=str(job.id),
        launched=len(launched),
        skipped=skipped,
        blocked=len(blocked),
    )

    return LaunchReport(
        launched=len(launched),
        skipped=skipped,
        blocked=blocked,
        calls=[CallAttemptOut.model_validate(attempt) for attempt in launched],
    )


def _rescore(attempt: CallAttempt, job: Job) -> None:
    """Normalise and score one attempt's answers in place."""
    field_spec = [FieldSpec.model_validate(field) for field in job.field_spec]
    attempt.normalized_result = normalize_result(attempt.raw_result, field_spec)

    questions = [QuestionOut.model_validate(question) for question in job.questions]
    outcome = score_candidate(attempt.normalized_result, questions)

    attempt.score = outcome.score
    attempt.score_breakdown = outcome.as_dict()
    attempt.disqualified = outcome.disqualified
    attempt.disqualified_reason = outcome.reason


async def apply_call_snapshot(
    session: AsyncSession, job: Job, attempt: CallAttempt, snapshot: HunarCall
) -> None:
    """Update an attempt from an authoritative call object.

    Applied monotonically. A call that has reached a terminal status is
    never moved backwards, and a recording or result already held is never
    replaced with nothing. Both matter because the four webhook events
    arrive in no guaranteed order and the reconciler can interleave with
    them.
    """
    already_terminal = attempt.status in {status.value for status in TERMINAL_STATUSES}
    incoming_terminal = snapshot.status in TERMINAL_STATUSES

    if not already_terminal or incoming_terminal:
        attempt.status = snapshot.status.value
        attempt.lifecycle_status = snapshot.lifecycle_status.value

    # Records the upstream id the first time we learn it, which is how an
    # attempt whose POST timed out finally gets linked to its real call.
    if attempt.hunar_call_id is None:
        attempt.hunar_call_id = snapshot.id

    if snapshot.engagement_status is not None:
        attempt.engagement_status = snapshot.engagement_status.value
    if snapshot.answered_by:
        attempt.answered_by = snapshot.answered_by
    if snapshot.call_ended_by:
        attempt.call_ended_by = snapshot.call_ended_by
    if snapshot.recording_url:
        attempt.recording_url = snapshot.recording_url
    if snapshot.duration_seconds is not None:
        attempt.duration_seconds = snapshot.duration_seconds
    if snapshot.user_speech_duration is not None:
        attempt.user_speech_duration = snapshot.user_speech_duration
    if snapshot.retry_count is not None:
        attempt.retry_count = snapshot.retry_count

    if snapshot.result:
        attempt.raw_result = snapshot.result
        _rescore(attempt, job)

    attempt.submit_state = SubmitState.SUBMITTED.value
    attempt.last_synced_at = datetime.now(UTC)
    await session.flush()


async def reconcile_job(
    session: AsyncSession,
    client: HunarClient,
    job: Job,
    *,
    force: bool = False,
    min_interval: timedelta | None = None,
) -> int:
    """Refresh non-terminal calls for a job from the upstream API.

    This is what makes live status work at all, since Hunar only pushes a
    webhook once a call has finished. It also recovers attempts whose
    submission timed out, by matching on ``request_id`` while paging.

    Returns the number of attempts updated.
    """
    if job.hunar_agent_id is None:
        return 0

    cutoff = datetime.now(UTC) - (min_interval or _SYNC_INTERVAL)
    horizon = datetime.now(UTC) - _MAX_RECONCILE_AGE
    terminal = {status.value for status in TERMINAL_STATUSES}

    pending = list(
        (
            await session.execute(
                select(CallAttempt).where(
                    CallAttempt.job_id == job.id,
                    CallAttempt.status.notin_(terminal),
                    CallAttempt.created_at >= horizon,
                )
            )
        )
        .scalars()
        .all()
    )
    if not pending:
        return 0

    if not force:
        due = [
            attempt
            for attempt in pending
            if (synced := _as_aware(attempt.last_synced_at)) is None or synced < cutoff
        ]
        if not due:
            return 0

    by_call_id = {a.hunar_call_id: a for a in pending if a.hunar_call_id is not None}
    by_request_id = {a.request_id: a for a in pending}

    updated = 0
    page = 1
    while page <= 10:  # 2,000 calls; far beyond any realistic single role
        try:
            listing = await client.list_calls(
                agent_id=[job.hunar_agent_id], page=page, page_size=200
            )
        except HunarError as exc:
            logger.warning("hiring.reconcile_failed", job_id=str(job.id), error=exc.message)
            break

        for snapshot in listing.results:
            attempt = by_call_id.get(snapshot.id)
            if attempt is None and snapshot.request_id:
                # Resolves an attempt whose POST timed out: we never
                # learned its call id, but our request id came back.
                attempt = by_request_id.get(snapshot.request_id)
            if attempt is None:
                continue

            await apply_call_snapshot(session, job, attempt, snapshot)
            updated += 1

        if not listing.next:
            break
        page += 1

    if updated:
        logger.info("hiring.reconciled", job_id=str(job.id), updated=updated)

    remaining = await session.scalar(
        select(CallAttempt.id)
        .where(CallAttempt.job_id == job.id, CallAttempt.status.notin_(terminal))
        .limit(1)
    )
    if remaining is None and job.status == JobStatus.CALLING.value:
        job.status = JobStatus.DONE.value

    await session.flush()
    return updated


async def resolve_by_token(session: AsyncSession, token: str) -> CallAttempt | None:
    """Find the attempt a webhook belongs to, using its callback token.

    Correlation lives in the URL because the ``call_result_done`` payload
    contains no call identifier at all.
    """
    result = await session.execute(select(CallAttempt).where(CallAttempt.callback_token == token))
    return result.scalar_one_or_none()


async def list_attempts(session: AsyncSession, job: Job) -> list[CallAttemptOut]:
    result = await session.execute(
        select(CallAttempt)
        .where(CallAttempt.job_id == job.id)
        .order_by(CallAttempt.created_at.desc())
    )
    return [CallAttemptOut.model_validate(attempt) for attempt in result.scalars()]


async def build_results(session: AsyncSession, job: Job) -> ResultsResponse:
    """Assemble the results table: its columns and its rows together.

    The columns come from the job's stored ``field_spec``, so one endpoint
    and one frontend table serve every role no matter what it asks.
    """
    columns = [FieldSpec.model_validate(field) for field in job.field_spec]
    terminal = {status.value for status in TERMINAL_STATUSES}

    rows_result = await session.execute(
        select(Candidate, CallAttempt)
        .outerjoin(
            CallAttempt,
            (CallAttempt.candidate_id == Candidate.id) & (CallAttempt.job_id == job.id),
        )
        .where(Candidate.job_id == job.id)
        .order_by(Candidate.created_at)
    )

    rows: list[ResultRow] = []
    completed = 0
    in_progress = False

    for candidate, attempt in rows_result:
        status = attempt.status if attempt else "NOT_STARTED"
        if status == "COMPLETED":
            completed += 1
        if attempt is not None and status not in terminal:
            in_progress = True

        rows.append(
            ResultRow(
                candidate_id=candidate.id,
                candidate_name=candidate.name,
                mobile_masked=mask_number(candidate.mobile_number),
                decision=CandidateDecision(candidate.decision),
                call_id=attempt.id if attempt else None,
                status=status,
                recording_url=attempt.recording_url if attempt else None,
                duration_seconds=attempt.duration_seconds if attempt else None,
                score=attempt.score if attempt else None,
                score_breakdown=attempt.score_breakdown if attempt else None,
                disqualified=bool(attempt.disqualified) if attempt else False,
                disqualified_reason=attempt.disqualified_reason if attempt else None,
                values=(attempt.normalized_result or {}) if attempt else {},
                raw_values=(attempt.raw_result or {}) if attempt else {},
            )
        )

    return ResultsResponse(
        columns=columns,
        rows=rows,
        total=len(rows),
        completed=completed,
        in_progress=in_progress,
    )
