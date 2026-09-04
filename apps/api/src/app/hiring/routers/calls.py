"""Call launching, live status and the results dashboard.

The read endpoints reconcile lazily: before answering, any non-terminal
call that has not been refreshed recently is re-fetched from the upstream
API. That is not an optimisation, it is the only source of live status,
because Hunar pushes a status webhook only once a call has finished.
Doing it on read rather than in a background worker also means it cannot
drift out of step with what the user is looking at, and it survives a
host that sleeps between requests.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter

from app.core.config import Settings
from app.deps import DbSession, SettingsDep, VoiceClient
from app.hiring.schemas import (
    CallAttemptOut,
    LaunchReport,
    LaunchRequest,
    ResultsResponse,
)
from app.hiring.services import call_service, job_service

router = APIRouter(prefix="/jobs/{job_id}")


def _interval(settings: Settings) -> timedelta:
    return timedelta(seconds=settings.reconcile_interval_seconds)


@router.post("/calls/launch", response_model=LaunchReport)
async def launch(
    job_id: uuid.UUID,
    payload: LaunchRequest,
    session: DbSession,
    client: VoiceClient,
    settings: SettingsDep,
) -> LaunchReport:
    """Place screening calls.

    Creates the job's voice agent on first use. Candidates who already
    have a completed or in-flight call are skipped rather than dialled
    again, and anything the telephony layer refuses comes back with its
    reason attached rather than failing the whole batch.
    """
    job = await job_service.get_job(session, job_id)
    return await call_service.launch_calls(session, client, settings, job, payload)


@router.get("/calls", response_model=list[CallAttemptOut])
async def list_calls(
    job_id: uuid.UUID, session: DbSession, client: VoiceClient, settings: SettingsDep
) -> list[CallAttemptOut]:
    """List call attempts, refreshing any that are still in flight."""
    job = await job_service.get_job(session, job_id)
    await call_service.reconcile_job(session, client, job, min_interval=_interval(settings))
    return await call_service.list_attempts(session, job)


@router.post("/calls/refresh", response_model=list[CallAttemptOut])
async def refresh_calls(
    job_id: uuid.UUID, session: DbSession, client: VoiceClient
) -> list[CallAttemptOut]:
    """Force a refresh, ignoring the usual staleness interval.

    Exists for the operator who is watching a call and wants an answer
    now rather than on the next poll.
    """
    job = await job_service.get_job(session, job_id)
    await call_service.reconcile_job(session, client, job, force=True)
    return await call_service.list_attempts(session, job)


@router.get("/results", response_model=ResultsResponse)
async def results(
    job_id: uuid.UUID, session: DbSession, client: VoiceClient, settings: SettingsDep
) -> ResultsResponse:
    """Return the results table's columns and rows together.

    Columns come from the job's stored field specification, so a single
    endpoint and a single frontend table render every role no matter what
    it asks. ``in_progress`` tells the client whether to keep polling.
    """
    job = await job_service.get_job(session, job_id)
    await call_service.reconcile_job(session, client, job, min_interval=_interval(settings))
    return await call_service.build_results(session, job)


__all__ = ["router"]
