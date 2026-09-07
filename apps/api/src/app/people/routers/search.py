"""Sourcing routes: interpret a description, then search on it.

Split into two endpoints on purpose. Extraction is free and reversible;
search costs a provider credit. Keeping them apart is what lets the
recruiter correct a bad interpretation before paying for it, and it
means a poor result set can be traced to the filters rather than blamed
on the provider.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select

from app.core.answers import mask_number
from app.core.errors import NotFoundError, ValidationFailedError
from app.deps import DbSession, SettingsDep
from app.people.models import (
    ContactAllowlistEntry,
    PeopleSearch,
    PhoneStatus,
    Prospect,
    ProspectSearchHit,
)
from app.people.providers import build_provider
from app.people.schemas import (
    ProspectOut,
    ProspectPage,
    SearchRequest,
    SearchResponse,
    SearchSummary,
)
from app.people.services.consent import (
    load_consent_context,
    seed_allowlist,
    within_calling_hours,
)
from app.people.services.extraction import ExtractionResult, extract_filters
from app.people.services.search_service import prospect_out, run_search

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
    id: uuid.UUID
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
async def policy(session: DbSession, settings: SettingsDep) -> CallingPolicyOut:
    """The consent and calling rules currently in force."""

    provider = build_provider(settings.people_provider, settings.pdl_api_key)
    reveals = provider.supports_phone_reveal()
    await provider.aclose()

    permitted = settings.allowlisted_numbers
    entries = [
        entry
        for entry in (await session.execute(select(ContactAllowlistEntry))).scalars()
        # Rows survive an entry being removed from the environment, because
        # a campaign may still reference one. Only what the environment
        # currently permits is offered for new work.
        if entry.e164 in permitted
    ]

    return CallingPolicyOut(
        provider=provider.name,
        provider_reveals_phone=reveals,
        allowlist=[
            AllowlistEntryOut(
                id=entry.id,
                e164_masked=mask_number(entry.e164),
                label=entry.label,
            )
            for entry in entries
        ],
        calling_hours_start=settings.calling_hours_start,
        calling_hours_end=settings.calling_hours_end,
        calling_timezone=settings.calling_timezone,
        within_calling_hours=within_calling_hours(settings),
    )


class LinkConsentRequest(BaseModel):
    """Record that a sourced person is reachable on a consented number."""

    allowlist_id: uuid.UUID


@router.post("/prospects/{prospect_id}/consent", response_model=ProspectOut)
async def link_consent(
    prospect_id: uuid.UUID,
    payload: LinkConsentRequest,
    session: DbSession,
    settings: SettingsDep,
) -> ProspectOut:
    """Bind a prospect to a number the operator has already consented to.

    This is the only way a sourced person becomes callable, and it is
    worth being precise about what it does and does not do.

    It does **not** grant permission. The set of dialable numbers is fixed
    by the environment and cannot be added to from inside the product.
    What this records is that a particular sourced person is reachable on
    a number that was already permitted, which is exactly what happens in
    reality: you find someone through a directory, and their consent to be
    called arrives through some other channel entirely, a reply, a
    referral, an event sign-up. Nothing about being findable implies it.

    The binding is exclusive. Two prospects pointing at one number would
    mean a campaign calling the same handset twice about the same role
    while believing it had reached two people.
    """
    prospect = await session.get(Prospect, prospect_id)
    if prospect is None:
        raise NotFoundError(f"No prospect with id {prospect_id}")

    if prospect.do_not_contact:
        raise ValidationFailedError(
            "This person asked not to be contacted again. That outlives any "
            "consent record, so the number cannot be re-linked."
        )

    entry = await session.get(ContactAllowlistEntry, payload.allowlist_id)
    if entry is None:
        raise NotFoundError("No such allowlist entry.")
    if entry.e164 not in settings.allowlisted_numbers:
        raise ValidationFailedError(
            "That number is no longer in the environment's allowlist, so it "
            "cannot be assigned to anyone new."
        )

    # Release the number from whoever holds it now.
    for holder in (
        await session.execute(select(Prospect).where(Prospect.phone_e164 == entry.e164))
    ).scalars():
        holder.phone_e164 = None
        holder.phone_status = PhoneStatus.PRESENT_MASKED.value

    prospect.phone_e164 = entry.e164
    prospect.phone_status = PhoneStatus.REVEALED.value
    await session.flush()

    return await prospect_out(session, settings, prospect)


@router.delete("/prospects/{prospect_id}/consent", response_model=ProspectOut)
async def unlink_consent(
    prospect_id: uuid.UUID, session: DbSession, settings: SettingsDep
) -> ProspectOut:
    """Withdraw a consent binding, making the person uncallable again."""
    prospect = await session.get(Prospect, prospect_id)
    if prospect is None:
        raise NotFoundError(f"No prospect with id {prospect_id}")

    prospect.phone_e164 = None
    prospect.phone_status = PhoneStatus.PRESENT_MASKED.value
    await session.flush()

    return await prospect_out(session, settings, prospect)


@router.post("/allowlist/seed", response_model=CallingPolicyOut)
async def seed(session: DbSession, settings: SettingsDep) -> CallingPolicyOut:
    """Load the environment's allowlist into the database.

    Idempotent. Runs at startup too; exposed so an operator who edits the
    environment can pick up the change without a restart.
    """
    await seed_allowlist(session, settings)
    return await policy(session, settings)


@router.get("/prospects", response_model=ProspectPage)
async def list_prospects(
    session: DbSession,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    search_id: uuid.UUID | None = None,
    consented_only: bool = False,
) -> ProspectPage:
    """Everyone sourced so far.

    This exists because prospects were persisted and then shown only in
    the response that created them. Reloading the search screen threw
    away people who had cost real provider credits to find, which is
    close to the worst possible way to lose data.

    Ordered by fit score so the list is immediately useful, and the
    consent decision is recomputed per row rather than stored, because it
    depends on the environment's allowlist and on the clock.
    """
    conditions = []
    if consented_only:
        # Cheap pre-filter. The authoritative decision is still made by
        # the consent gate below; this only avoids loading obvious misses.
        conditions.append(Prospect.phone_e164.is_not(None))

    query = select(Prospect)
    count_query = select(func.count(Prospect.id))

    if search_id is not None:
        # Revisiting one search rather than the whole pool.
        joined = ProspectSearchHit.prospect_id == Prospect.id
        query = query.join(ProspectSearchHit, joined).where(
            ProspectSearchHit.search_id == search_id
        )
        count_query = count_query.join(ProspectSearchHit, joined).where(
            ProspectSearchHit.search_id == search_id
        )

    if conditions:
        query = query.where(*conditions)
        count_query = count_query.where(*conditions)

    total = await session.scalar(count_query) or 0
    rows = list(
        (
            await session.execute(
                query.order_by(desc(Prospect.fit_score).nullslast(), Prospect.full_name)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    # One context for the whole page rather than one per row.
    consent = await load_consent_context(session, settings)
    rendered = [await prospect_out(session, settings, row, context=consent) for row in rows]
    if consented_only:
        rendered = [person for person in rendered if person.consented]

    return ProspectPage(
        prospects=rendered,
        total=total,
        consented_count=sum(1 for person in rendered if person.consented),
        limit=limit,
        offset=offset,
    )


@router.get("/searches", response_model=list[SearchSummary])
async def list_searches(session: DbSession, limit: int = 20) -> list[SearchSummary]:
    """Past searches, so a result set can be found again.

    Each one cost credits, so being able to point at the search that
    produced a list of people is both an audit trail and a way to avoid
    paying twice for the same question.
    """
    rows = (
        await session.execute(
            select(PeopleSearch).order_by(desc(PeopleSearch.created_at)).limit(limit)
        )
    ).scalars()

    summaries: list[SearchSummary] = []
    for search in rows:
        first_line = next(
            (line.strip() for line in search.jd_text.splitlines() if line.strip()), ""
        )
        if not first_line:
            # A manual search has no description, so name it by what it
            # actually asked for.
            titles = (search.filters_resolved or {}).get("titles") or []
            cities = (search.filters_resolved or {}).get("cities") or []
            parts = [", ".join(titles[:3])] if titles else []
            if cities:
                parts.append(f"in {cities[0]}")
            first_line = " ".join(parts) or "Untitled search"

        summaries.append(
            SearchSummary(
                id=search.id,
                created_at=search.created_at,
                provider=search.provider,
                provider_degraded_to=search.provider_degraded_to,
                extraction_method=search.extraction_method,
                result_count=search.result_count,
                credits_charged=search.credits_charged,
                label=first_line[:120],
            )
        )
    return summaries
