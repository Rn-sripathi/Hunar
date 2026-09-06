"""Outreach campaigns: who gets called, and what comes back.

This module places real phone calls to people who did not apply for
anything, so it is written defensively on purpose.

**The consent gate runs twice.** Once when the campaign is built, and
again immediately before each call is placed. A campaign assembled at
18:55 can still be draining at 19:05, and calling hours are not
advisory. The second check is not redundant; it is the one that matters.

**A blocked prospect never becomes a target.** ``OutreachTarget`` carries
a non-nullable foreign key to an allowlist entry, so refusals are
reported rather than stored. There is no partially-consented row waiting
for someone to flip a status field.

**A call POST is never retried.** Same reasoning as the hiring app, with
more force: a duplicate here is a second unsolicited call to a stranger.

Everything downstream of placing the call, reconciliation, webhook
handling and answer normalisation, is the hiring app's machinery reused
without modification.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.answers import FieldSpec, mask_number, normalize_result
from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationFailedError
from app.core.models import CallAttempt
from app.people.models import (
    ContactAllowlistEntry,
    OutreachCampaign,
    OutreachTarget,
    Prospect,
    TargetStatus,
)
from app.people.schemas import (
    CampaignCreate,
    CampaignDetail,
    CampaignSummary,
    LaunchOutreachReport,
    TargetOut,
)
from app.people.services.consent import allowlist_for, record_do_not_contact
from app.people.services.reachout_agent import (
    REACHOUT_FIELD_SPEC,
    REACHOUT_RESULT_SCHEMA,
    build_reachout_agent,
    reachout_custom_data,
    script_preview,
)
from hunar_sdk import (
    CallbackConfig,
    CallCreate,
    CallStatus,
    HunarClient,
    HunarError,
    HunarTransportError,
    SubmitState,
)
from hunar_sdk.enums import TERMINAL_STATUSES

logger = structlog.get_logger(__name__)

__all__ = [
    "campaign_detail",
    "create_campaign",
    "delete_campaign",
    "get_campaign",
    "launch_campaign",
    "list_campaigns",
    "reconcile_campaign",
]

_SYNC_INTERVAL = timedelta(seconds=8)
_MAX_RECONCILE_AGE = timedelta(hours=6)

#: Statuses meaning this person has already been dialled for this
#: campaign. Absent on purpose: NOT_CONNECTED and FAILED, where trying
#: again is reasonable.
_ALREADY_DIALLED = (
    "NOT_STARTED",
    "SCHEDULED",
    "INITIATED",
    "RINGING",
    "IN_PROGRESS",
    "COMPLETED",
)


def _new_request_id() -> str:
    return f"out-{datetime.now(UTC).strftime('%Y%m%d')}-{secrets.token_hex(8)}"


async def get_campaign(session: AsyncSession, campaign_id: uuid.UUID) -> OutreachCampaign:
    campaign = await session.scalar(
        select(OutreachCampaign)
        .options(selectinload(OutreachCampaign.targets))
        .where(OutreachCampaign.id == campaign_id)
    )
    if campaign is None:
        raise NotFoundError(f"No campaign with id {campaign_id}")
    return campaign


async def create_campaign(
    session: AsyncSession, settings: Settings, payload: CampaignCreate
) -> tuple[OutreachCampaign, list[dict[str, str]]]:
    """Build a campaign, admitting only prospects the gate permits.

    Returns the campaign and the list of refusals. The refusals are
    returned rather than swallowed because the UI shows blocked people
    with their reason: a list that silently omits who it will not call
    teaches the operator nothing about why.
    """
    prospects = list(
        (
            await session.execute(select(Prospect).where(Prospect.id.in_(payload.prospect_ids)))
        ).scalars()
    )
    if not prospects:
        raise ValidationFailedError("None of those prospects exist.")

    campaign = OutreachCampaign(
        search_id=payload.search_id,
        job_title=payload.job_title,
        company_name=payload.company_name,
        job_city=payload.job_city,
        work_mode=payload.work_mode,
        role_pitch=payload.role_pitch,
        comp_range_text=payload.comp_range_text,
        recruiter_name=payload.recruiter_name,
        result_schema=REACHOUT_RESULT_SCHEMA,
        field_spec=REACHOUT_FIELD_SPEC,
        status="DRAFT",
    )
    session.add(campaign)
    await session.flush()

    blocked: list[dict[str, str]] = []

    for prospect in prospects:
        decision = await allowlist_for(session, settings, prospect)

        # No allowlist entry means no target row is even possible: the
        # column is NOT NULL. The refusal is reported instead.
        if decision.allowlist_id is None:
            blocked.append({"prospect": prospect.full_name, "reason": decision.reason})
            continue

        session.add(
            OutreachTarget(
                campaign_id=campaign.id,
                prospect_id=prospect.id,
                allowlist_id=decision.allowlist_id,
                status=(
                    TargetStatus.PENDING.value if decision.allowed else TargetStatus.DEFERRED.value
                ),
                block_reason=None if decision.allowed else decision.reason,
            )
        )

    await session.flush()
    logger.info(
        "people.campaign_created",
        campaign_id=str(campaign.id),
        requested=len(prospects),
        blocked=len(blocked),
    )
    return campaign, blocked


async def _ensure_agent(
    session: AsyncSession, client: HunarClient, campaign: OutreachCampaign
) -> uuid.UUID:
    """Return the campaign's Hunar agent, creating it on first launch.

    Deferred to launch rather than creation, so drafting a campaign that
    is never run costs nothing upstream.
    """
    if campaign.hunar_agent_id is not None:
        return campaign.hunar_agent_id

    agent = await client.create_agent(
        build_reachout_agent(campaign_title=f"{campaign.job_title} at {campaign.company_name}")
    )
    campaign.hunar_agent_id = agent.id
    await session.flush()
    logger.info("people.outreach_agent_ready", campaign_id=str(campaign.id), agent_id=str(agent.id))
    return agent.id


async def launch_campaign(
    session: AsyncSession,
    client: HunarClient,
    settings: Settings,
    campaign: OutreachCampaign,
) -> LaunchOutreachReport:
    """Place a call for every target the gate still permits.

    The re-check is the point of this function. A target admitted when
    the campaign was built is verified again here, against the clock and
    the suppression list as they are right now.
    """
    agent_id = await _ensure_agent(session, client, campaign)

    already_called = set(
        (
            await session.execute(
                select(CallAttempt.outreach_target_id).where(
                    CallAttempt.campaign_id == campaign.id,
                    CallAttempt.status.in_(_ALREADY_DIALLED),
                )
            )
        )
        .scalars()
        .all()
    )

    rows = list(
        (
            await session.execute(
                select(OutreachTarget, Prospect, ContactAllowlistEntry)
                .join(Prospect, Prospect.id == OutreachTarget.prospect_id)
                .join(
                    ContactAllowlistEntry,
                    ContactAllowlistEntry.id == OutreachTarget.allowlist_id,
                )
                .where(OutreachTarget.campaign_id == campaign.id)
            )
        ).all()
    )

    deliverable = settings.webhooks_deliverable
    launched: list[OutreachTarget] = []
    blocked: list[dict[str, str]] = []
    deferred = 0

    for target, prospect, entry in rows:
        if target.id in already_called:
            continue

        # The second gate. Same function, run against the current clock
        # and the current suppression list rather than the ones that
        # applied when the campaign was assembled.
        decision = await allowlist_for(session, settings, prospect)
        if not decision.allowed:
            target.status = (
                TargetStatus.DEFERRED.value if decision.deferrable else TargetStatus.BLOCKED.value
            )
            target.block_reason = decision.reason
            if decision.deferrable:
                deferred += 1
            else:
                blocked.append({"prospect": prospect.full_name, "reason": decision.reason})
            continue

        token = secrets.token_urlsafe(24)
        attempt = CallAttempt(
            campaign_id=campaign.id,
            outreach_target_id=target.id,
            request_id=_new_request_id(),
            callback_token=token,
            status=CallStatus.NOT_STARTED.value,
            submit_state=SubmitState.PENDING.value,
        )
        session.add(attempt)
        await session.flush()

        request = CallCreate(
            callee_name=prospect.full_name,
            mobile_number=entry.e164,
            agent_id=agent_id,
            request_id=attempt.request_id,
            timezone=settings.calling_timezone,
            custom_data=reachout_custom_data(
                callee_name=prospect.full_name,
                job_title=campaign.job_title,
                company_name=campaign.company_name,
                job_city=campaign.job_city,
                work_mode=campaign.work_mode,
                role_pitch=campaign.role_pitch,
                comp_range_text=campaign.comp_range_text,
                recruiter_name=campaign.recruiter_name,
            ),
            callback_config=(
                CallbackConfig(
                    call_status_callback_url=settings.webhook_url(token, "status"),
                    call_recording_callback_url=settings.webhook_url(token, "recording"),
                    call_result_callback_url=settings.webhook_url(token, "result"),
                    call_summary_callback_url=settings.webhook_url(token, "summary"),
                )
                if deliverable
                else None
            ),
        )

        try:
            placed = await client.create_call(request)
        except HunarTransportError as exc:
            # May or may not have dialled. Parked for the reconciler, never
            # retried: a duplicate is a second cold call to a stranger.
            attempt.submit_state = SubmitState.UNKNOWN.value
            attempt.error_detail = exc.message
            target.status = TargetStatus.CALLING.value
            logger.warning("people.call_submit_unknown", target_id=str(target.id))
            continue
        except HunarError as exc:
            attempt.submit_state = SubmitState.FAILED.value
            attempt.status = CallStatus.FAILED.value
            attempt.error_code = exc.error_code
            attempt.error_detail = exc.message
            target.status = TargetStatus.FAILED.value
            target.block_reason = exc.message
            blocked.append({"prospect": prospect.full_name, "reason": exc.message})
            continue

        attempt.submit_state = SubmitState.SUBMITTED.value
        attempt.hunar_call_id = placed.id
        attempt.status = placed.status.value
        attempt.last_synced_at = datetime.now(UTC)
        target.status = TargetStatus.CALLING.value
        target.block_reason = None
        launched.append(target)

    if launched:
        campaign.status = "CALLING"
        if campaign.launched_at is None:
            campaign.launched_at = datetime.now(UTC)

    await session.flush()
    logger.info(
        "people.campaign_launched",
        campaign_id=str(campaign.id),
        launched=len(launched),
        blocked=len(blocked),
        deferred=deferred,
    )

    detail = await campaign_detail(session, campaign)
    launched_ids = {t.id for t in launched}
    return LaunchOutreachReport(
        launched=len(launched),
        blocked=blocked,
        deferred=deferred,
        targets=[t for t in detail.targets if t.id in launched_ids],
    )


async def _apply_outcome(
    session: AsyncSession, target: OutreachTarget, attempt: CallAttempt
) -> None:
    """Act on what the conversation actually recorded.

    The one consequential branch: if the person asked not to be contacted
    again, that is honoured immediately and permanently, against the
    prospect and against the number's hash. Waiting for someone to review
    it later would mean the next campaign calls them again.
    """
    values = attempt.normalized_result or {}
    if not values.get("do_not_contact"):
        return

    prospect = await session.get(Prospect, target.prospect_id)
    if prospect is not None and not prospect.do_not_contact:
        prospect.do_not_contact = True

    entry = await session.get(ContactAllowlistEntry, target.allowlist_id)
    if entry is not None:
        await record_do_not_contact(
            session, entry.e164, reason="requested during outreach call", source="call"
        )
    logger.info("people.do_not_contact_honoured", target_id=str(target.id))


async def reconcile_campaign(
    session: AsyncSession,
    client: HunarClient,
    campaign: OutreachCampaign,
    *,
    force: bool = False,
) -> int:
    """Refresh unsettled calls for a campaign.

    Same two-part definition of unsettled as the hiring app: a call that
    has not finished, or one that has finished but whose answers have not
    arrived yet, because Hunar extracts after the hangup.
    """
    if campaign.hunar_agent_id is None:
        return 0

    terminal = [status.value for status in TERMINAL_STATUSES]
    cutoff = datetime.now(UTC) - _SYNC_INTERVAL
    horizon = datetime.now(UTC) - _MAX_RECONCILE_AGE

    pending = list(
        (
            await session.execute(
                select(CallAttempt).where(
                    CallAttempt.campaign_id == campaign.id,
                    CallAttempt.created_at >= horizon,
                    (CallAttempt.status.notin_(terminal))
                    | ((CallAttempt.status == "COMPLETED") & (CallAttempt.raw_result.is_(None))),
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
            a
            for a in pending
            if a.last_synced_at is None
            or (
                a.last_synced_at.replace(tzinfo=UTC)
                if a.last_synced_at.tzinfo is None
                else a.last_synced_at
            )
            < cutoff
        ]
        if not due:
            return 0

    by_call_id = {a.hunar_call_id: a for a in pending if a.hunar_call_id is not None}
    by_request_id = {a.request_id: a for a in pending}
    targets = {
        t.id: t
        for t in (
            await session.execute(
                select(OutreachTarget).where(OutreachTarget.campaign_id == campaign.id)
            )
        ).scalars()
    }
    columns = [FieldSpec.model_validate(field) for field in campaign.field_spec]

    updated = 0
    page = 1
    while page <= 10:
        try:
            listing = await client.list_calls(
                agent_id=[campaign.hunar_agent_id], page=page, page_size=200
            )
        except HunarError as exc:
            logger.warning(
                "people.reconcile_failed", campaign_id=str(campaign.id), error=exc.message
            )
            break

        for snapshot in listing.results:
            attempt = by_call_id.get(snapshot.id) or (
                by_request_id.get(snapshot.request_id) if snapshot.request_id else None
            )
            if attempt is None:
                continue

            already_terminal = attempt.status in set(terminal)
            if not already_terminal or snapshot.status in TERMINAL_STATUSES:
                attempt.status = snapshot.status.value
                attempt.lifecycle_status = snapshot.lifecycle_status.value

            if attempt.hunar_call_id is None:
                attempt.hunar_call_id = snapshot.id
            if snapshot.recording_url:
                attempt.recording_url = snapshot.recording_url
            if snapshot.duration_seconds is not None:
                attempt.duration_seconds = snapshot.duration_seconds
            if snapshot.answered_by:
                attempt.answered_by = snapshot.answered_by
            if snapshot.result:
                attempt.raw_result = snapshot.result
                attempt.normalized_result = normalize_result(snapshot.result, columns)

            attempt.submit_state = SubmitState.SUBMITTED.value
            attempt.last_synced_at = datetime.now(UTC)

            target = targets.get(attempt.outreach_target_id or uuid.uuid4())
            if target is not None:
                if attempt.status == "COMPLETED":
                    target.status = TargetStatus.DONE.value
                    if attempt.raw_result:
                        await _apply_outcome(session, target, attempt)
                elif attempt.status in {"FAILED", "NOT_CONNECTED", "CANCELLED"}:
                    target.status = TargetStatus.FAILED.value

            updated += 1

        if not listing.next:
            break
        page += 1

    remaining = await session.scalar(
        select(CallAttempt.id)
        .where(
            CallAttempt.campaign_id == campaign.id,
            (CallAttempt.status.notin_(terminal))
            | ((CallAttempt.status == "COMPLETED") & (CallAttempt.raw_result.is_(None))),
        )
        .limit(1)
    )
    if remaining is None and campaign.status == "CALLING":
        campaign.status = "DONE"

    await session.flush()
    if updated:
        logger.info("people.reconciled", campaign_id=str(campaign.id), updated=updated)
    return updated


async def campaign_detail(session: AsyncSession, campaign: OutreachCampaign) -> CampaignDetail:
    """Assemble a campaign's full view: columns, rows and the script.

    The columns come from the stored field spec, so the same frontend
    table renders both applications' results without knowing which one it
    is looking at.
    """
    terminal = {status.value for status in TERMINAL_STATUSES}

    rows = list(
        (
            await session.execute(
                select(OutreachTarget, Prospect, ContactAllowlistEntry, CallAttempt)
                .join(Prospect, Prospect.id == OutreachTarget.prospect_id)
                .join(
                    ContactAllowlistEntry,
                    ContactAllowlistEntry.id == OutreachTarget.allowlist_id,
                )
                .outerjoin(CallAttempt, CallAttempt.outreach_target_id == OutreachTarget.id)
                .where(OutreachTarget.campaign_id == campaign.id)
                .order_by(Prospect.fit_score.desc().nullslast(), Prospect.full_name)
            )
        ).all()
    )

    targets: list[TargetOut] = []
    completed = 0
    interested = 0
    blocked = 0
    in_progress = False

    for target, prospect, entry, attempt in rows:
        status = attempt.status if attempt else "NOT_STARTED"
        values = (attempt.normalized_result or {}) if attempt else {}

        if status == "COMPLETED":
            completed += 1
        if values.get("interested"):
            interested += 1
        if target.status == TargetStatus.BLOCKED.value:
            blocked += 1
        if attempt is not None and (
            status not in terminal or (status == "COMPLETED" and not attempt.raw_result)
        ):
            in_progress = True

        targets.append(
            TargetOut(
                id=target.id,
                prospect_id=prospect.id,
                prospect_name=prospect.full_name,
                mobile_masked=mask_number(entry.e164),
                allowlist_label=entry.label,
                status=TargetStatus(target.status),
                block_reason=target.block_reason,
                next_attempt_at=target.next_attempt_at,
                call_status=status,
                recording_url=attempt.recording_url if attempt else None,
                values=values,
                raw_values=(attempt.raw_result or {}) if attempt else {},
            )
        )

    preview = script_preview(
        reachout_custom_data(
            callee_name=targets[0].prospect_name if targets else "the person you are calling",
            job_title=campaign.job_title,
            company_name=campaign.company_name,
            job_city=campaign.job_city,
            work_mode=campaign.work_mode,
            role_pitch=campaign.role_pitch,
            comp_range_text=campaign.comp_range_text,
            recruiter_name=campaign.recruiter_name,
        )
    )

    return CampaignDetail(
        id=campaign.id,
        job_title=campaign.job_title,
        company_name=campaign.company_name,
        job_city=campaign.job_city,
        status=campaign.status,
        created_at=campaign.created_at,
        launched_at=campaign.launched_at,
        target_count=len(targets),
        completed_count=completed,
        interested_count=interested,
        blocked_count=blocked,
        role_pitch=campaign.role_pitch,
        comp_range_text=campaign.comp_range_text,
        recruiter_name=campaign.recruiter_name,
        work_mode=campaign.work_mode,
        columns=list(campaign.field_spec),
        targets=targets,
        in_progress=in_progress,
        script_preview=preview,
    )


async def list_campaigns(session: AsyncSession) -> list[CampaignSummary]:
    """List campaigns with the counts their cards display."""
    target_counts = (
        select(
            OutreachTarget.campaign_id,
            func.count(OutreachTarget.id).label("n"),
            func.sum(func.cast(OutreachTarget.status == TargetStatus.BLOCKED.value, Integer)).label(
                "blocked"
            ),
        )
        .group_by(OutreachTarget.campaign_id)
        .subquery()
    )
    completed = (
        select(CallAttempt.campaign_id, func.count(CallAttempt.id).label("n"))
        .where(CallAttempt.status == "COMPLETED")
        .group_by(CallAttempt.campaign_id)
        .subquery()
    )

    result = await session.execute(
        select(
            OutreachCampaign,
            func.coalesce(target_counts.c.n, 0),
            func.coalesce(target_counts.c.blocked, 0),
            func.coalesce(completed.c.n, 0),
        )
        .outerjoin(target_counts, target_counts.c.campaign_id == OutreachCampaign.id)
        .outerjoin(completed, completed.c.campaign_id == OutreachCampaign.id)
        .order_by(OutreachCampaign.created_at.desc())
    )

    return [
        CampaignSummary(
            id=campaign.id,
            job_title=campaign.job_title,
            company_name=campaign.company_name,
            job_city=campaign.job_city,
            status=campaign.status,
            created_at=campaign.created_at,
            launched_at=campaign.launched_at,
            target_count=target_count,
            blocked_count=blocked_count,
            completed_count=completed_count,
        )
        for campaign, target_count, blocked_count, completed_count in result
    ]


async def delete_campaign(session: AsyncSession, campaign: OutreachCampaign) -> None:
    await session.delete(campaign)
    await session.flush()
