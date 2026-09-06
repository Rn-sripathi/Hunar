"""API contracts for the hiring domain.

``FieldSpec`` is the important type here. Because every job extracts a
different set of answers, the results table cannot have fixed columns.
The API therefore returns the column definitions alongside the rows, and
the frontend builds its table from them at runtime. One endpoint shape
serves every job, and adding a question to a role never needs a frontend
change or a migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.hiring.models import AnswerType, CandidateDecision, JobStatus
from hunar_sdk.models import normalize_e164

__all__ = [
    "AgentPreview",
    "CallAttemptOut",
    "CandidateCreate",
    "CandidateImportReport",
    "CandidateOut",
    "DecisionUpdate",
    "FieldSpec",
    "JobCreate",
    "JobDetail",
    "JobSummary",
    "JobUpdate",
    "LaunchReport",
    "LaunchRequest",
    "QuestionInput",
    "QuestionOut",
    "ResultRow",
    "ResultsResponse",
]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ─────────────────────────────────────────────────────────────
# Screening questions
# ─────────────────────────────────────────────────────────────
class QuestionInput(BaseModel):
    """One question as the recruiter defines it."""

    #: Spoken to the candidate, so it should read like a sentence.
    text: str = Field(min_length=3, max_length=500)
    #: Column header in the results table.
    label: str = Field(min_length=1, max_length=120)
    answer_type: AnswerType = AnswerType.STRING

    #: Required when ``answer_type`` is ENUM, ignored otherwise.
    enum_options: list[str] | None = None

    #: Extra extraction instruction. Folded into ``result_prompt``, which
    #: is the only place nuance can live since ``result_schema`` holds no
    #: descriptions.
    extraction_hint: str | None = Field(default=None, max_length=500)

    #: Relative importance when scoring. Zero means informational only.
    weight: Annotated[float, Field(ge=0, le=10)] = 1.0

    #: A hard requirement. Failing it disqualifies rather than lowering
    #: the score, and the reason is always shown.
    is_knockout: bool = False

    #: How the answer is judged, e.g. ``{"op": "gte", "value": 2}`` or
    #: ``{"op": "is_true"}`` or ``{"op": "in", "value": ["Yes"]}``.
    scoring_rule: dict[str, Any] | None = None

    @field_validator("enum_options")
    @classmethod
    def _clean_options(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [option.strip() for option in value if option.strip()]
        return cleaned or None

    @field_validator("scoring_rule")
    @classmethod
    def _validate_rule(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        allowed = {"gte", "lte", "eq", "is_true", "is_false", "in", "not_in", "contains"}
        op = value.get("op")
        if op not in allowed:
            raise ValueError(f"scoring_rule.op must be one of {sorted(allowed)}, got {op!r}")
        return value


class QuestionOut(_Out):
    id: uuid.UUID
    order_index: int
    text: str
    label: str
    field_key: str
    answer_type: AnswerType
    enum_options: list[str] | None = None
    extraction_hint: str | None = None
    weight: float
    is_knockout: bool
    scoring_rule: dict[str, Any] | None = None


# ─────────────────────────────────────────────────────────────
# Jobs
# ─────────────────────────────────────────────────────────────
class JobCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    company_name: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    description_raw: str = Field(default="", max_length=20_000)

    language: str = "ENGLISH"
    voice_persona: str = "NEHA"
    persona_name: str | None = Field(default=None, max_length=64)
    timezone: str = "Asia/Kolkata"

    questions: list[QuestionInput] = Field(min_length=1, max_length=15)

    @field_validator("questions")
    @classmethod
    def _reject_duplicate_labels(cls, value: list[QuestionInput]) -> list[QuestionInput]:
        """Two questions sharing a label would produce one merged column."""
        seen = {question.label.strip().lower() for question in value}
        if len(seen) != len(value):
            raise ValueError("each question needs a distinct label")
        return value


class JobUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    company_name: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    description_raw: str | None = Field(default=None, max_length=20_000)
    language: str | None = None
    voice_persona: str | None = None
    persona_name: str | None = Field(default=None, max_length=64)
    questions: list[QuestionInput] | None = Field(default=None, min_length=1, max_length=15)


class FieldSpec(BaseModel):
    """One column of the results table, derived from a question.

    Returned alongside the rows so the frontend can build a table for a
    schema it has never seen. ``system`` marks the fields every job gets
    regardless of what the recruiter asked.
    """

    key: str
    label: str
    answer_type: AnswerType
    enum_options: list[str] | None = None
    weight: float = 0.0
    is_knockout: bool = False
    system: bool = False


class JobSummary(_Out):
    id: uuid.UUID
    title: str
    company_name: str
    location: str | None = None
    language: str
    voice_persona: str
    status: JobStatus
    created_at: datetime

    candidate_count: int = 0
    call_count: int = 0
    completed_count: int = 0
    shortlisted_count: int = 0

    #: Calls genuinely still running. Deliberately its own field rather
    #: than `call_count - completed_count`: attempts include retries and
    #: terminal failures, so subtracting would report a finished role as
    #: having calls in flight.
    in_flight_count: int = 0


class AgentPreview(BaseModel):
    """Exactly what the voice agent will be told, shown before any call.

    This is the screen that makes the product legible: a recruiter can
    read the script and the extraction schema their job description
    produced, and correct it, rather than discovering a bad prompt from a
    confused candidate on the phone.
    """

    introduction: str
    objective: str
    agent_prompt: str
    result_prompt: str
    result_schema: dict[str, str]
    variables: list[str]


class JobDetail(JobSummary):
    description_raw: str
    persona_name: str | None = None
    timezone: str
    hunar_agent_id: uuid.UUID | None = None
    agent_synced_at: datetime | None = None
    questions: list[QuestionOut] = Field(default_factory=list)
    field_spec: list[FieldSpec] = Field(default_factory=list)
    preview: AgentPreview | None = None


# ─────────────────────────────────────────────────────────────
# Candidates
# ─────────────────────────────────────────────────────────────
class CandidateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mobile_number: str
    extra: dict[str, str] = Field(default_factory=dict)

    @field_validator("mobile_number")
    @classmethod
    def _normalise(cls, value: str) -> str:
        normalized = normalize_e164(value)
        if normalized is None:
            raise ValueError("a mobile number is required")
        return normalized


class CandidateOut(_Out):
    id: uuid.UUID
    name: str
    mobile_number: str
    #: Masked for list views. The full number is never sent to the browser.
    mobile_masked: str = ""
    source: str
    decision: CandidateDecision
    decision_note: str | None = None
    created_at: datetime


class CandidateImportReport(BaseModel):
    """Outcome of a spreadsheet import.

    Rejected rows are returned with their reason rather than silently
    dropped, because a recruiter who uploaded 200 rows and got 180
    candidates needs to know which twenty were lost and why.
    """

    imported: int
    skipped_duplicates: int
    rejected: list[dict[str, str]] = Field(default_factory=list)
    candidates: list[CandidateOut] = Field(default_factory=list)


class DecisionUpdate(BaseModel):
    decision: CandidateDecision
    note: str | None = Field(default=None, max_length=1000)


# ─────────────────────────────────────────────────────────────
# Calls and results
# ─────────────────────────────────────────────────────────────
class CallAttemptOut(_Out):
    id: uuid.UUID
    candidate_id: uuid.UUID | None = None
    hunar_call_id: uuid.UUID | None = None
    status: str
    lifecycle_status: str
    engagement_status: str | None = None
    answered_by: str | None = None
    recording_url: str | None = None
    duration_seconds: float | None = None
    raw_result: dict[str, Any] | None = None
    normalized_result: dict[str, Any] | None = None
    score: float | None = None
    score_breakdown: dict[str, Any] | None = None
    disqualified: bool = False
    disqualified_reason: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    retry_count: int = 0
    created_at: datetime
    updated_at: datetime

    @property
    def is_terminal(self) -> bool:
        return self.status in {"COMPLETED", "NOT_CONNECTED", "FAILED", "CANCELLED"}


class LaunchRequest(BaseModel):
    """Ask to place calls for a set of candidates."""

    #: Omit to call every candidate who has no completed call yet.
    candidate_ids: list[uuid.UUID] | None = None
    max_retry_count: Annotated[int, Field(ge=0, le=10)] = 0
    #: Hunar accepts only these cadences; a partial retry config is a 400.
    retry_interval_hours: Literal[0, 3, 6, 9, 12, 24] = 0


class LaunchReport(BaseModel):
    launched: int
    skipped: int
    blocked: list[dict[str, str]] = Field(default_factory=list)
    calls: list[CallAttemptOut] = Field(default_factory=list)


class ResultRow(BaseModel):
    """One candidate's screening outcome."""

    candidate_id: uuid.UUID
    candidate_name: str
    mobile_masked: str
    decision: CandidateDecision
    call_id: uuid.UUID | None = None
    status: str
    recording_url: str | None = None
    duration_seconds: float | None = None
    score: float | None = None
    score_breakdown: dict[str, Any] | None = None
    disqualified: bool = False
    disqualified_reason: str | None = None
    #: Coerced answers, keyed by ``FieldSpec.key``.
    values: dict[str, Any] = Field(default_factory=dict)
    #: The literal strings Hunar returned, shown beside the coerced values
    #: so a recruiter can always see what the candidate actually said.
    raw_values: dict[str, Any] = Field(default_factory=dict)


class ResultsResponse(BaseModel):
    """Columns and rows together, so the table can build itself."""

    columns: list[FieldSpec]
    rows: list[ResultRow]
    total: int
    completed: int
    #: True while any call is still running, which is what tells the
    #: frontend to keep polling.
    in_progress: bool = False
