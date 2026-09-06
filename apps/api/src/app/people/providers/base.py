"""The provider interface and the normalised prospect.

``Prospect`` deliberately has no plain ``phone`` string as its primary
contact route. It has a ``phone_status``, because the honest answer for
most sourced records is "a mobile exists but this plan will not show it",
and a field called ``phone`` invites code that assumes a number is there.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.people.models import PhoneStatus
from app.people.schemas import SearchFilters

__all__ = [
    "PeopleSearchProvider",
    "Prospect",
    "SearchPage",
    "dedupe_key_for",
    "redact",
]

ProviderName = Literal["pdl", "pdl_sandbox", "apollo", "coresignal", "fixture"]

#: Dropped before a prospect is persisted. None of it is needed to rank
#: someone or to call them, and holding it is the liability. Storing a
#: home address for a person who never asked to be in our database is
#: exactly the kind of thing a data broker gets sued for.
REDACTED_FIELDS = frozenset(
    {
        "location_street_address",
        "location_address_line_2",
        "emails",
        "personal_emails",
        "recommended_personal_email",
        "birth_date",
        "birth_year",
        "sex",
        "gender",
        "mobile_phone",
        "phone_numbers",
    }
)


class Prospect(BaseModel):
    """One person, normalised across providers."""

    provider: ProviderName
    provider_person_id: str | None = None
    dedupe_key: str
    dedupe_confidence: Literal["high", "medium", "low"] = "high"

    full_name: str
    headline: str | None = None
    job_title: str | None = None
    seniority: str | None = None
    company_name: str | None = None
    company_size_band: str | None = None
    industry: str | None = None
    years_experience: int | None = None
    skills: list[str] = Field(default_factory=list)

    location_city: str | None = None
    location_region: str | None = None
    location_country: str | None = None
    linkedin_url: str | None = None

    phone_status: PhoneStatus = PhoneStatus.UNKNOWN
    #: Populated only when a provider genuinely returns digits, which on
    #: the free tiers in this brief means never.
    phone_e164: str | None = None
    email_status: PhoneStatus = PhoneStatus.UNKNOWN

    #: The provider payload after redaction, kept so a later provider
    #: change can backfill without paying for the record twice.
    raw: dict[str, Any] = Field(default_factory=dict)
    redacted_fields: list[str] = Field(default_factory=list)


class SearchPage(BaseModel):
    """One page of results, plus what it cost."""

    prospects: list[Prospect] = Field(default_factory=list)
    total_estimated: int | None = None
    credits_charged: int = 0
    credits_remaining: int | None = None
    #: The literal query sent upstream, for reproducibility and for the
    #: "show me what you actually asked for" affordance in the UI.
    provider_query: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class PeopleSearchProvider(Protocol):
    """A source of people matching a set of filters."""

    name: str

    def supports_phone_reveal(self) -> bool:
        """Whether this provider returns dialable digits on this plan.

        Lets the UI say "mobile on file, not available on this plan"
        instead of rendering an empty column and looking broken.
        """
        ...

    def build_query(self, filters: SearchFilters, limit: int) -> dict[str, Any]:
        """Translate filters into this provider's query shape.

        Separate from :meth:`search` on purpose, so the resolved query can
        be shown to the operator before a credit is spent on it.
        """
        ...

    async def search(self, filters: SearchFilters, limit: int) -> SearchPage: ...

    async def aclose(self) -> None: ...


_LINKEDIN = re.compile(r"linkedin\.com/in/([^/?#]+)", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slug(value: str | None) -> str:
    return _NON_ALNUM.sub("-", (value or "").strip().lower()).strip("-")


def dedupe_key_for(
    *,
    linkedin_url: str | None,
    phone_e164: str | None,
    full_name: str,
    company_name: str | None,
    country: str | None,
) -> tuple[str, Literal["high", "medium", "low"]]:
    """Identify a person across providers, strongest signal first.

    Returns the key and how much to trust it. The confidence matters:
    a name-and-company match collides on common Indian names at large
    employers, so those are stored but never merged into a
    LinkedIn-identified record. A wrong merge is worse than a duplicate,
    because it silently attributes one person's answers to another.
    """
    if linkedin_url and (match := _LINKEDIN.search(linkedin_url)):
        return f"li:{match.group(1).lower()}", "high"

    if phone_e164:
        digest = hashlib.sha256(phone_e164.encode()).hexdigest()[:32]
        return f"ph:{digest}", "high"

    return (
        f"nc:{_slug(full_name)}|{_slug(company_name)}|{_slug(country)}",
        "low",
    )


def redact(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Strip fields we have no business keeping, and record what went.

    The list of removals is stored alongside the payload so the redaction
    is auditable rather than merely claimed.
    """
    removed = sorted(key for key in payload if key in REDACTED_FIELDS)
    cleaned = {key: value for key, value in payload.items() if key not in REDACTED_FIELDS}
    return cleaned, removed
