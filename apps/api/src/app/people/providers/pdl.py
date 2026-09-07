"""People Data Labs adapter.

Chosen as the live provider because it is the only one in the brief that
self-serves an API key in minutes and offers genuine person *search*
rather than enrichment of an identifier you already have.

What it will not do is give you a phone number. Since release v29.0, free
plans return contact fields as ``true`` or ``false`` instead of values.
You learn that a mobile exists; you never learn what it is. That is why
``supports_phone_reveal`` is keyed off the plan rather than hardcoded, and
why nothing downstream may assume a number is present.

This adapter owns the Elasticsearch query DSL and the field mapping, so
the rest of the application never sees them. Swapping providers means
writing a sibling of this file and nothing else.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.people.models import PhoneStatus
from app.people.providers.base import Prospect, SearchPage, dedupe_key_for, redact
from app.people.schemas import SearchFilters

logger = structlog.get_logger(__name__)

__all__ = [
    "PdlAuthError",
    "PdlProvider",
    "PdlQueryError",
    "PdlQuotaError",
    "PdlRateLimitError",
    "PdlUnavailableError",
]

_PRODUCTION_URL = "https://api.peopledatalabs.com/v5/person/search"
#: Free, charges no credits, returns artificial data. The safety net when
#: the monthly allowance is gone.
_SANDBOX_URL = "https://sandbox.api.peopledatalabs.com/v5/person/search"

#: Our seniority vocabulary mapped onto PDL's job_title_levels.
_SENIORITY_TO_LEVELS: dict[str, list[str]] = {
    "ic": ["entry", "senior"],
    "senior": ["senior"],
    "lead": ["manager", "senior"],
    "manager": ["manager"],
    "director": ["director"],
    "vp": ["vp"],
    "cxo": ["cxo", "owner", "partner"],
}

#: Indian cities routinely appear under more than one name, and PDL keys
#: on the lowercase locality. Searching only the current name silently
#: loses everyone recorded under the old one.
_CITY_ALIASES: dict[str, list[str]] = {
    "bengaluru": ["bengaluru", "bangalore"],
    "mumbai": ["mumbai", "bombay"],
    "gurugram": ["gurugram", "gurgaon"],
    "chennai": ["chennai", "madras"],
    "kolkata": ["kolkata", "calcutta"],
    "pune": ["pune"],
    "hyderabad": ["hyderabad", "secunderabad"],
    "noida": ["noida"],
    "ahmedabad": ["ahmedabad"],
}


class PdlProvider:
    """Person search against People Data Labs."""

    def __init__(self, api_key: str, *, sandbox: bool = False, timeout: float = 30.0) -> None:
        self._api_key = api_key
        self._sandbox = sandbox
        self.name = "pdl_sandbox" if sandbox else "pdl"
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
        )

    def supports_phone_reveal(self) -> bool:
        """False on the plans this project can reach.

        Left as a method rather than a constant so a paid plan can flip
        it without anything downstream changing.
        """
        return False

    def build_query(self, filters: SearchFilters, limit: int) -> dict[str, Any]:
        """Build the Elasticsearch query PDL expects."""
        must: list[dict[str, Any]] = []
        should: list[dict[str, Any]] = []
        must_not: list[dict[str, Any]] = []

        if filters.country:
            must.append({"term": {"location_country": filters.country.lower()}})

        if filters.cities:
            localities: list[str] = []
            for city in filters.cities:
                key = city.strip().lower()
                localities.extend(_CITY_ALIASES.get(key, [key]))
            must.append({"terms": {"location_locality": sorted(set(localities))}})

        if filters.titles:
            # A nested bool whose only clause is `should` already requires
            # one of them to match, which is exactly what we want. The
            # explicit `minimum_should_match` that would normally say so
            # is rejected outright: "Query clause [minimum_should_match]
            # not allowed or invalid field name." Verified against the
            # live API that both forms return the same result count, so
            # dropping it changes nothing but the acceptance.
            must.append(
                {
                    "bool": {
                        "should": [
                            {"match_phrase": {"job_title": title.lower()}}
                            for title in filters.titles
                        ]
                    }
                }
            )

        if filters.seniorities:
            levels = sorted(
                {
                    level
                    for seniority in filters.seniorities
                    for level in _SENIORITY_TO_LEVELS.get(seniority, [])
                }
            )
            if levels:
                must.append({"terms": {"job_title_levels": levels}})

        if filters.require_phone:
            must.append({"exists": {"field": "mobile_phone"}})

        skills = [s.lower() for s in filters.skills_required + filters.skills_nice]
        if skills:
            should.append({"terms": {"skills": skills}})
        if filters.industries:
            should.append(
                {"terms": {"job_company_industry": [i.lower() for i in filters.industries]}}
            )

        must_not.extend(
            {"match_phrase": {"job_title": title.lower()}} for title in filters.excluded_titles
        )
        must_not.extend(
            {"match_phrase": {"job_company_name": company.lower()}}
            for company in filters.exclude_companies
        )

        query: dict[str, Any] = {"bool": {"must": must}}
        if should:
            query["bool"]["should"] = should
        if must_not:
            query["bool"]["must_not"] = must_not

        return {"query": query, "size": limit, "titlecase": True}

    async def search(self, filters: SearchFilters, limit: int) -> SearchPage:
        payload = self.build_query(filters, limit)
        url = _SANDBOX_URL if self._sandbox else _PRODUCTION_URL

        try:
            response = await self._http.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise PdlUnavailableError(f"could not reach People Data Labs: {exc}") from exc

        if response.status_code == 402:
            # Out of credits. The caller degrades to another provider
            # rather than showing an empty screen.
            raise PdlQuotaError("People Data Labs credits are exhausted")
        if response.status_code in (401, 403):
            raise PdlAuthError("The People Data Labs key was rejected")
        if response.status_code == 429:
            # Free plans allow only a handful of searches a minute. Its
            # own class because the remedy is to wait, not to switch
            # provider or to go looking for a bad key.
            raise PdlRateLimitError(
                "People Data Labs is rate limiting this key. Free plans allow only "
                "a few searches per minute; wait a moment and try again."
            )
        if response.status_code >= 400:
            # This previously escaped as a raw httpx error, which reached
            # the UI as an unhandled exception rather than something a
            # person could act on. PDL explains itself in the body, so
            # repeat what it said.
            raise PdlQueryError(_explain(response))

        body = response.json()
        records = body.get("data") or []

        # PDL reports what it actually charged. Counting records guesses,
        # and guessed wrong whenever a query matched nothing at all.
        spent = _header_int(response, "x-call-credits-spent")
        return SearchPage(
            prospects=[self._to_prospect(record) for record in records],
            total_estimated=body.get("total"),
            credits_charged=0 if self._sandbox else (spent if spent is not None else len(records)),
            # `x-ratelimit-remaining` is a JSON object such as
            # {"minute": 6}, so reading it as an integer always produced
            # None. The monthly allowance is the number an operator
            # actually cares about, and it has its own header.
            credits_remaining=_header_int(response, "x-totallimit-remaining"),
            provider_query=payload,
        )

    def _to_prospect(self, record: dict[str, Any]) -> Prospect:
        cleaned, removed = redact(record)

        # Free plans return `true`/`false` here rather than a number, so
        # a truthy value means "a mobile exists", not "here it is".
        mobile = record.get("mobile_phone")
        if mobile is True:
            phone_status = PhoneStatus.PRESENT_MASKED
            phone_e164 = None
        elif isinstance(mobile, str) and mobile.strip():
            phone_status = PhoneStatus.REVEALED
            phone_e164 = mobile.strip()
        elif mobile is False or mobile is None:
            phone_status = PhoneStatus.ABSENT
            phone_e164 = None
        else:
            phone_status = PhoneStatus.UNKNOWN
            phone_e164 = None

        full_name = _text(record.get("full_name")) or "Unknown"
        linkedin = _text(record.get("linkedin_url"))
        key, confidence = dedupe_key_for(
            linkedin_url=linkedin,
            phone_e164=phone_e164,
            full_name=full_name,
            company_name=record.get("job_company_name"),
            country=record.get("location_country"),
        )

        levels = record.get("job_title_levels") or []
        return Prospect(
            provider="pdl_sandbox" if self._sandbox else "pdl",
            provider_person_id=str(record.get("id") or "") or None,
            dedupe_key=key,
            dedupe_confidence=confidence,
            full_name=full_name,
            headline=_text(record.get("headline")) or _text(record.get("job_title")),
            job_title=_text(record.get("job_title")),
            seniority=_text(levels[0]) if levels else None,
            company_name=_text(record.get("job_company_name")),
            company_size_band=_text(record.get("job_company_size")),
            industry=_text(record.get("job_company_industry")),
            years_experience=_years(record),
            skills=[s for s in (record.get("skills") or []) if isinstance(s, str)][:20],
            # Granular location is masked to `true` on free plans exactly
            # as the contact fields are, so this is often unknown rather
            # than absent. Without `_text` the literal string "True"
            # appears in the city column.
            location_city=_text(record.get("location_locality")),
            location_region=_text(record.get("location_region")),
            location_country=_text(record.get("location_country")),
            linkedin_url=linkedin,
            phone_status=phone_status,
            phone_e164=phone_e164,
            email_status=(
                PhoneStatus.PRESENT_MASKED
                if record.get("work_email") or record.get("personal_emails")
                else PhoneStatus.ABSENT
            ),
            raw=cleaned,
            redacted_fields=removed,
        )

    async def aclose(self) -> None:
        await self._http.aclose()


class PdlQuotaError(RuntimeError):
    """Credits are spent. Degrade rather than fail."""


class PdlAuthError(RuntimeError):
    """The key is wrong, missing or revoked."""


class PdlRateLimitError(RuntimeError):
    """Too many requests. The remedy is to wait, not to switch provider."""


class PdlQueryError(RuntimeError):
    """PDL refused the query itself, and said why."""


class PdlUnavailableError(RuntimeError):
    """The network failed before PDL could answer."""


def _explain(response: httpx.Response) -> str:
    """Recover PDL's own account of what was wrong with the request."""
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return f"People Data Labs returned HTTP {response.status_code}"
    message = error.get("message") or response.text[:200]
    return f"People Data Labs rejected the query: {message}"


def _text(value: Any) -> str | None:
    """Return a real string, or None.

    Free plans mask contact and granular-location fields to the boolean
    ``true`` rather than omitting them. Passing that through would put
    the word "True" in a city column and, worse, make a value we do not
    have look like one we do.
    """
    if isinstance(value, str):
        return value.strip() or None
    return None


def _header_int(response: httpx.Response, name: str) -> int | None:
    raw = response.headers.get(name)
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def _years(record: dict[str, Any]) -> int | None:
    """Approximate experience from the earliest role's start year.

    PDL has no direct years-of-experience field. This is a derivation and
    is treated as soft signal for ranking, never as a filter that could
    exclude someone on a guess.
    """
    experience = record.get("experience") or []
    starts: list[int] = []
    for entry in experience:
        start = (entry or {}).get("start_date")
        if isinstance(start, str) and len(start) >= 4 and start[:4].isdigit():
            starts.append(int(start[:4]))
    if not starts:
        return None
    from datetime import UTC, datetime

    span = datetime.now(UTC).year - min(starts)
    return span if 0 <= span <= 60 else None
