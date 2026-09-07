"""Creating and maintaining jobs, and the agents that screen for them.

The one rule worth stating plainly: **a live agent is never mutated once
calls exist against it.** Changing a job's questions after candidates have
been screened would silently change the meaning of answers already
stored, because ``result_schema`` defines what each key means and the
stored results only make sense against the schema they were extracted
with. So an edit after calling creates a *new* agent, and the old results
keep rendering against the ``field_spec`` snapshot they were captured
under.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError
from app.core.models import CallAttempt
from app.hiring.models import Candidate, Job, JobStatus, ScreeningQuestion
from app.hiring.schemas import (
    FieldSpec,
    JobCreate,
    JobDetail,
    JobSummary,
    JobUpdate,
    QuestionInput,
    QuestionOut,
)
from app.hiring.services.prompt_builder import (
    assign_field_keys,
    build_agent_payload,
    build_field_spec,
    build_preview,
)
from hunar_sdk import Agent, AgentCreate, AgentUpdate, HunarClient, HunarError

logger = structlog.get_logger(__name__)

#: Statuses a call will never move out of.
_TERMINAL_STATUS_VALUES = ("COMPLETED", "NOT_CONNECTED", "FAILED", "CANCELLED")

__all__ = [
    "agent_fingerprint",
    "create_job",
    "delete_job",
    "ensure_agent",
    "get_job",
    "job_detail",
    "list_jobs",
    "update_job",
]


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    """Load a job with its questions, or raise a 404."""
    result = await session.execute(
        select(Job).options(selectinload(Job.questions)).where(Job.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise NotFoundError(f"No job with id {job_id}")
    return job


def _questions_to_inputs(job: Job) -> list[QuestionInput]:
    """Rebuild the input form of a job's stored questions."""
    return [
        QuestionInput(
            text=question.text,
            label=question.label,
            answer_type=question.answer_type,  # type: ignore[arg-type]
            enum_options=question.enum_options,
            extraction_hint=question.extraction_hint,
            weight=question.weight,
            is_knockout=question.is_knockout,
            scoring_rule=question.scoring_rule,
        )
        for question in job.questions
    ]


def _apply_questions(job: Job, questions: list[QuestionInput]) -> None:
    """Replace a job's questions and regenerate everything derived from them."""
    keys = assign_field_keys(questions)

    job.questions.clear()
    for index, (key, question) in enumerate(zip(keys, questions, strict=True)):
        job.questions.append(
            ScreeningQuestion(
                order_index=index,
                text=question.text,
                label=question.label,
                field_key=key,
                answer_type=question.answer_type.value,
                enum_options=question.enum_options,
                extraction_hint=question.extraction_hint,
                weight=question.weight,
                is_knockout=question.is_knockout,
                scoring_rule=question.scoring_rule,
            )
        )

    preview = build_preview(
        title=job.title,
        company_name=job.company_name,
        location=job.location,
        description_raw=job.description_raw,
        questions=questions,
        language=job.language,
    )
    job.agent_prompt = preview.agent_prompt
    job.objective = preview.objective
    job.introduction = preview.introduction
    job.result_prompt = preview.result_prompt
    job.result_schema = preview.result_schema
    job.field_spec = [field.model_dump(mode="json") for field in build_field_spec(questions, keys)]


async def create_job(session: AsyncSession, payload: JobCreate) -> Job:
    """Create a job and everything derived from its questions.

    The agent is not created here. It is created on first launch, so a
    recruiter can draft and revise a role without spending an upstream
    resource on a job that may never be used.
    """
    job = Job(
        title=payload.title,
        company_name=payload.company_name,
        location=payload.location,
        description_raw=payload.description_raw,
        language=payload.language,
        voice_persona=payload.voice_persona,
        persona_name=payload.persona_name,
        timezone=payload.timezone,
        status=JobStatus.DRAFT.value,
    )
    _apply_questions(job, payload.questions)

    session.add(job)
    await session.flush()
    logger.info("hiring.job_created", job_id=str(job.id), title=job.title)
    return job


async def update_job(session: AsyncSession, job: Job, payload: JobUpdate) -> Job:
    """Update a job, protecting results already collected.

    Editing the questions once calls exist would invalidate stored
    answers, so it is refused. The recruiter is told to duplicate the role
    instead, which keeps both the old results and the new script intact.
    """
    for field in ("title", "company_name", "location", "description_raw", "persona_name"):
        value = getattr(payload, field)
        if value is not None:
            setattr(job, field, value)

    if payload.language is not None:
        job.language = payload.language
    if payload.voice_persona is not None:
        job.voice_persona = payload.voice_persona

    if payload.questions is not None:
        call_count = await session.scalar(
            select(func.count(CallAttempt.id)).where(CallAttempt.job_id == job.id)
        )
        if call_count:
            raise ConflictError(
                "This role has already been used for screening calls, so its "
                "questions cannot be changed. Answers already collected are "
                "stored against the current question set and would no longer "
                "mean the same thing. Duplicate the role instead.",
                details={"call_count": call_count},
            )
        _apply_questions(job, payload.questions)
        # The upstream agent no longer matches, so it is discarded and a
        # fresh one is created on the next launch.
        job.hunar_agent_id = None
        job.agent_synced_at = None
        job.status = JobStatus.DRAFT.value

    await session.flush()
    return job


def agent_fingerprint(payload: AgentCreate) -> str:
    """A stable hash of everything that decides what the agent says.

    Covers the voice, the spoken name, the language and every prompt, so
    a change to any of them is detectable. Computed from the serialised
    payload rather than a hand-listed set of fields, because the failure
    this guards against is precisely the one where somebody changes
    prompt generation and forgets that live agents exist.
    """
    body = payload.model_dump_json(exclude_none=False)
    return hashlib.sha256(body.encode()).hexdigest()


async def ensure_agent(session: AsyncSession, client: HunarClient, job: Job) -> uuid.UUID:
    """Return this job's Hunar agent id, creating or replacing as needed.

    Idempotent, so callers can invoke it before every launch without
    worrying about duplicates. One agent per job is forced by the API:
    ``result_schema`` lives on the agent, and each role extracts different
    fields, so a shared agent could not carry per-role extraction at all.

    It also repairs an agent that has **drifted**. An agent is created
    once and then never inspected again, so when prompt generation
    changes, the existing agents keep the old wording indefinitely. That
    happened: the persona name started following the voice, but roles
    created earlier went on introducing a male voice as "Neha", and the
    only place the mistake was visible was a live phone call.

    Drift is corrected by updating the agent, never by replacing it. The
    agent id has to stay stable because reconciliation finds calls by it,
    and a fresh id orphans every call already placed for the role. This
    is the one narrow case where editing a live agent is right: the
    questions cannot have changed, because ``update_job`` refuses to
    change them once calls exist, so only the spoken fields can differ
    and none of those alter what a stored answer means.
    """
    payload: AgentCreate = build_agent_payload(
        title=job.title,
        company_name=job.company_name,
        location=job.location,
        description_raw=job.description_raw,
        questions=_questions_to_inputs(job),
        language=job.language,
        voice_persona=job.voice_persona,
        persona_name=job.persona_name,
    )
    fingerprint = agent_fingerprint(payload)

    if job.hunar_agent_id is not None:
        if job.agent_fingerprint == fingerprint:
            return job.hunar_agent_id

        # Drifted. Correct it **in place**, keeping the same agent id.
        #
        # Replacing the agent instead was the obvious first attempt and it
        # was wrong: reconciliation finds calls by agent id, so a new
        # agent orphans every call already placed for this role. The
        # symptom was completed calls that never delivered their answers,
        # which is a worse bug than the one being fixed.
        #
        # Editing a live agent is safe here specifically because
        # ``result_schema`` cannot have changed: ``update_job`` refuses to
        # edit a role's questions once calls exist, so the only fields
        # that can differ are the spoken ones, and those do not change
        # what a stored answer means.
        try:
            await client.update_agent(
                job.hunar_agent_id,
                AgentUpdate(
                    name=payload.name,
                    voice_persona=payload.voice_persona,
                    persona_name=payload.persona_name,
                    language=payload.language,
                    introduction=payload.introduction,
                    objective=payload.objective,
                    agent_prompt=payload.agent_prompt,
                    result_prompt=payload.result_prompt,
                    result_schema=payload.result_schema,
                ),
            )
        except HunarError as exc:
            # Keep using the agent we have. A stale spoken name is bad;
            # refusing to place the call at all is worse.
            logger.warning(
                "hiring.agent_drift_update_failed",
                job_id=str(job.id),
                agent_id=str(job.hunar_agent_id),
                error=exc.message,
            )
            return job.hunar_agent_id

        job.agent_fingerprint = fingerprint
        job.agent_synced_at = datetime.now(UTC)
        await session.flush()
        logger.info(
            "hiring.agent_drift_corrected",
            job_id=str(job.id),
            agent_id=str(job.hunar_agent_id),
            persona_name=payload.persona_name,
            voice_persona=payload.voice_persona.value,
        )
        return job.hunar_agent_id

    try:
        agent: Agent = await client.create_agent(payload)
    except HunarError:
        logger.exception("hiring.agent_creation_failed", job_id=str(job.id))
        raise

    job.hunar_agent_id = agent.id
    job.hunar_agent_code = agent.agent_code
    job.agent_fingerprint = fingerprint
    job.agent_synced_at = datetime.now(UTC)
    if job.status == JobStatus.DRAFT.value:
        job.status = JobStatus.READY.value

    await session.flush()
    logger.info("hiring.agent_ready", job_id=str(job.id), agent_id=str(agent.id))
    return agent.id


async def list_jobs(session: AsyncSession) -> list[JobSummary]:
    """List jobs with the counts the dashboard cards display.

    Counts are computed in the query rather than by loading collections,
    because a job with two thousand candidates should not cost two
    thousand rows to render one card.
    """
    candidates = (
        select(Candidate.job_id, func.count(Candidate.id).label("n"))
        .group_by(Candidate.job_id)
        .subquery()
    )
    shortlisted = (
        select(Candidate.job_id, func.count(Candidate.id).label("n"))
        .where(Candidate.decision == "SHORTLISTED")
        .group_by(Candidate.job_id)
        .subquery()
    )
    calls = (
        select(CallAttempt.job_id, func.count(CallAttempt.id).label("n"))
        .group_by(CallAttempt.job_id)
        .subquery()
    )
    completed = (
        select(CallAttempt.job_id, func.count(CallAttempt.id).label("n"))
        .where(CallAttempt.status == "COMPLETED")
        .group_by(CallAttempt.job_id)
        .subquery()
    )
    in_flight = (
        select(CallAttempt.job_id, func.count(CallAttempt.id).label("n"))
        .where(CallAttempt.status.notin_(_TERMINAL_STATUS_VALUES))
        .group_by(CallAttempt.job_id)
        .subquery()
    )

    rows = await session.execute(
        select(
            Job,
            func.coalesce(candidates.c.n, 0),
            func.coalesce(calls.c.n, 0),
            func.coalesce(completed.c.n, 0),
            func.coalesce(shortlisted.c.n, 0),
            func.coalesce(in_flight.c.n, 0),
        )
        .outerjoin(candidates, candidates.c.job_id == Job.id)
        .outerjoin(calls, calls.c.job_id == Job.id)
        .outerjoin(completed, completed.c.job_id == Job.id)
        .outerjoin(shortlisted, shortlisted.c.job_id == Job.id)
        .outerjoin(in_flight, in_flight.c.job_id == Job.id)
        .where(Job.status != JobStatus.ARCHIVED.value)
        .order_by(Job.created_at.desc())
    )

    counted = {
        "candidate_count",
        "call_count",
        "completed_count",
        "shortlisted_count",
        "in_flight_count",
    }
    return [
        JobSummary(
            **JobSummary.model_validate(job).model_dump(exclude=counted),
            candidate_count=candidate_count,
            call_count=call_count,
            completed_count=completed_count,
            shortlisted_count=shortlisted_count,
            in_flight_count=in_flight_count,
        )
        for (
            job,
            candidate_count,
            call_count,
            completed_count,
            shortlisted_count,
            in_flight_count,
        ) in rows
    ]


async def job_detail(session: AsyncSession, job: Job) -> JobDetail:
    """Assemble a job's full view, including the agent script preview."""
    counts = await session.execute(
        select(
            select(func.count(Candidate.id)).where(Candidate.job_id == job.id).scalar_subquery(),
            select(func.count(CallAttempt.id))
            .where(CallAttempt.job_id == job.id)
            .scalar_subquery(),
            select(func.count(CallAttempt.id))
            .where(CallAttempt.job_id == job.id, CallAttempt.status == "COMPLETED")
            .scalar_subquery(),
            select(func.count(Candidate.id))
            .where(Candidate.job_id == job.id, Candidate.decision == "SHORTLISTED")
            .scalar_subquery(),
            select(func.count(CallAttempt.id))
            .where(
                CallAttempt.job_id == job.id,
                CallAttempt.status.notin_(_TERMINAL_STATUS_VALUES),
            )
            .scalar_subquery(),
        )
    )
    (
        candidate_count,
        call_count,
        completed_count,
        shortlisted_count,
        in_flight_count,
    ) = counts.one()

    questions = _questions_to_inputs(job)
    preview = (
        build_preview(
            title=job.title,
            company_name=job.company_name,
            location=job.location,
            description_raw=job.description_raw,
            questions=questions,
            language=job.language,
        )
        if questions
        else None
    )

    return JobDetail(
        id=job.id,
        title=job.title,
        company_name=job.company_name,
        location=job.location,
        language=job.language,
        voice_persona=job.voice_persona,
        persona_name=job.persona_name,
        timezone=job.timezone,
        status=JobStatus(job.status),
        created_at=job.created_at,
        description_raw=job.description_raw,
        hunar_agent_id=job.hunar_agent_id,
        agent_synced_at=job.agent_synced_at,
        questions=[QuestionOut.model_validate(question) for question in job.questions],
        field_spec=[FieldSpec.model_validate(field) for field in job.field_spec],
        preview=preview,
        candidate_count=candidate_count,
        call_count=call_count,
        completed_count=completed_count,
        shortlisted_count=shortlisted_count,
        in_flight_count=in_flight_count,
    )


async def delete_job(session: AsyncSession, job: Job) -> None:
    """Archive a job rather than deleting it.

    Screening results describe real conversations with real people. A
    misclick should not destroy them, and an archived role can still be
    audited if a candidate asks why they were rejected.
    """
    job.status = JobStatus.ARCHIVED.value
    await session.flush()
