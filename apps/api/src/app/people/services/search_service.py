"""Running a search, ranking what comes back, and persisting it once.

Three things happen here that are worth stating plainly.

**Nothing is called from this module.** Search produces a list of people
and a fit score. Whether any of them may be phoned is a separate
question, answered by :mod:`app.people.services.consent`, and the
separation is deliberate: a module that both finds people and decides to
call them will eventually do the second because it did the first.

**Prospects are deduplicated across searches.** The same person surfaces
in two searches for related roles, and merging them means their
do-not-contact flag follows them. A suppression that only applied to one
search would be worthless.

**The score is for ordering a call list, never for a hiring decision.**
It ranks who to approach first. It is not evidence about anyone's
ability, the data behind it was bought rather than given, and the
provider's own terms forbid using it to gate employment. The reasons are
returned alongside the number so a recruiter reads why rather than
trusting a total.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.people.models import PeopleSearch, PhoneStatus, Prospect, ProspectSearchHit
from app.people.providers import build_provider
from app.people.providers.base import Prospect as ProviderProspect
from app.people.providers.pdl import PdlAuthError, PdlQuotaError
from app.people.schemas import ProspectOut, SearchFilters, SearchRequest, SearchResponse
from app.people.services.consent import allowlist_for
from app.people.services.extraction import extract_filters

logger = structlog.get_logger(__name__)

__all__ = ["run_search", "score_prospect"]

#: How long a sourced record is kept. These are people who never asked to
#: be in our database, so the default is short and the expiry is written
#: at insert rather than promised in a policy document.
_RETENTION = timedelta(days=90)

_SENIORITY_ORDER = ["ic", "senior", "lead", "manager", "director", "vp", "cxo"]


# ── ranking ──────────────────────────────────────────────────
def score_prospect(
    prospect: ProviderProspect, filters: SearchFilters
) -> tuple[int, list[dict[str, Any]]]:
    """Score a prospect for outreach priority, with its reasons.

    Every component returns both a contribution and a sentence. A score
    with no explanation invites the operator to trust it, which is the
    opposite of what a number derived from broker data deserves.
    """
    reasons: list[dict[str, Any]] = []
    earned = 0.0
    available = 0.0

    # Title match, the strongest signal that this is the right kind of person.
    if filters.titles:
        available += 35
        title = (prospect.job_title or "").lower()
        wanted = [t.lower() for t in filters.titles]
        if any(t == title for t in wanted):
            earned += 35
            reasons.append(
                {"label": "Title", "detail": f"exactly {prospect.job_title}", "points": 35}
            )
        elif any(t in title or title in t for t in wanted if t):
            earned += 24
            reasons.append(
                {"label": "Title", "detail": f"close: {prospect.job_title}", "points": 24}
            )
        else:
            reasons.append(
                {"label": "Title", "detail": f"{prospect.job_title} is off-target", "points": 0}
            )

    # Required skills, scored proportionally rather than all or nothing.
    if filters.skills_required:
        available += 30
        held = {s.lower() for s in prospect.skills}
        wanted = [s.lower() for s in filters.skills_required]
        matched = [s for s in wanted if s in held]
        share = len(matched) / len(wanted)
        points = round(30 * share)
        earned += points
        reasons.append(
            {
                "label": "Required skills",
                "detail": f"{len(matched)} of {len(wanted)}: {', '.join(matched) or 'none listed'}",
                "points": points,
            }
        )

    if filters.skills_nice:
        available += 10
        held = {s.lower() for s in prospect.skills}
        matched = [s for s in filters.skills_nice if s.lower() in held]
        points = round(10 * (len(matched) / len(filters.skills_nice)))
        earned += points
        if matched:
            reasons.append({"label": "Also has", "detail": ", ".join(matched), "points": points})

    if filters.cities:
        available += 15
        city = (prospect.location_city or "").lower()
        if any(c.lower() == city for c in filters.cities):
            earned += 15
            reasons.append(
                {"label": "Location", "detail": prospect.location_city or "", "points": 15}
            )
        else:
            reasons.append(
                {
                    "label": "Location",
                    "detail": f"{prospect.location_city or 'unknown'}, not a target city",
                    "points": 0,
                }
            )

    if filters.seniorities:
        available += 10
        level = (prospect.seniority or "").lower()
        if level in {s.lower() for s in filters.seniorities}:
            earned += 10
            reasons.append({"label": "Seniority", "detail": level, "points": 10})
        elif level in _SENIORITY_ORDER and filters.seniorities:
            # One level either side is a near miss, not a failure. Titles
            # are inconsistent enough between companies that treating this
            # as binary throws away good people.
            wanted_indexes = [
                _SENIORITY_ORDER.index(s) for s in filters.seniorities if s in _SENIORITY_ORDER
            ]
            if (
                wanted_indexes
                and min(abs(_SENIORITY_ORDER.index(level) - i) for i in wanted_indexes) == 1
            ):
                earned += 5
                reasons.append(
                    {"label": "Seniority", "detail": f"{level}, one level off", "points": 5}
                )
            else:
                reasons.append({"label": "Seniority", "detail": level, "points": 0})

    if filters.min_years is not None and prospect.years_experience is not None:
        available += 10
        if prospect.years_experience >= filters.min_years:
            earned += 10
            reasons.append(
                {
                    "label": "Experience",
                    "detail": f"{prospect.years_experience} years",
                    "points": 10,
                }
            )
        else:
            reasons.append(
                {
                    "label": "Experience",
                    "detail": f"{prospect.years_experience} years, under the {filters.min_years} asked for",
                    "points": 0,
                }
            )

    if available == 0:
        # No filters to judge against. Returning 50 rather than 0 or 100
        # keeps the ordering neutral instead of implying a verdict the
        # search never had the information to reach.
        return 50, [{"label": "No filters", "detail": "nothing to score against", "points": 0}]

    return round(100 * earned / available), reasons


# ── persistence ──────────────────────────────────────────────
async def _upsert_prospect(
    session: AsyncSession, incoming: ProviderProspect, score: int, reasons: list[dict[str, Any]]
) -> Prospect:
    """Insert or refresh a prospect, preserving anything we must not lose.

    ``do_not_contact`` is never cleared by a refresh. A suppression is a
    person's stated wish, and a later search rediscovering them must not
    quietly undo it.
    """
    existing = await session.scalar(
        select(Prospect).where(Prospect.dedupe_key == incoming.dedupe_key)
    )

    if existing is None:
        existing = Prospect(dedupe_key=incoming.dedupe_key)
        session.add(existing)

    existing.dedupe_confidence = incoming.dedupe_confidence
    existing.provider = incoming.provider
    existing.provider_person_id = incoming.provider_person_id
    existing.full_name = incoming.full_name
    existing.headline = incoming.headline
    existing.job_title = incoming.job_title
    existing.seniority = incoming.seniority
    existing.company_name = incoming.company_name
    existing.industry = incoming.industry
    existing.years_experience = incoming.years_experience
    existing.skills = list(incoming.skills)
    existing.location_city = incoming.location_city
    existing.location_country = incoming.location_country
    existing.linkedin_url = incoming.linkedin_url
    existing.phone_status = incoming.phone_status.value
    existing.phone_e164 = incoming.phone_e164
    existing.fit_score = score
    existing.fit_reasons = reasons
    existing.raw = incoming.raw
    existing.redacted_fields = incoming.redacted_fields
    existing.expires_at = datetime.now(UTC) + _RETENTION

    await session.flush()
    return existing


async def run_search(
    session: AsyncSession, settings: Settings, payload: SearchRequest
) -> SearchResponse:
    """Interpret a job description, search, rank, and persist the result.

    The filters are returned with the results, along with the literal
    provider query, so a surprising result set can be explained rather
    than argued about.
    """
    if payload.filters is not None:
        filters = payload.filters
        method: str = "manual"
        note: str | None = None
    else:
        extracted = await extract_filters(payload.jd_text, settings)
        filters = extracted.filters
        method = extracted.method
        note = extracted.note

    provider = build_provider(settings.people_provider, settings.pdl_api_key)
    degraded_to: str | None = None

    try:
        try:
            page = await provider.search(filters, payload.limit)
        except (PdlQuotaError, PdlAuthError) as exc:
            # Out of credits or a bad key. The screen still has to work, so
            # the fixture provider serves the results and the response says
            # so rather than passing sample data off as live.
            logger.warning("people.provider_degraded", provider=provider.name, error=str(exc))
            degraded_to = "fixture"
            note = (
                f"{provider.name} is unavailable ({exc}), so these results come from "
                "the built-in sample set rather than live data."
            )
            await provider.aclose()
            provider = build_provider("fixture")
            page = await provider.search(filters, payload.limit)
    finally:
        await provider.aclose()

    scored = [(p, *score_prospect(p, filters)) for p in page.prospects]
    scored.sort(key=lambda row: row[1], reverse=True)

    search = PeopleSearch(
        jd_text=payload.jd_text[:20_000],
        jd_sha256=hashlib.sha256(payload.jd_text.encode()).hexdigest(),
        extraction_method=method,
        filters_resolved=filters.model_dump(mode="json"),
        filters_edited=payload.filters is not None,
        provider=provider.name,
        provider_query=page.provider_query,
        provider_degraded_to=degraded_to,
        result_count=len(scored),
        credits_charged=page.credits_charged,
        cache_hit=False,
    )
    session.add(search)
    await session.flush()

    out: list[ProspectOut] = []
    callable_count = 0

    for rank, (incoming, score, reasons) in enumerate(scored):
        row = await _upsert_prospect(session, incoming, score, reasons)
        session.add(ProspectSearchHit(search_id=search.id, prospect_id=row.id, rank=rank))

        decision = await allowlist_for(session, settings, row)
        if decision.allowed:
            callable_count += 1

        out.append(
            ProspectOut(
                id=row.id,
                full_name=row.full_name,
                headline=row.headline,
                job_title=row.job_title,
                seniority=row.seniority,
                company_name=row.company_name,
                industry=row.industry,
                years_experience=row.years_experience,
                skills=row.skills,
                location_city=row.location_city,
                location_country=row.location_country,
                linkedin_url=row.linkedin_url,
                phone_status=PhoneStatus(row.phone_status),
                callable=decision.allowed,
                not_callable_reason=None if decision.allowed else decision.reason,
                do_not_contact=row.do_not_contact,
                fit_score=row.fit_score,
                fit_reasons=row.fit_reasons,
            )
        )

    await session.flush()
    logger.info(
        "people.search_completed",
        search_id=str(search.id),
        provider=search.provider,
        results=len(out),
        callable=callable_count,
        method=method,
    )

    return SearchResponse(
        search_id=search.id,
        provider=search.provider,
        provider_degraded_to=degraded_to,
        extraction_method=method,  # type: ignore[arg-type]
        filters=filters,
        provider_query=page.provider_query,
        prospects=out,
        total_estimated=page.total_estimated,
        credits_charged=page.credits_charged,
        callable_count=callable_count,
        note=note,
    )


async def get_search(session: AsyncSession, search_id: uuid.UUID) -> PeopleSearch | None:
    """Load a stored search, so a result set can be revisited or audited."""
    result = await session.execute(select(PeopleSearch).where(PeopleSearch.id == search_id))
    return result.scalar_one_or_none()
