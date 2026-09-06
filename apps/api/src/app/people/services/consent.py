"""The gate that decides whether a stranger may be called.

This is the most important file in the sourcing domain, and the shortest
way to explain it is: **sourcing is broad, calling is narrow.**

Prospects come from a data broker. They did not apply, have not heard of
the company, and never agreed to be phoned. Three separate things say
that dialling them from this application would be wrong:

* India's TCCCPR rules require a commercial voice caller to be a
  registered sender on the DLT platform. This application is not one.
* People Data Labs' acceptable use policy forbids using their data for
  employment eligibility decisions, so a sourced record may inform who
  you contact but must never gate a hiring outcome.
* Independent of any regulation, cold-calling someone whose number came
  from a broker is a thing a person should have to opt into deliberately.

So every outbound call must point at a number on an allowlist the
operator controls, supplied through the environment rather than editable
in the product. A deployment cannot grow its own permission to call
people, and the database enforces it: ``outreach_target.allowlist_id`` is
NOT NULL, so a bug that skips this module fails at insert rather than
placing a call.

The check runs twice on purpose. Once when a campaign is created, and
again immediately before each call, because a campaign launched at 18:55
can still be draining at 19:05 and calling hours are not advisory.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.people.models import ContactAllowlistEntry, DoNotContact, Prospect

logger = structlog.get_logger(__name__)

__all__ = [
    "ConsentDecision",
    "allowlist_for",
    "record_do_not_contact",
    "seed_allowlist",
    "within_calling_hours",
]


def hash_number(e164: str) -> str:
    """Hash a number for the suppression list.

    Honouring "never call me again" should not require keeping the number
    of the person who asked. A hash answers membership without retaining
    the thing being suppressed.
    """
    return hashlib.sha256(e164.strip().encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ConsentDecision:
    """Whether one prospect may be called, and why not if not."""

    allowed: bool
    allowlist_id: object | None = None
    allowlist_label: str = ""
    #: Written for the recruiter, not for a log. It appears in the table
    #: next to the person it refers to.
    reason: str = ""
    #: True when the only obstacle is the clock, so the call can be
    #: deferred rather than abandoned.
    deferrable: bool = False


async def seed_allowlist(session: AsyncSession, settings: Settings) -> int:
    """Load the environment's allowlist into the database.

    Idempotent, and additive only. Removing an entry from the environment
    does not delete the row, because an existing campaign may reference
    it and a foreign key should not break on a config edit. What it does
    stop is any *new* target being created against it, since eligibility
    is always re-checked against the current environment.
    """
    existing = {
        entry.e164: entry
        for entry in (await session.execute(select(ContactAllowlistEntry))).scalars()
    }

    added = 0
    for entry in settings.allowlist:
        if entry.e164 in existing:
            continue
        session.add(
            ContactAllowlistEntry(
                e164=entry.e164,
                label=entry.label,
                consent_source="environment",
                consented_at=datetime.now(UTC),
            )
        )
        added += 1

    if added:
        await session.flush()
        logger.info("people.allowlist_seeded", added=added)
    return added


def within_calling_hours(settings: Settings, now: datetime | None = None) -> bool:
    """Whether the clock currently permits an outbound call.

    Evaluated in the recipient's timezone rather than the server's. A
    deployment in another region calling India at three in the morning
    would be both illegal and unforgivable.
    """
    zone = ZoneInfo(settings.calling_timezone)
    local = (now or datetime.now(UTC)).astimezone(zone)
    return settings.calling_window_start <= local.time() <= settings.calling_window_end


async def allowlist_for(
    session: AsyncSession,
    settings: Settings,
    prospect: Prospect,
    *,
    now: datetime | None = None,
) -> ConsentDecision:
    """Decide whether this prospect may be called, and on what number.

    Every refusal carries a reason written for a person, because the UI
    shows blocked prospects rather than hiding them. Demonstrating the
    gate firing is the point of having it: a list that silently omits the
    people it will not call teaches the operator nothing.
    """
    if prospect.do_not_contact:
        return ConsentDecision(
            allowed=False,
            reason="This person asked not to be contacted again.",
        )

    # A broker-sourced number is never dialable, whether or not we hold
    # it. Only a number someone put on the allowlist is.
    candidate = (prospect.phone_e164 or "").strip()
    entries = {
        entry.e164: entry
        for entry in (await session.execute(select(ContactAllowlistEntry))).scalars()
    }
    permitted = settings.allowlisted_numbers

    match = entries.get(candidate) if candidate else None
    if match is None or match.e164 not in permitted:
        return ConsentDecision(
            allowed=False,
            reason=(
                "Not on the consent allowlist. This deployment only calls numbers "
                "its operator has explicitly consented to, so sourced numbers are "
                "never dialled."
            ),
        )

    suppressed = await session.scalar(
        select(DoNotContact.e164_sha256).where(DoNotContact.e164_sha256 == hash_number(match.e164))
    )
    if suppressed is not None:
        return ConsentDecision(
            allowed=False,
            reason="This number is on the do-not-contact list.",
        )

    if not within_calling_hours(settings, now):
        return ConsentDecision(
            allowed=False,
            allowlist_id=match.id,
            allowlist_label=match.label,
            reason=(
                f"Outside calling hours ({settings.calling_hours_start} to "
                f"{settings.calling_hours_end} {settings.calling_timezone}). "
                "Queued for the next window."
            ),
            deferrable=True,
        )

    return ConsentDecision(allowed=True, allowlist_id=match.id, allowlist_label=match.label)


async def record_do_not_contact(
    session: AsyncSession, e164: str, *, reason: str, source: str = "call"
) -> None:
    """Suppress a number permanently, storing only its hash.

    Called automatically when a conversation records a do-not-contact
    request, and manually from the prospect table. Suppression outlives
    the prospect record: deleting someone's data must not resurrect the
    ability to call them.
    """
    digest = hash_number(e164)
    existing = await session.scalar(
        select(DoNotContact.e164_sha256).where(DoNotContact.e164_sha256 == digest)
    )
    if existing is not None:
        return

    session.add(
        DoNotContact(
            e164_sha256=digest,
            reason=reason,
            source=source,
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    logger.info("people.do_not_contact_recorded", source=source)
