"""Pydantic models mirroring the Hunar Voice Agents API payloads.

The request models enforce Hunar's documented constraints *locally*, before
a request is ever sent. That is deliberate: call minutes and API quota are
finite and the assignment key is short-lived, so a validation error we can
detect ourselves should never cost a round trip, and a 402 should never be
triggered by a request we knew was malformed.

Several constraints are non-obvious and are enforced here because the API
returns an opaque 400 for them:

* ``retry_interval_hours`` accepts only 0, 3, 6, 9, 12 or 24.
* A partial ``retry_config`` is rejected, so both fields are required together.
* ``guardrails`` needs at least three distinct allowed days and a calling
  window at least three hours wide.
* Callback URLs must be HTTPS.
"""

from __future__ import annotations

import re
from datetime import datetime, time
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    AgentStatus,
    CallStatus,
    EngagementStatus,
    Language,
    LifecycleStatus,
    VoicePersona,
)

__all__ = [
    "Agent",
    "AgentCreate",
    "AgentUpdate",
    "BulkCallCreate",
    "BulkCallRecipient",
    "BulkCallResult",
    "Call",
    "CallCreate",
    "CallbackConfig",
    "Guardrails",
    "Page",
    "PhoneNumber",
    "RetryConfig",
    "normalize_e164",
]

#: Hunar rejects any other cadence with an opaque 400.
ALLOWED_RETRY_INTERVAL_HOURS: frozenset[int] = frozenset({0, 3, 6, 9, 12, 24})

_WEEKDAYS: frozenset[str] = frozenset({"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"})
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

MIN_GUARDRAIL_DAYS = 3
MIN_GUARDRAIL_WINDOW_HOURS = 3


def normalize_e164(value: str | None) -> str | None:
    """Validate and normalise a phone number to bare E.164.

    Hunar expects ``+919876543210``. Operators paste numbers with spaces,
    hyphens and brackets, so those are stripped rather than rejected, but
    anything that is not recognisably E.164 fails here instead of costing
    a round trip and an opaque 400.
    """
    if value is None:
        return None
    candidate = re.sub(r"[\s\-()]", "", value.strip())
    if not _E164.match(candidate):
        raise ValueError(f"expected an E.164 number such as +919876543210, got {value!r}")
    return candidate


class _Base(BaseModel):
    """Base for API-facing models.

    ``extra="allow"`` on responses is intentional. Hunar adds fields over
    time and an unrecognised key must never break deserialisation of a
    payload we otherwise understand.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class _StrictBase(BaseModel):
    """Base for request models, where an unexpected field is our own bug."""

    model_config = ConfigDict(extra="forbid")


# ─────────────────────────────────────────────────────────────
# Pagination
# ─────────────────────────────────────────────────────────────
class Page[T](_Base):
    """One page of a list response."""

    results: list[T] = Field(default_factory=list)
    count: int | None = None
    next: str | None = None
    previous: str | None = None


# ─────────────────────────────────────────────────────────────
# Agents
# ─────────────────────────────────────────────────────────────
class AgentCreate(_StrictBase):
    """Request body for ``POST /agents/``.

    ``result_schema`` is the heart of the product: a flat mapping of field
    name to a type-hint string, for example ``{"interested": "boolean"}``.
    It is NOT JSON Schema, it carries no descriptions, and it lives on the
    agent rather than the call. Two consequences follow. Each job role
    needs its own agent, because the schema cannot vary per call. And all
    extraction nuance has to be expressed in ``result_prompt``, since the
    schema has nowhere to put it.
    """

    name: str = Field(min_length=3, max_length=64)
    voice_persona: VoicePersona
    agent_prompt: str = Field(min_length=3)
    objective: str = Field(min_length=3)
    introduction: str = Field(min_length=3)
    result_prompt: str = Field(min_length=3)
    result_schema: dict[str, str] = Field(min_length=1)
    language: Language = Language.ENGLISH
    persona_name: str | None = Field(default=None, min_length=3, max_length=64)

    @field_validator("result_schema")
    @classmethod
    def _validate_result_schema(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            if not key or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
                raise ValueError(
                    f"result_schema key {key!r} must be snake_case, start with a letter "
                    "and be at most 64 characters"
                )
        return value


class AgentUpdate(_StrictBase):
    """Request body for ``PUT /agents/{id}/``.

    Only safe while a job has no calls yet. Once results exist, editing the
    agent would silently change the meaning of stored answers, so the app
    creates a replacement agent instead and keeps the old schema snapshot.
    """

    name: str | None = Field(default=None, min_length=3, max_length=64)
    voice_persona: VoicePersona | None = None
    agent_prompt: str | None = Field(default=None, min_length=3)
    objective: str | None = Field(default=None, min_length=3)
    introduction: str | None = Field(default=None, min_length=3)
    result_prompt: str | None = Field(default=None, min_length=3)
    result_schema: dict[str, str] | None = None
    language: Language | None = None
    persona_name: str | None = Field(default=None, min_length=3, max_length=64)


class Agent(_Base):
    """An agent as returned by the API."""

    id: UUID
    name: str
    voice_persona: VoicePersona = VoicePersona.UNKNOWN
    language: Language = Language.UNKNOWN
    agent_prompt: str | None = None
    objective: str | None = None
    introduction: str | None = None
    result_prompt: str | None = None
    result_schema: dict[str, Any] = Field(default_factory=dict)
    persona_name: str | None = None
    status: AgentStatus = AgentStatus.UNKNOWN
    agent_code: str | None = None
    custom_variables: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ─────────────────────────────────────────────────────────────
# Call configuration
# ─────────────────────────────────────────────────────────────
class CallbackConfig(_StrictBase):
    """Per-call webhook URLs.

    Because these are supplied per call rather than registered globally,
    an opaque correlation token can be embedded in the path. That is what
    makes the ``call_result_done`` event usable at all, since its payload
    carries no call identifier.
    """

    call_status_callback_url: str | None = Field(default=None, max_length=2083)
    call_recording_callback_url: str | None = Field(default=None, max_length=2083)
    call_result_callback_url: str | None = Field(default=None, max_length=2083)
    call_summary_callback_url: str | None = Field(default=None, max_length=2083)

    @field_validator("*")
    @classmethod
    def _must_be_https(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("Hunar requires callback URLs to be HTTPS")
        return value


class RetryConfig(_StrictBase):
    """Automatic redial policy. Both fields are required together."""

    max_retry_count: int = Field(ge=0, le=10)
    retry_interval_hours: int

    @field_validator("retry_interval_hours")
    @classmethod
    def _validate_interval(cls, value: int) -> int:
        if value not in ALLOWED_RETRY_INTERVAL_HOURS:
            allowed = ", ".join(str(v) for v in sorted(ALLOWED_RETRY_INTERVAL_HOURS))
            raise ValueError(f"retry_interval_hours must be one of {allowed}")
        return value


class Guardrails(_StrictBase):
    """When Hunar is permitted to place the call.

    A second, independent calling-hours check runs in our own service layer
    immediately before each call, because a campaign launched near the end
    of the window can still be draining after it closes.
    """

    allowed_days: list[str] = Field(min_length=MIN_GUARDRAIL_DAYS)
    earliest_call_time: str
    last_call_time: str

    @field_validator("allowed_days")
    @classmethod
    def _validate_days(cls, value: list[str]) -> list[str]:
        upper = [day.strip().upper() for day in value]
        invalid = sorted(set(upper) - _WEEKDAYS)
        if invalid:
            raise ValueError(f"unknown day codes: {', '.join(invalid)}")
        if len(set(upper)) < MIN_GUARDRAIL_DAYS:
            raise ValueError(f"Hunar requires at least {MIN_GUARDRAIL_DAYS} distinct allowed days")
        return upper

    @field_validator("earliest_call_time", "last_call_time")
    @classmethod
    def _validate_time(cls, value: str) -> str:
        if not _HHMM.match(value.strip()):
            raise ValueError(f"expected HH:MM in 24-hour form, got {value!r}")
        return value.strip()

    @model_validator(mode="after")
    def _validate_window(self) -> Guardrails:
        start = time.fromisoformat(self.earliest_call_time)
        end = time.fromisoformat(self.last_call_time)
        span_hours = (end.hour * 60 + end.minute - start.hour * 60 - start.minute) / 60
        if span_hours < MIN_GUARDRAIL_WINDOW_HOURS:
            raise ValueError(
                f"calling window must span at least {MIN_GUARDRAIL_WINDOW_HOURS} hours; "
                f"got {span_hours:.1f}"
            )
        return self


# ─────────────────────────────────────────────────────────────
# Calls
# ─────────────────────────────────────────────────────────────
class CallCreate(_StrictBase):
    """Request body for ``POST /calls/``."""

    callee_name: str = Field(min_length=1, max_length=128)
    mobile_number: str
    agent_id: UUID
    timezone: str | None = "Asia/Kolkata"
    from_phone_number: str | None = None
    callback_config: CallbackConfig | None = None
    retry_config: RetryConfig | None = None
    guardrails: Guardrails | None = None
    custom_data: dict[str, str] | None = None
    request_id: str | None = Field(default=None, max_length=64)

    @field_validator("mobile_number", "from_phone_number")
    @classmethod
    def _validate_e164(cls, value: str | None) -> str | None:
        return normalize_e164(value)


class BulkCallRecipient(_StrictBase):
    """One recipient inside a bulk request."""

    callee_name: str = Field(min_length=1, max_length=128)
    mobile_number: str
    custom_data: dict[str, str] | None = None

    @field_validator("mobile_number")
    @classmethod
    def _validate_e164(cls, value: str) -> str:
        normalized = normalize_e164(value)
        assert normalized is not None  # a required field is never None here
        return normalized


class BulkCallCreate(_StrictBase):
    """Request body for ``POST /calls/bulk/``, up to 10,000 recipients."""

    agent_id: UUID
    data: list[BulkCallRecipient] = Field(min_length=1, max_length=10_000)
    timezone: str | None = "Asia/Kolkata"
    from_phone_number: str | None = None
    callback_config: CallbackConfig | None = None
    retry_config: RetryConfig | None = None
    guardrails: Guardrails | None = None
    request_id: str | None = Field(default=None, max_length=64)
    remove_invalid_rows: bool = True
    remove_duplicate_phone_numbers: bool = True


class Call(_Base):
    """A call attempt as returned by the API.

    ``result`` mirrors the agent's ``result_schema``, but every value
    arrives as a STRING regardless of the declared type. A declared
    ``"boolean"`` comes back as ``"Yes"``. Coercing that is the job of the
    result normaliser, and the raw payload is always retained beside the
    coerced values.
    """

    id: UUID
    callee_name: str | None = None
    mobile_number: str | None = None
    agent_id: UUID | None = None
    language: Language = Language.UNKNOWN
    campaign_id: UUID | None = None
    call_type: str | None = None
    status: CallStatus = CallStatus.UNKNOWN
    lifecycle_status: LifecycleStatus = LifecycleStatus.UNKNOWN
    engagement_status: EngagementStatus = EngagementStatus.UNKNOWN
    answered_by: str | None = None
    call_ended_by: str | None = None
    duration_minutes: float | None = None
    duration_seconds: float | None = None
    user_speech_duration: float | None = None
    recording_url: str | None = None
    result: dict[str, Any] | None = None
    request_id: str | None = None
    max_retries: int | None = None
    retry_count: int | None = None
    retries_left: int | None = None
    redial_status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        """Whether this call will never change again, so polling can stop."""
        from .enums import TERMINAL_STATUSES

        return self.status in TERMINAL_STATUSES


class BulkCallResult(_Base):
    """Outcome of a bulk submission.

    Hunar drops invalid and duplicate rows by default, so the accepted
    count can be lower than what was sent. The operator is shown the
    difference rather than left to assume every row dialled.
    """

    request_id: str | None = None
    campaign_id: UUID | None = None
    total_submitted: int | None = None
    total_accepted: int | None = None
    total_rejected: int | None = None
    calls: list[Call] = Field(default_factory=list)
    rejected_rows: list[dict[str, Any]] = Field(default_factory=list)


class PhoneNumber(_Base):
    """A validated outbound caller ID available to the organisation.

    If this list is empty the organisation cannot place calls at all, which
    is why the day-zero spike checks it first: there is no workaround on
    our side.
    """

    id: UUID | None = None
    phone_number: str | None = None
    allowed_countries: list[str] = Field(default_factory=list)
    is_validated: bool | None = None
    provider: str | None = None
    status: str | None = None


CallType = Literal["OUTBOUND", "INBOUND"]
