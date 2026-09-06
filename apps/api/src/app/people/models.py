"""Database models for the sourcing domain.

The shape here encodes the consent rule directly. An ``OutreachTarget``
carries a **non-nullable** foreign key to a ``ContactAllowlistEntry``, so
there is no way to create something dialable without pointing at a number
someone has explicitly consented to. A bug that bypasses the service
layer fails at insert rather than placing a call.

``Prospect`` deliberately has no plain phone column as its primary
contact route. It has a ``phone_status``, because the honest answer for
most sourced records is "a mobile exists but this plan will not show it",
and a schema that pretends otherwise invites code that assumes a number
is there.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.models import TimestampMixin, UUIDMixin
from app.db import Base, JSONVariant

__all__ = [
    "ContactAllowlistEntry",
    "DoNotContact",
    "OutreachCampaign",
    "OutreachTarget",
    "PeopleSearch",
    "PhoneStatus",
    "Prospect",
    "TargetStatus",
]


class PhoneStatus(StrEnum):
    """What we actually know about reaching a prospect by phone."""

    #: Real digits are available.
    REVEALED = "REVEALED"
    #: The provider says a mobile exists but will not return its value on
    #: this plan. The common case, and the reason for the allowlist.
    PRESENT_MASKED = "PRESENT_MASKED"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


class TargetStatus(StrEnum):
    PENDING = "PENDING"
    #: Outside calling hours; will be attempted when the window opens.
    DEFERRED = "DEFERRED"
    CALLING = "CALLING"
    DONE = "DONE"
    #: Refused by the consent gate. Always carries a reason.
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class PeopleSearch(UUIDMixin, TimestampMixin, Base):
    """One search, stored so it can be reproduced and audited.

    The literal provider query is kept alongside the job description that
    produced it. Without that, a surprising result set is impossible to
    explain, and a provider bill is impossible to attribute.
    """

    __tablename__ = "people_search"

    jd_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    jd_sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    #: llm, heuristic or manual, so a poor result set can be traced to how
    #: the filters were derived rather than blamed on the provider.
    extraction_method: Mapped[str] = mapped_column(String(16), default="heuristic")
    filters_resolved: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    filters_edited: Mapped[bool] = mapped_column(Boolean, default=False)

    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_query: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    #: Set when the primary provider was unavailable and a fallback served
    #: the results. Surfaced in the UI so nobody mistakes sample data for
    #: live data.
    provider_degraded_to: Mapped[str | None] = mapped_column(String(24), nullable=True)

    result_count: Mapped[int] = mapped_column(Integer, default=0)
    credits_charged: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)

    hits: Mapped[list[ProspectSearchHit]] = relationship(
        back_populates="search", cascade="all, delete-orphan"
    )


class Prospect(UUIDMixin, TimestampMixin, Base):
    """A person a provider returned."""

    __tablename__ = "prospect"
    __table_args__ = (Index("ix_prospect_score", "fit_score"),)

    #: Provider-agnostic identity, so the same person found twice merges
    #: rather than duplicating. LinkedIn identity first, then a hashed
    #: phone, then a weak name-and-company fallback.
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    dedupe_confidence: Mapped[str] = mapped_column(String(8), default="high")

    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_person_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    headline: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    seniority: Mapped[str | None] = mapped_column(String(32), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(120), nullable=True)
    years_experience: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skills: Mapped[list[str]] = mapped_column(JSONVariant, default=list)

    location_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    location_country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    phone_status: Mapped[str] = mapped_column(
        String(16), default=PhoneStatus.UNKNOWN.value, nullable=False, index=True
    )
    #: Almost always null by design. Populated only when a provider
    #: genuinely returns digits.
    phone_e164: Mapped[str | None] = mapped_column(String(20), nullable=True)

    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: Outreach priority only. Never a hiring decision, both because that
    #: would be the wrong use of broker data and because the provider's
    #: own terms forbid it.
    fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fit_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONVariant, default=list)

    #: The provider payload after redaction, kept so a later provider swap
    #: can backfill without paying for the record again.
    raw: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    redacted_fields: Mapped[list[str]] = mapped_column(JSONVariant, default=list)

    #: Short retention: this is personal data about people who never asked
    #: to be in our database.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProspectSearchHit(UUIDMixin, Base):
    """Which search surfaced which prospect, and where it ranked."""

    __tablename__ = "prospect_search_hit"
    __table_args__ = (UniqueConstraint("search_id", "prospect_id", name="hit_once"),)

    search_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("people_search.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prospect_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prospect.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rank: Mapped[int] = mapped_column(Integer, default=0)

    search: Mapped[PeopleSearch] = relationship(back_populates="hits")


class ContactAllowlistEntry(UUIDMixin, TimestampMixin, Base):
    """A number this deployment is permitted to call.

    Seeded from the environment rather than editable in the UI. That is
    itself a security statement: a deployment cannot grow its own calling
    permissions, and adding one requires access to the environment.
    """

    __tablename__ = "contact_allowlist"

    e164: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(120), default="unlabelled")
    consent_source: Mapped[str] = mapped_column(String(64), default="env")
    consented_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DoNotContact(Base):
    """Numbers that have asked never to be called again.

    Stores a SHA-256 hash rather than the number. Honouring a suppression
    request should not require retaining the very data being suppressed,
    and a hash is enough to check membership.
    """

    __tablename__ = "dnc_number"

    e164_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="call")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class OutreachCampaign(UUIDMixin, TimestampMixin, Base):
    """One round of outreach about one role."""

    __tablename__ = "outreach_campaign"

    search_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("people_search.id", ondelete="SET NULL"), nullable=True
    )

    job_title: Mapped[str] = mapped_column(String(200), nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    job_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    work_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: One sentence, spoken aloud. Long pitches make bad phone calls.
    role_pitch: Mapped[str] = mapped_column(Text, default="")
    comp_range_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    recruiter_name: Mapped[str] = mapped_column(String(120), default="our recruiter")

    hunar_agent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    result_schema: Mapped[dict[str, Any]] = mapped_column(JSONVariant, default=dict)
    field_spec: Mapped[list[dict[str, Any]]] = mapped_column(JSONVariant, default=list)

    status: Mapped[str] = mapped_column(String(16), default="DRAFT", index=True)
    launched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    targets: Mapped[list[OutreachTarget]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class OutreachTarget(UUIDMixin, TimestampMixin, Base):
    """One prospect in one campaign, and the consent that permits calling.

    ``allowlist_id`` is NOT NULL on purpose. It is the difference between
    a policy and a guarantee: there is no way to persist a callable target
    that is not bound to a consented number.
    """

    __tablename__ = "outreach_target"
    __table_args__ = (
        UniqueConstraint("campaign_id", "prospect_id", name="target_once"),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("outreach_campaign.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prospect_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prospect.id", ondelete="CASCADE"), nullable=False, index=True
    )
    allowlist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contact_allowlist.id", ondelete="RESTRICT"), nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(16), default=TargetStatus.PENDING.value, nullable=False, index=True
    )
    block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    campaign: Mapped[OutreachCampaign] = relationship(back_populates="targets")
