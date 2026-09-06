"""API contracts for the sourcing domain.

``SearchFilters`` is the pivot. A job description is prose; a people
search API wants structured filters. Everything upstream of the filters
is interpretation, everything downstream is a provider query, and keeping
the two apart is what lets the recruiter correct the interpretation
before a credit is spent.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.people.models import PhoneStatus, TargetStatus

__all__ = [
    "CampaignCreate",
    "CampaignDetail",
    "CampaignSummary",
    "LaunchOutreachReport",
    "ProspectOut",
    "SearchFilters",
    "SearchRequest",
    "SearchResponse",
    "TargetOut",
]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


Seniority = Literal["ic", "senior", "lead", "manager", "director", "vp", "cxo"]


class SearchFilters(BaseModel):
    """What we will actually ask the provider for.

    Deliberately provider-neutral. People Data Labs wants an
    Elasticsearch query and Apollo wants flat parameters, so each adapter
    owns that translation rather than leaking it into this shape.
    """

    titles: list[str] = Field(default_factory=list)
    excluded_titles: list[str] = Field(default_factory=list)
    seniorities: list[Seniority] = Field(default_factory=list)
    skills_required: list[str] = Field(default_factory=list)
    skills_nice: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    country: str = "india"
    industries: list[str] = Field(default_factory=list)
    company_size_bands: list[str] = Field(default_factory=list)
    exclude_companies: list[str] = Field(default_factory=list)
    min_years: int | None = Field(default=None, ge=0, le=50)
    max_years: int | None = Field(default=None, ge=0, le=50)

    #: Ask the provider only for people it believes have a mobile on file.
    #: On a free tier that is a boolean, not a number, which is exactly
    #: why the consent allowlist exists.
    require_phone: bool = True

    # ── carried to the voice agent, not to the provider ──────
    #: The role being hired for. Deliberately not ``titles[0]``: those are
    #: the titles these people hold *today*, which is usually a rung below
    #: the opening. Saying the wrong one aloud on a cold call is both
    #: confusing and slightly insulting.
    hiring_title: str = Field(default="", max_length=200)
    #: Who is hiring. A cold call that cannot name the employer is a cold
    #: call nobody should take.
    company_name: str = Field(default="", max_length=200)
    role_pitch: str = Field(default="", max_length=240)
    comp_range_text: str = ""
    work_mode: str = ""

    @field_validator("titles", "skills_required", "skills_nice", "cities", "industries")
    @classmethod
    def _tidy(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            cleaned = item.strip()
            if cleaned and cleaned.lower() not in {s.lower() for s in seen}:
                seen.append(cleaned)
        return seen[:20]


class SearchRequest(BaseModel):
    """Paste a job description, optionally with corrected filters."""

    jd_text: str = Field(min_length=1, max_length=20_000)
    #: Present on the second call, once the recruiter has edited what the
    #: extraction proposed. Absent on the first, which asks us to interpret.
    filters: SearchFilters | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 25


class ProspectOut(_Out):
    id: uuid.UUID
    full_name: str
    headline: str | None = None
    job_title: str | None = None
    seniority: str | None = None
    company_name: str | None = None
    industry: str | None = None
    years_experience: int | None = None
    skills: list[str] = Field(default_factory=list)
    location_city: str | None = None
    location_country: str | None = None
    linkedin_url: str | None = None

    phone_status: PhoneStatus
    #: Bound to a number on the consent allowlist. A durable fact about
    #: this person, and the one that decides whether they can be added to
    #: a campaign at all.
    consented: bool = False
    #: Consented *and* dialable this minute. A momentary fact: the same
    #: person is callable at noon and not at midnight. Kept separate from
    #: ``consented`` because collapsing the two makes recording a consent
    #: look like it did nothing whenever the calling window is shut.
    callable: bool = False
    #: True when the only obstacle is the clock, so the call is queued for
    #: the next window rather than refused.
    deferrable: bool = False
    #: Why not, when not. Shown in the table rather than hidden.
    not_callable_reason: str | None = None

    do_not_contact: bool = False
    fit_score: int | None = None
    fit_reasons: list[dict[str, Any]] = Field(default_factory=list)


class SearchResponse(BaseModel):
    """Everything the search screen needs, including how it was derived."""

    search_id: uuid.UUID
    provider: str
    #: Set when the primary provider was unavailable and something else
    #: served the results. Surfaced so nobody mistakes sample data for live.
    provider_degraded_to: str | None = None
    extraction_method: Literal["llm", "heuristic", "manual"]

    filters: SearchFilters
    #: The literal query sent upstream, shown so a surprising result set
    #: can be explained rather than argued about.
    provider_query: dict[str, Any] = Field(default_factory=dict)

    prospects: list[ProspectOut] = Field(default_factory=list)
    total_estimated: int | None = None
    credits_charged: int = 0
    cache_hit: bool = False
    #: How many of the results this deployment is permitted to call.
    callable_count: int = 0
    note: str | None = None


class CampaignCreate(BaseModel):
    """Start an outreach round against a set of prospects."""

    search_id: uuid.UUID | None = None
    prospect_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)

    job_title: str = Field(min_length=2, max_length=200)
    company_name: str = Field(min_length=1, max_length=200)
    job_city: str | None = Field(default=None, max_length=120)
    work_mode: str | None = Field(default=None, max_length=40)
    #: Spoken aloud. Capped hard, because a long pitch makes a bad call.
    role_pitch: str = Field(min_length=3, max_length=240)
    comp_range_text: str | None = Field(default=None, max_length=120)
    recruiter_name: str = Field(default="our recruiter", max_length=120)


class TargetOut(_Out):
    id: uuid.UUID
    prospect_id: uuid.UUID
    prospect_name: str = ""
    #: Masked. The full number never reaches the browser.
    mobile_masked: str = ""
    allowlist_label: str = ""

    status: TargetStatus
    block_reason: str | None = None
    next_attempt_at: datetime | None = None

    call_status: str = "NOT_STARTED"
    recording_url: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    raw_values: dict[str, Any] = Field(default_factory=dict)


class CampaignSummary(_Out):
    id: uuid.UUID
    job_title: str
    company_name: str
    job_city: str | None = None
    status: str
    created_at: datetime
    launched_at: datetime | None = None

    target_count: int = 0
    completed_count: int = 0
    interested_count: int = 0
    blocked_count: int = 0


class CampaignDetail(CampaignSummary):
    role_pitch: str = ""
    comp_range_text: str | None = None
    recruiter_name: str = ""
    work_mode: str | None = None

    columns: list[dict[str, Any]] = Field(default_factory=list)
    targets: list[TargetOut] = Field(default_factory=list)
    in_progress: bool = False
    #: The literal opening line, so an operator can read what will be said
    #: before it is said to a stranger.
    script_preview: dict[str, str] = Field(default_factory=dict)


class LaunchOutreachReport(BaseModel):
    launched: int
    #: Refused by the consent gate, each with its reason. Shown, never
    #: hidden: demonstrating the gate firing is the point of having it.
    blocked: list[dict[str, str]] = Field(default_factory=list)
    deferred: int = 0
    #: Submitted, but the response never arrived. These may be ringing.
    #: Reported separately from launched because we genuinely do not know,
    #: and never retried, because a duplicate is a second cold call to a
    #: stranger. The reconciler resolves them.
    unresolved: int = 0
    targets: list[TargetOut] = Field(default_factory=list)
