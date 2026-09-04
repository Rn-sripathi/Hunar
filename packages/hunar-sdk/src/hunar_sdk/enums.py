"""Enumerations mirroring the Hunar Voice Agents API vocabularies.

Every enum here is deliberately LENIENT: an unrecognised value from the
API degrades to ``UNKNOWN`` and is logged, rather than raising. Hunar can
add a status or a voice at any time, and a hard validation error would
take the entire dashboard down over one unfamiliar string arriving in a
list response.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "NON_TERMINAL_STATUSES",
    "TERMINAL_STATUSES",
    "AgentStatus",
    "CallStatus",
    "EngagementStatus",
    "Language",
    "LifecycleStatus",
    "SubmitState",
    "VoicePersona",
    "WebhookEventType",
]


class _LenientEnum(StrEnum):
    """String enum whose unknown values fall back to ``UNKNOWN``."""

    @classmethod
    def _missing_(cls, value: object) -> Self | None:
        logger.warning("hunar.unknown_enum_value", enum=cls.__name__, value=repr(value))
        fallback = cls.__members__.get("UNKNOWN")
        return fallback if isinstance(fallback, cls) else None


class CallStatus(_LenientEnum):
    """Status of a single call attempt."""

    NOT_STARTED = "NOT_STARTED"
    SCHEDULED = "SCHEDULED"
    INITIATED = "INITIATED"
    RINGING = "RINGING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    NOT_CONNECTED = "NOT_CONNECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


#: A call in one of these states will never change again, so polling can stop.
TERMINAL_STATUSES: frozenset[CallStatus] = frozenset(
    {
        CallStatus.COMPLETED,
        CallStatus.NOT_CONNECTED,
        CallStatus.FAILED,
        CallStatus.CANCELLED,
    }
)

NON_TERMINAL_STATUSES: frozenset[CallStatus] = frozenset(
    s for s in CallStatus if s not in TERMINAL_STATUSES and s is not CallStatus.UNKNOWN
)


class LifecycleStatus(_LenientEnum):
    """Overall state of a call including its retry chain."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    EXHAUSTED = "EXHAUSTED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class EngagementStatus(_LenientEnum):
    """How engaged the person on the call was."""

    ENGAGED = "ENGAGED"
    PARTIALLY_ENGAGED = "PARTIALLY_ENGAGED"
    NOT_ENGAGED = "NOT_ENGAGED"
    UNKNOWN = "UNKNOWN"


class AgentStatus(_LenientEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DRAFT = "DRAFT"
    UNKNOWN = "UNKNOWN"


class VoicePersona(_LenientEnum):
    """Voices Hunar offers. Names suggest but do not guarantee a locale,
    so listen to one test call before committing a persona to a script."""

    NEHA = "NEHA"
    ROY = "ROY"
    ZOE = "ZOE"
    SAM = "SAM"
    MIRA = "MIRA"
    EESHA = "EESHA"
    UNKNOWN = "UNKNOWN"


class Language(_LenientEnum):
    """Languages the agent can converse in.

    The exact accepted spelling is one of the day-zero spike's open
    questions; these are the values the published docs list.
    """

    ENGLISH = "ENGLISH"
    HINDI = "HINDI"
    TAMIL = "TAMIL"
    TELUGU = "TELUGU"
    KANNADA = "KANNADA"
    MARATHI = "MARATHI"
    MALAYALAM = "MALAYALAM"
    GUJARATI = "GUJARATI"
    BENGALI = "BENGALI"
    TURKISH = "TURKISH"
    ARABIC = "ARABIC"
    SPANISH = "SPANISH"
    UNKNOWN = "UNKNOWN"


class WebhookEventType(_LenientEnum):
    """Event types Hunar posts to the callback URLs.

    ``CALL_STATUS_UPDATED`` fires ONLY when an attempt reaches a terminal
    status, which is why live in-progress state has to come from polling.
    """

    CALL_STATUS_UPDATED = "call_status_updated"
    CALL_RECORDING_DONE = "call_recording_done"
    CALL_RESULT_DONE = "call_result_done"
    CALL_SUMMARY = "call_summary"
    UNKNOWN = "UNKNOWN"


class SubmitState(_LenientEnum):
    """Our own tracking of whether a call POST actually reached Hunar.

    ``UNKNOWN`` is the important one: a timed-out ``POST /calls/`` may or
    may not have created a call, and ``GET /calls/`` cannot be filtered by
    ``request_id``, so the attempt is parked here for the reconciler to
    resolve. It is never blindly retried, because a duplicate is a second
    real phone call to a real person.
    """

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
