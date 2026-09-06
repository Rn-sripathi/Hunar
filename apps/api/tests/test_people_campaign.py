"""Tests for outreach campaigns, where the gate turns into a phone call.

Two properties are worth more than everything else in this file.

A blocked prospect must never become a target. ``outreach_target``
carries a NOT NULL foreign key to an allowlist entry, so the service
refusing first is what stops the database refusing second, and the
difference between those two is whether the recruiter sees a reason or a
stack trace.

And the gate runs again at launch. A campaign assembled at 18:55 can
still be draining at 19:05, and a person can ask to be left alone in
between. A re-check that only re-read the stored status would call them
anyway.

Every call in here goes to the demo Hunar client the application already
owns. Nothing dials, and the callback URLs are pointed at a host the
settings themselves classify as undeliverable, so the simulated calls
attempt no outbound HTTP either.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.answers import mask_number
from app.core.answers import normalize_result as core_normalize_result
from app.core.config import Settings
from app.core.errors import ValidationFailedError
from app.core.models import CallAttempt
from app.people.models import OutreachCampaign, OutreachTarget, PhoneStatus, Prospect, TargetStatus
from app.people.schemas import CampaignCreate
from app.people.services.campaign_service import create_campaign, launch_campaign
from app.people.services.consent import seed_allowlist

ALLOWLISTED = "+919000000001"
UNLISTED = "+919000000099"


def _fixed_offset_zone(local_hour: int, now: datetime) -> str:
    """A timezone in which ``now`` reads as ``local_hour`` o'clock.

    The consent gate reads the wall clock through the configured
    timezone, and ``create_campaign`` and ``launch_campaign`` do not take
    a ``now`` argument. Rather than patching the clock, these tests move
    the timezone: a fixed-offset zone chosen from the current UTC hour
    puts "right now" wherever the test needs it, deterministically, on
    any machine and at any time of day.
    """
    offset = (local_hour - now.astimezone(UTC).hour) % 24
    if offset > 14:
        offset -= 24
    # Etc/GMT zones invert the sign: UTC+5 is spelled Etc/GMT-5.
    return f"Etc/GMT{-offset:+d}"


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session on the same in-memory database the application holds."""
    async with session_factory() as db:
        yield db


@pytest.fixture
def open_hours(settings: Settings) -> Settings:
    """Settings whose 10:00-19:00 window is open at this exact moment."""
    return settings.model_copy(
        update={
            "calling_timezone": _fixed_offset_zone(14, datetime.now(UTC)),
            # Classified undeliverable, so the demo client places its calls
            # without callbacks and the suite makes no outbound request.
            "public_api_base_url": "https://localhost",
        }
    )


@pytest.fixture
def closed_hours(settings: Settings) -> Settings:
    """Settings whose window is shut at this exact moment: it is 03:00 there."""
    return settings.model_copy(
        update={
            "calling_timezone": _fixed_offset_zone(3, datetime.now(UTC)),
            "public_api_base_url": "https://localhost",
        }
    )


async def add_prospect(session: AsyncSession, name: str, *, phone: str | None = None) -> Prospect:
    session.add(
        prospect := Prospect(
            dedupe_key=f"li:{name.lower().replace(' ', '-')}",
            provider="fixture",
            full_name=name,
            phone_status=(
                PhoneStatus.PRESENT_MASKED.value if phone is None else PhoneStatus.REVEALED.value
            ),
            phone_e164=phone,
        )
    )
    await session.flush()
    return prospect


def campaign_over(*prospects: Prospect) -> CampaignCreate:
    return CampaignCreate(
        prospect_ids=[prospect.id for prospect in prospects],
        job_title="Senior Backend Engineer",
        company_name="Acme Payments",
        job_city="Bengaluru",
        role_pitch="a payments company in Bengaluru hiring a senior backend engineer",
    )


async def targets_of(session: AsyncSession, campaign: OutreachCampaign) -> list[OutreachTarget]:
    result = await session.execute(
        select(OutreachTarget).where(OutreachTarget.campaign_id == campaign.id)
    )
    return list(result.scalars())


async def attempt_count(session: AsyncSession, campaign: OutreachCampaign) -> int:
    count = await session.scalar(
        select(func.count()).select_from(CallAttempt).where(CallAttempt.campaign_id == campaign.id)
    )
    return count or 0


class TestCreatingACampaign:
    async def test_prospects_nobody_consented_to_produce_no_targets_at_all(
        self, session: AsyncSession, open_hours: Settings
    ) -> None:
        """This is the test that proves the NOT NULL column is never reached.

        The allowlist is seeded, so the refusal is genuinely "these
        numbers are not on it" rather than "there is no allowlist". Every
        prospect is refused, and the service reports each refusal instead
        of writing a half-consented row for someone to flip later.
        """
        await seed_allowlist(session, open_hours)
        one = await add_prospect(session, "Ananya Iyer")
        two = await add_prospect(session, "Rahul Bhatia", phone=UNLISTED)

        campaign, blocked = await create_campaign(session, open_hours, campaign_over(one, two))

        assert await targets_of(session, campaign) == []
        assert len(blocked) == 2
        assert {entry["prospect"] for entry in blocked} == {"Ananya Iyer", "Rahul Bhatia"}
        for entry in blocked:
            assert "allowlist" in entry["reason"].lower()

    async def test_an_allowlisted_prospect_becomes_a_pending_target(
        self, session: AsyncSession, open_hours: Settings
    ) -> None:
        """The gate has to be able to admit somebody, or nothing is ever called."""
        await seed_allowlist(session, open_hours)
        prospect = await add_prospect(session, "Aditya Menon", phone=ALLOWLISTED)

        campaign, blocked = await create_campaign(session, open_hours, campaign_over(prospect))

        assert blocked == []
        target = (await targets_of(session, campaign)).pop()
        assert target.status == TargetStatus.PENDING.value
        assert target.allowlist_id is not None
        assert target.block_reason is None

    async def test_creating_a_campaign_places_no_calls(
        self, session: AsyncSession, open_hours: Settings, voice_client: Any
    ) -> None:
        """Assembling a list and dialling it are separate, deliberate acts."""
        await seed_allowlist(session, open_hours)
        prospect = await add_prospect(session, "Aditya Menon", phone=ALLOWLISTED)

        campaign, _ = await create_campaign(session, open_hours, campaign_over(prospect))

        assert campaign.status == "DRAFT"
        assert campaign.hunar_agent_id is None
        assert await attempt_count(session, campaign) == 0

    async def test_a_campaign_over_nobody_real_is_refused(
        self, session: AsyncSession, open_hours: Settings
    ) -> None:
        """A stale prospect id from a reloaded tab must not create an empty campaign."""
        payload = CampaignCreate(
            prospect_ids=[uuid.uuid4()],
            job_title="Senior Backend Engineer",
            company_name="Acme Payments",
            role_pitch="a payments company hiring a senior backend engineer",
        )

        with pytest.raises(ValidationFailedError):
            await create_campaign(session, open_hours, payload)


class TestLaunching:
    async def test_the_gate_runs_again_and_a_newly_suppressed_person_is_not_called(
        self, session: AsyncSession, open_hours: Settings, voice_client: Any
    ) -> None:
        """The second check is the one that matters.

        The target was admitted when the campaign was built and carries a
        valid allowlist id, so nothing about its stored state would stop
        the call. Only re-running the gate against the prospect as it is
        right now does.
        """
        await seed_allowlist(session, open_hours)
        prospect = await add_prospect(session, "Vikram Shetty", phone=ALLOWLISTED)
        campaign, _ = await create_campaign(session, open_hours, campaign_over(prospect))
        admitted = (await targets_of(session, campaign)).pop()
        assert admitted.status == TargetStatus.PENDING.value

        prospect.do_not_contact = True
        await session.flush()

        report = await launch_campaign(session, voice_client, open_hours, campaign)

        assert report.launched == 0
        assert await attempt_count(session, campaign) == 0
        target = (await targets_of(session, campaign)).pop()
        assert target.status == TargetStatus.BLOCKED.value
        assert target.block_reason
        assert [entry["prospect"] for entry in report.blocked] == ["Vikram Shetty"]

    async def test_launching_outside_calling_hours_defers_rather_than_blocks(
        self, session: AsyncSession, closed_hours: Settings, voice_client: Any
    ) -> None:
        """The clock is the one obstacle that clears by itself.

        Blocking would need a human to notice and re-run the campaign;
        deferring keeps the target alive for the next window. Getting this
        branch backwards loses a whole evening's list silently.
        """
        await seed_allowlist(session, closed_hours)
        prospect = await add_prospect(session, "Sneha Kulkarni", phone=ALLOWLISTED)
        campaign, blocked = await create_campaign(session, closed_hours, campaign_over(prospect))
        assert blocked == [], "out of hours is not a refusal"

        report = await launch_campaign(session, voice_client, closed_hours, campaign)

        assert report.launched == 0
        assert report.deferred == 1
        assert report.blocked == []
        assert await attempt_count(session, campaign) == 0
        target = (await targets_of(session, campaign)).pop()
        assert target.status == TargetStatus.DEFERRED.value

    async def test_a_permitted_target_is_called_exactly_once(
        self, session: AsyncSession, open_hours: Settings, voice_client: Any
    ) -> None:
        """A duplicate here is a second unsolicited call to a stranger.

        Relaunching is an ordinary thing to do: a campaign can be launched
        again to pick up targets that were deferred, or after a transport
        failure. Anyone already dialled has to be skipped on the way past.
        """
        await seed_allowlist(session, open_hours)
        prospect = await add_prospect(session, "Aditya Menon", phone=ALLOWLISTED)
        campaign, _ = await create_campaign(session, open_hours, campaign_over(prospect))

        first = await launch_campaign(session, voice_client, open_hours, campaign)
        assert first.launched == 1
        assert await attempt_count(session, campaign) == 1

        second = await launch_campaign(session, voice_client, open_hours, campaign)

        assert second.launched == 0
        assert await attempt_count(session, campaign) == 1, "the same stranger, called twice"

    async def test_a_placed_call_dials_the_consented_number_not_the_sourced_one(
        self, session: AsyncSession, open_hours: Settings, voice_client: Any
    ) -> None:
        """The allowlist entry is the dialling instruction, not the prospect.

        Reading the number off the prospect would work for as long as the
        two agree, and then quietly call a broker-sourced number the day
        somebody edits one of them.
        """
        await seed_allowlist(session, open_hours)
        prospect = await add_prospect(session, "Aditya Menon", phone=ALLOWLISTED)
        campaign, _ = await create_campaign(session, open_hours, campaign_over(prospect))

        await launch_campaign(session, voice_client, open_hours, campaign)

        attempt = (await session.execute(select(CallAttempt))).scalar_one()
        assert attempt.hunar_call_id is not None
        placed = await voice_client.get_call(attempt.hunar_call_id)
        assert placed.mobile_number == ALLOWLISTED


class TestSharedAnswerMachinery:
    """The outreach table renders through :mod:`app.core.answers`.

    Both applications ask a voice agent questions and get back a flat map
    of strings, so the coercion and the masking are shared. These two
    guard the parts the sourcing UI depends on.
    """

    def test_masking_keeps_the_last_four_digits_and_hides_the_rest(self) -> None:
        """Enough to recognise a row, not enough to be worth screenshotting."""
        masked = mask_number(ALLOWLISTED)

        assert masked.endswith("0001")
        assert ALLOWLISTED not in masked
        assert "9000000" not in masked

    def test_masking_a_number_too_short_to_mask_reveals_nothing(self) -> None:
        """A malformed value must not fall through to being printed whole."""
        assert mask_number("1234") == "••••"

    def test_the_hiring_domains_name_for_the_normaliser_is_the_same_function(self) -> None:
        """The implementation moved to core; the old import must not fork.

        A re-export that drifted into a second copy would be invisible
        until the two coercions disagreed about what "Haan" means.
        """
        from app.hiring.services.result_normalizer import normalize_result

        assert normalize_result is core_normalize_result
