"""Database models for the hiring domain.

The central design tension: Hunar's ``result_schema`` is a flat mapping of
field name to a type-hint string, with nowhere to record what a field
means, how it should be rendered, or how much it matters when scoring. So
each job stores two related structures.

``result_schema`` is exactly what was sent to Hunar, kept verbatim so a
stored job can be reproduced or replayed. ``field_spec`` is our own
richer description of the same fields, carrying the label, answer type,
enum options and scoring weight. The dashboard builds its columns from
``field_spec``, which is what lets one table render every job's differently
shaped answers without a schema migration per role.

Both are snapshots. Editing a job's questions after calls exist creates a
new agent rather than mutating the old one, so historical results keep
rendering against the schema they were actually extracted with.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.models import CallAttempt, TimestampMixin, UUIDMixin
from app.db import Base, JSONVariant

__all__ = [
    "AnswerType",
    "Candidate",
    "CandidateDecision",
    "CandidateSource",
    "Job",
    "JobStatus",
    "ScreeningQuestion",
]


class JobStatus(StrEnum):
    """Where a role is in its screening lifecycle."""

    DRAFT = "DRAFT"
    #: An agent exists upstream and candidates may be called.
    READY = "READY"
    CALLING = "CALLING"
    DONE = "DONE"
    ARCHIVED = "ARCHIVED"


class AnswerType(StrEnum):
    """How an answer is coerced, rendered and scored.

    Hunar returns every value as a string, so this is our declaration of
    what the string is meant to represent. It drives the normaliser, the
    column renderer and the scoring rules from one place.
    """

    BOOLEAN = "BOOLEAN"
    NUMBER = "NUMBER"
    STRING = "STRING"
    ENUM = "ENUM"


class CandidateDecision(StrEnum):
    PENDING = "PENDING"
    SHORTLISTED = "SHORTLISTED"
    REJECTED = "REJECTED"


class CandidateSource(StrEnum):
    MANUAL = "MANUAL"
    CSV = "CSV"
    DEMO = "DEMO"


class Job(UUIDMixin, TimestampMixin, Base):
    """A role being recruited for, and the voice agent that screens for it."""

    __tablename__ = "job"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)

    #: The recruiter's job description, exactly as pasted. Braces are
    #: stripped only when it is rendered into a prompt, never here, so the
    #: original text stays readable and editable.
    description_raw: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # ── voice configuration ──────────────────────────────────
    language: Mapped[str] = mapped_column(String(32), default="ENGLISH", nullable=False)
    voice_persona: Mapped[str] = mapped_column(String(32), default="NEHA", nullable=False)
    persona_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    from_phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", nullable=False)

    # ── the generated agent ──────────────────────────────────
    hunar_agent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    hunar_agent_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Stored so the create-job screen can show exactly what the agent was
    #: told before a single call is placed. Making the translation from a
    #: job description into a voice script visible is what turns this from
    #: a black box into something a recruiter can correct.
    agent_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    introduction: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Sent to Hunar verbatim: ``{"years_experience": "number", ...}``.
    result_schema: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict, nullable=False)
    #: Our richer view of the same fields, which the dashboard renders from.
    field_spec: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONVariant, default=list, nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(16), default=JobStatus.DRAFT.value, nullable=False, index=True
    )

    questions: Mapped[list[ScreeningQuestion]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="ScreeningQuestion.order_index",
        lazy="selectin",
    )
    candidates: Mapped[list[Candidate]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    call_attempts: Mapped[list[CallAttempt]] = relationship(
        cascade="all, delete-orphan",
        primaryjoin="Job.id == CallAttempt.job_id",
        foreign_keys="CallAttempt.job_id",
        viewonly=False,
    )


class ScreeningQuestion(UUIDMixin, TimestampMixin, Base):
    """One thing the agent should find out, and what it is worth."""

    __tablename__ = "screening_question"
    __table_args__ = (
        # `field_key` becomes a result_schema key and a dashboard column, so
        # it has to be unique within the job or answers would collide.
        UniqueConstraint("job_id", "field_key", name="field_key_per_job"),
        Index("ix_screening_question_order", "job_id", "order_index"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: The question in the recruiter's words, spoken by the agent.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Short label for the dashboard column header.
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    #: snake_case identifier used in `result_schema` and the results table.
    field_key: Mapped[str] = mapped_column(String(64), nullable=False)

    answer_type: Mapped[str] = mapped_column(
        String(16), default=AnswerType.STRING.value, nullable=False
    )
    enum_options: Mapped[list[str] | None] = mapped_column(JSONVariant, nullable=True)

    #: Extra instruction folded into `result_prompt`. This is where all
    #: extraction nuance has to live, because `result_schema` has no room
    #: for a description.
    extraction_hint: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── scoring ──────────────────────────────────────────────
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    #: A knockout is a hard requirement. Failing it disqualifies rather
    #: than merely lowering the score, and the recruiter is always shown
    #: the reason, because a silent rejection is indefensible.
    is_knockout: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: How the answer is judged, e.g. ``{"op": "gte", "value": 2}``.
    scoring_rule: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant, nullable=True)

    job: Mapped[Job] = relationship(back_populates="questions")


class Candidate(UUIDMixin, TimestampMixin, Base):
    """Someone who applied for the role and may be screened by phone."""

    __tablename__ = "candidate"
    __table_args__ = (
        # Prevents one careless spreadsheet paste from dialling the same
        # person twice for the same role.
        UniqueConstraint("job_id", "mobile_number", name="candidate_per_job"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    #: Stored in E.164. Masked in list views and never logged in full.
    mobile_number: Mapped[str] = mapped_column(String(20), nullable=False)
    #: What the operator actually typed, kept so an import error is
    #: traceable back to the row that caused it.
    raw_mobile: Mapped[str | None] = mapped_column(String(40), nullable=True)

    source: Mapped[str] = mapped_column(
        String(16), default=CandidateSource.MANUAL.value, nullable=False
    )
    #: Unmapped spreadsheet columns, passed through as `custom_data` so a
    #: prompt can reference them.
    extra: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict, nullable=False)

    decision: Mapped[str] = mapped_column(
        String(16), default=CandidateDecision.PENDING.value, nullable=False, index=True
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[Job] = relationship(back_populates="candidates")
    call_attempts: Mapped[list[CallAttempt]] = relationship(
        cascade="all, delete-orphan",
        primaryjoin="Candidate.id == CallAttempt.candidate_id",
        foreign_keys="CallAttempt.candidate_id",
        order_by="CallAttempt.created_at",
    )
