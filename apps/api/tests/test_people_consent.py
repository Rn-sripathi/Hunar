"""Tests for the gate that decides whether a stranger may be called.

Everything else in the sourcing domain can be wrong and cost someone an
afternoon. This can be wrong and cost someone an unsolicited phone call
they never agreed to, from a sender who is not registered on DLT, using
data a broker sold. So the cases below are chosen for consequence rather
than coverage: each one is a way the gate could fail open.

The clock is never mocked. :func:`allowlist_for` and
:func:`within_calling_hours` both take an explicit ``now``, and passing a
fixed instant proves the timezone arithmetic rather than proving that
``freezegun`` works.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.people.models import ContactAllowlistEntry, DoNotContact, PhoneStatus, Prospect
from app.people.services.consent import (
    allowlist_for,
    hash_number,
    record_do_not_contact,
    seed_allowlist,
    within_calling_hours,
)

#: On the allowlist the `settings` fixture declares. Deliberately in a
#: reserved-looking block: nothing here should ever reach a real handset.
ALLOWLISTED = "+919000000001"
#: Well-formed, plausible, and not consented to. The whole point.
UNLISTED = "+919000000099"

#: 11:30 in Asia/Kolkata, comfortably inside the default 10:00-19:00
#: window, so tests about consent are not also tests about the clock.
IN_HOURS = datetime(2026, 1, 15, 6, 0, tzinfo=UTC)
#: 01:30 the next morning in Asia/Kolkata.
OUT_OF_HOURS = datetime(2026, 1, 15, 20, 0, tzinfo=UTC)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session on the same in-memory database the application holds."""
    async with session_factory() as db:
        yield db


async def add_prospect(
    session: AsyncSession, name: str, *, phone: str | None = None, dnc: bool = False
) -> Prospect:
    """A sourced person, with or without digits the provider surfaced."""
    prospect = Prospect(
        dedupe_key=f"li:{name.lower().replace(' ', '-')}",
        provider="fixture",
        full_name=name,
        phone_status=(
            PhoneStatus.PRESENT_MASKED.value if phone is None else PhoneStatus.REVEALED.value
        ),
        phone_e164=phone,
        do_not_contact=dnc,
    )
    session.add(prospect)
    await session.flush()
    return prospect


class TestTheAllowlistGate:
    async def test_a_prospect_with_no_allowlisted_number_is_refused(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """The default answer is no.

        Almost every sourced record arrives with no dialable number at
        all, and the gate must treat that as a refusal rather than as a
        missing precondition to fill in later.
        """
        await seed_allowlist(session, settings)
        prospect = await add_prospect(session, "Ananya Iyer")

        decision = await allowlist_for(session, settings, prospect, now=IN_HOURS)

        assert decision.allowed is False
        assert decision.allowlist_id is None

    async def test_a_number_we_hold_but_never_consented_to_is_refused(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """Holding digits is not permission to dial them.

        This is the case a bug would most plausibly get wrong: the record
        has a perfectly valid mobile on it, so any check that asks "do we
        have a number?" rather than "did the operator consent to this
        number?" passes.
        """
        await seed_allowlist(session, settings)
        prospect = await add_prospect(session, "Rahul Bhatia", phone=UNLISTED)

        decision = await allowlist_for(session, settings, prospect, now=IN_HOURS)

        assert decision.allowed is False
        assert decision.allowlist_id is None

    async def test_a_refusal_is_explained_in_words_a_recruiter_can_read(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """The UI shows blocked people rather than hiding them.

        A list that silently omits who it will not call teaches the
        operator nothing, so the reason has to be a sentence rather than
        an error code.
        """
        await seed_allowlist(session, settings)
        prospect = await add_prospect(session, "Priya Raghavan", phone=UNLISTED)

        reason = (await allowlist_for(session, settings, prospect, now=IN_HOURS)).reason

        assert "allowlist" in reason.lower()
        assert reason == reason.strip()
        assert len(reason.split()) >= 5, "a code, not an explanation"

    async def test_an_allowlisted_number_is_permitted(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """The gate has to be able to say yes, or the product does nothing."""
        await seed_allowlist(session, settings)
        prospect = await add_prospect(session, "Aditya Menon", phone=ALLOWLISTED)

        decision = await allowlist_for(session, settings, prospect, now=IN_HOURS)

        assert decision.allowed is True
        assert decision.allowlist_id is not None
        assert decision.allowlist_label == "Test line one"
        assert decision.deferrable is False

    async def test_the_environment_still_has_the_final_say(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """A row in the database is not consent on its own.

        Allowlist rows are additive and never deleted, because a live
        campaign holds foreign keys to them. Eligibility is therefore
        re-checked against the current environment, so removing a number
        from the environment stops new calls immediately.
        """
        await seed_allowlist(session, settings)
        prospect = await add_prospect(session, "Deepika Nair", phone=ALLOWLISTED)
        revoked = settings.model_copy(update={"demo_allowlist": "+919000000002|Test line two"})

        decision = await allowlist_for(session, revoked, prospect, now=IN_HOURS)

        assert decision.allowed is False


class TestSuppression:
    async def test_do_not_contact_on_the_prospect_overrides_the_allowlist(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """Someone's stated wish outranks the operator's configuration."""
        await seed_allowlist(session, settings)
        prospect = await add_prospect(session, "Vikram Shetty", phone=ALLOWLISTED, dnc=True)

        decision = await allowlist_for(session, settings, prospect, now=IN_HOURS)

        assert decision.allowed is False
        assert decision.deferrable is False
        assert "contacted" in decision.reason.lower()

    async def test_a_suppressed_number_is_refused_even_when_allowlisted(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """Suppression is keyed on the number, not on the prospect row.

        Deleting or re-discovering a person must not resurrect the
        ability to call them, so the check that matters is the hash of
        the number rather than a flag on a record that can be replaced.
        """
        await seed_allowlist(session, settings)
        await record_do_not_contact(session, ALLOWLISTED, reason="asked during a call")
        prospect = await add_prospect(session, "Imran Qureshi", phone=ALLOWLISTED)

        decision = await allowlist_for(session, settings, prospect, now=IN_HOURS)

        assert decision.allowed is False
        assert decision.allowlist_id is None
        assert "do-not-contact" in decision.reason.lower()

    async def test_only_a_hash_of_the_number_is_stored(self, session: AsyncSession) -> None:
        """Honouring "never call me again" must not require keeping the number.

        Retaining the very data a person asked you to stop using is the
        obvious failure here, and it is invisible unless something asserts
        on what actually landed in the row.
        """
        await record_do_not_contact(session, ALLOWLISTED, reason="asked during a call")

        row = (await session.execute(select(DoNotContact))).scalar_one()
        stored = " ".join(str(value) for value in (row.e164_sha256, row.reason, row.source))

        assert ALLOWLISTED not in stored
        assert "9000000001" not in stored, "the digits must not survive in any column"
        assert row.e164_sha256 == hash_number(ALLOWLISTED)
        assert len(row.e164_sha256) == 64

    async def test_suppressing_the_same_number_twice_adds_one_row(
        self, session: AsyncSession
    ) -> None:
        """Someone can ask twice; the hash is the primary key and would raise."""
        await record_do_not_contact(session, ALLOWLISTED, reason="asked during a call")
        await record_do_not_contact(session, ALLOWLISTED, reason="asked again")

        assert await session.scalar(select(func.count()).select_from(DoNotContact)) == 1


class TestCallingHours:
    async def test_outside_the_window_the_call_is_deferred_rather_than_lost(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """The clock is the one obstacle that goes away by itself.

        Carrying the allowlist id through the refusal is what lets a
        target row exist at all, since the foreign key is NOT NULL. Drop
        it here and an out-of-hours campaign silently discards everyone
        instead of queueing them for the morning.
        """
        await seed_allowlist(session, settings)
        prospect = await add_prospect(session, "Sneha Kulkarni", phone=ALLOWLISTED)

        decision = await allowlist_for(session, settings, prospect, now=OUT_OF_HOURS)

        assert decision.allowed is False
        assert decision.deferrable is True
        assert decision.allowlist_id is not None
        assert settings.calling_timezone in decision.reason

    def test_the_window_is_evaluated_where_the_phone_rings(self, settings: Settings) -> None:
        """A deployment in another region must not call India at 03:00.

        05:00 UTC is 10:30 in Asia/Kolkata: inside a 10:00-19:00 window
        read locally, outside the same window read as UTC. If the server's
        own clock ever leaks in, exactly one of these assertions breaks.
        """
        server_local = settings.model_copy(update={"calling_timezone": "UTC"})
        morning_in_india = datetime(2026, 1, 15, 5, 0, tzinfo=UTC)

        assert within_calling_hours(settings, morning_in_india) is True
        assert within_calling_hours(server_local, morning_in_india) is False

    def test_and_the_same_holds_in_the_other_direction(self, settings: Settings) -> None:
        """14:00 UTC is 19:30 in Asia/Kolkata, which is past the window."""
        server_local = settings.model_copy(update={"calling_timezone": "UTC"})
        evening_in_india = datetime(2026, 1, 15, 14, 0, tzinfo=UTC)

        assert within_calling_hours(settings, evening_in_india) is False
        assert within_calling_hours(server_local, evening_in_india) is True


class TestSeeding:
    async def test_seeding_twice_adds_nothing_the_second_time(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """Seeding runs on demand, so it has to be safe to run repeatedly.

        A duplicate row would either violate the unique constraint or
        leave two allowlist entries for one number, and a target pointing
        at the stale one would survive the number being revoked.
        """
        expected = len(settings.allowlist)

        assert await seed_allowlist(session, settings) == expected
        assert await seed_allowlist(session, settings) == 0
        assert (
            await session.scalar(select(func.count()).select_from(ContactAllowlistEntry))
            == expected
        )

    async def test_seeding_records_where_the_consent_came_from(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """Consent that cannot be traced to a source is not evidence of anything."""
        await seed_allowlist(session, settings)

        entries = list((await session.execute(select(ContactAllowlistEntry))).scalars())

        assert {entry.e164 for entry in entries} == settings.allowlisted_numbers
        for entry in entries:
            assert entry.consent_source == "environment"
            assert entry.consented_at is not None
