"""Job and screening-question routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.deps import DbSession
from app.hiring.schemas import (
    AgentPreview,
    JobCreate,
    JobDetail,
    JobSummary,
    JobUpdate,
    QuestionInput,
)
from app.hiring.services import job_service
from app.hiring.services.prompt_builder import build_preview

router = APIRouter(prefix="/jobs")


class PreviewRequest(BaseModel):
    """Ask what the agent would be told, without saving anything.

    Powers the live preview in the create-job form. Making the generated
    script visible before any call is placed is what lets a recruiter
    catch a bad prompt themselves, rather than hearing about it from a
    confused candidate.
    """

    title: str = Field(default="the role", max_length=200)
    company_name: str = Field(default="the company", max_length=200)
    location: str | None = None
    description_raw: str = Field(default="", max_length=20_000)
    language: str = "ENGLISH"
    questions: list[QuestionInput] = Field(min_length=1, max_length=15)


@router.post("/preview", response_model=AgentPreview)
async def preview_agent(payload: PreviewRequest) -> AgentPreview:
    """Render the agent script and extraction schema for a draft role."""
    return build_preview(
        title=payload.title,
        company_name=payload.company_name,
        location=payload.location,
        description_raw=payload.description_raw,
        questions=payload.questions,
        language=payload.language,
    )


@router.get("", response_model=list[JobSummary])
async def list_jobs(session: DbSession) -> list[JobSummary]:
    return await job_service.list_jobs(session)


@router.post("", response_model=JobDetail, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreate, session: DbSession) -> JobDetail:
    job = await job_service.create_job(session, payload)
    return await job_service.job_detail(session, job)


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(job_id: uuid.UUID, session: DbSession) -> JobDetail:
    job = await job_service.get_job(session, job_id)
    return await job_service.job_detail(session, job)


@router.patch("/{job_id}", response_model=JobDetail)
async def update_job(job_id: uuid.UUID, payload: JobUpdate, session: DbSession) -> JobDetail:
    job = await job_service.get_job(session, job_id)
    job = await job_service.update_job(session, job, payload)
    return await job_service.job_detail(session, job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_job(job_id: uuid.UUID, session: DbSession) -> None:
    """Archive rather than delete. Screening results describe real calls."""
    job = await job_service.get_job(session, job_id)
    await job_service.delete_job(session, job)


__all__ = ["router"]
