"""Sourcing routes: interpret a description, then search on it.

Split into two endpoints on purpose. Extraction is free and reversible;
search costs a provider credit. Keeping them apart is what lets the
recruiter correct a bad interpretation before paying for it, and it
means a poor result set can be traced to the filters rather than blamed
on the provider.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.deps import DbSession, SettingsDep
from app.people.schemas import SearchRequest, SearchResponse
from app.people.services.extraction import ExtractionResult, extract_filters
from app.people.services.search_service import run_search

router = APIRouter(prefix="/people")


class ExtractFiltersRequest(BaseModel):
    jd_text: str = Field(min_length=1, max_length=20_000)


@router.post("/extract-filters", response_model=ExtractionResult)
async def extract(payload: ExtractFiltersRequest, settings: SettingsDep) -> ExtractionResult:
    """Turn a job description into editable search filters.

    Spends nothing and saves nothing. ``method`` reports whether a model
    read the description or whether keyword matching filled it in, so the
    screen can say how much to trust what it is showing.
    """
    return await extract_filters(payload.jd_text, settings)


@router.post("/search", response_model=SearchResponse)
async def search(
    payload: SearchRequest, session: DbSession, settings: SettingsDep
) -> SearchResponse:
    """Search for people, rank them, and record what was asked for.

    Returns the literal provider query alongside the results. A surprising
    result set should be explainable rather than arguable.
    """
    return await run_search(session, settings, payload)


class AllowlistEntryOut(BaseModel):
    e164_masked: str
    label: str


class CallingPolicyOut(BaseModel):
    """What this deployment is permitted to do, stated plainly.

    Surfaced to the UI so the calling policy is visible in the product
    rather than buried in a README nobody opens. A recruiter looking at a
    list of people it will not call deserves to see why in the same
    screen.
    """

    provider: str
    provider_reveals_phone: bool
    allowlist: list[AllowlistEntryOut]
    calling_hours_start: str
    calling_hours_end: str
    calling_timezone: str
    within_calling_hours: bool


@router.get("/policy", response_model=CallingPolicyOut)
async def policy(settings: SettingsDep) -> CallingPolicyOut:
    """The consent and calling rules currently in force."""
    from app.people.providers import build_provider
    from app.people.services.consent import within_calling_hours

    provider = build_provider(settings.people_provider, settings.pdl_api_key)
    reveals = provider.supports_phone_reveal()
    await provider.aclose()

    return CallingPolicyOut(
        provider=provider.name,
        provider_reveals_phone=reveals,
        allowlist=[
            AllowlistEntryOut(
                e164_masked=f"{entry.e164[:3]}•••••{entry.e164[-4:]}", label=entry.label
            )
            for entry in settings.allowlist
        ],
        calling_hours_start=settings.calling_hours_start,
        calling_hours_end=settings.calling_hours_end,
        calling_timezone=settings.calling_timezone,
        within_calling_hours=within_calling_hours(settings),
    )
