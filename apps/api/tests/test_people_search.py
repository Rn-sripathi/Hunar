"""Tests for ranking prospects and for persisting a search once.

The score exists to order a call list. It is not evidence about anyone's
ability, the data behind it was bought rather than given, and the
provider's own terms forbid using it to gate employment. Two consequences
show up here as tests: the reasons must always be present and always the
same shape, because the UI shows them instead of the bare number; and a
score of 50 with "nothing to score against" must never be reported as a
verdict.

The persistence tests care about one thing above all. A person found
twice is one person, and their suppression has to follow them. A refresh
that quietly re-enabled calling for someone who asked to be left alone
would be the worst bug in this codebase, and it would look like a
one-line assignment in an upsert.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.people.models import Prospect
from app.people.providers.base import Prospect as ProviderProspect
from app.people.schemas import SearchFilters, SearchRequest
from app.people.services.search_service import run_search, score_prospect

BACKEND_JD = """
Job Title: Senior Backend Engineer
Location: Bengaluru

We need someone with at least 5 years of experience building Python and
PostgreSQL services for a payments platform.
"""


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session on the same in-memory database the application holds."""
    async with session_factory() as db:
        yield db


def candidate(**overrides: Any) -> ProviderProspect:
    """One provider record, varied per test rather than rebuilt per test."""
    fields: dict[str, Any] = {
        "provider": "fixture",
        "dedupe_key": "li:test-person",
        "full_name": "Test Person",
        "job_title": "Backend Engineer",
        "seniority": "senior",
        "company_name": "Acme",
        "skills": ["python", "postgresql"],
        "location_city": "Bengaluru",
        "years_experience": 6,
    }
    fields.update(overrides)
    return ProviderProspect.model_validate(fields)


class TestScoring:
    def test_no_filters_scores_neutral_and_says_why(self) -> None:
        """Zero would read as "bad fit" and 100 as "perfect".

        Neither is true when there was nothing to judge against, so the
        neutral answer keeps the ordering honest and the reason stops the
        50 being mistaken for a measurement.
        """
        score, reasons = score_prospect(candidate(), SearchFilters())

        assert score == 50
        assert reasons == [
            {"label": "No filters", "detail": "nothing to score against", "points": 0}
        ]

    def test_an_exact_title_beats_a_near_one_which_beats_a_miss(self) -> None:
        """Title is the strongest signal that this is the right kind of person.

        Collapsing near matches into misses throws away good people,
        because titles are wildly inconsistent between companies. Treating
        them as equal to exact matches fills the top of the list with
        people who merely share a word.
        """
        filters = SearchFilters(titles=["Backend Engineer"])

        exact, _ = score_prospect(candidate(job_title="Backend Engineer"), filters)
        near, _ = score_prospect(candidate(job_title="Senior Backend Engineer"), filters)
        miss, _ = score_prospect(candidate(job_title="Regional Sales Manager"), filters)

        assert exact > near > miss

    def test_a_title_miss_is_still_explained(self) -> None:
        """The recruiter needs to see why someone ranked low, not just that they did."""
        filters = SearchFilters(titles=["Backend Engineer"])

        _, reasons = score_prospect(candidate(job_title="Regional Sales Manager"), filters)

        titles = [reason for reason in reasons if reason["label"] == "Title"]
        assert len(titles) == 1
        assert "Regional Sales Manager" in titles[0]["detail"]

    def test_required_skills_are_scored_in_proportion(self) -> None:
        """Three of four required skills is not the same as none of four.

        All-or-nothing scoring on required skills collapses the middle of
        the list, which is where the people worth calling actually are.
        """
        filters = SearchFilters(skills_required=["python", "kafka", "aws", "terraform"])

        none, _ = score_prospect(candidate(skills=["java"]), filters)
        half, _ = score_prospect(candidate(skills=["python", "kafka"]), filters)
        most, _ = score_prospect(candidate(skills=["python", "kafka", "aws"]), filters)
        all_of, _ = score_prospect(
            candidate(skills=["python", "kafka", "aws", "terraform"]), filters
        )

        assert none == 0
        assert all_of == 100
        assert none < half < most < all_of

    def test_skill_matching_ignores_case(self) -> None:
        """Providers capitalise skills inconsistently; a recruiter types lowercase."""
        filters = SearchFilters(skills_required=["Python", "Kafka"])

        score, _ = score_prospect(candidate(skills=["python", "KAFKA"]), filters)

        assert score == 100

    def test_every_reason_has_the_shape_the_table_renders(self) -> None:
        """The UI reads these three keys positionally into a column.

        A reason missing ``points``, or carrying a key by another name,
        renders as a blank cell rather than raising, so nothing else in
        the stack would notice.
        """
        filters = SearchFilters(
            titles=["Backend Engineer"],
            skills_required=["python"],
            skills_nice=["postgresql"],
            cities=["Bengaluru"],
            seniorities=["senior"],
            min_years=3,
        )

        for prospect in (candidate(), candidate(job_title="Chef", skills=[], seniority="cxo")):
            _, reasons = score_prospect(prospect, filters)
            assert reasons
            for reason in reasons:
                assert set(reason) == {"label", "detail", "points"}
                assert isinstance(reason["label"], str) and reason["label"]
                assert isinstance(reason["detail"], str)
                assert isinstance(reason["points"], int)

    def test_a_score_never_leaves_the_zero_to_hundred_range(self) -> None:
        """It is rendered as a percentage, and a 140 would look like a bug."""
        filters = SearchFilters(
            titles=["Backend Engineer"],
            skills_required=["python", "postgresql"],
            skills_nice=["python"],
            cities=["Bengaluru"],
            seniorities=["senior"],
            min_years=1,
        )

        for prospect in (candidate(), candidate(job_title="", skills=[], years_experience=0)):
            score, _ = score_prospect(prospect, filters)
            assert 0 <= score <= 100


class TestRunSearch:
    async def test_the_same_person_found_twice_is_one_row(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """Deduplication is what makes a suppression durable.

        If a second search inserted a second row for the same person,
        their do-not-contact flag would live on a record nobody looks at
        and the next campaign would call them.
        """
        request = SearchRequest(
            jd_text=BACKEND_JD,
            filters=SearchFilters(titles=["Backend Engineer"], cities=["Bengaluru"]),
            limit=10,
        )

        first = await run_search(session, settings, request)
        after_one = await session.scalar(select(func.count()).select_from(Prospect))
        second = await run_search(session, settings, request)
        after_two = await session.scalar(select(func.count()).select_from(Prospect))

        assert first.prospects, "the fixture provider must return something to deduplicate"
        assert after_one == after_two
        assert {p.id for p in first.prospects} == {p.id for p in second.prospects}

    async def test_a_refresh_never_clears_do_not_contact(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """Rediscovering someone must not undo their suppression.

        The upsert rewrites nearly every column from the provider payload,
        which has no opinion about suppression. One more assignment in
        that block would silently re-enable calling for a person who asked
        to be left alone.
        """
        request = SearchRequest(
            jd_text=BACKEND_JD,
            filters=SearchFilters(titles=["Backend Engineer"], cities=["Bengaluru"]),
            limit=10,
        )
        first = await run_search(session, settings, request)
        suppressed_id = first.prospects[0].id

        row = await session.get(Prospect, suppressed_id)
        assert row is not None
        row.do_not_contact = True
        await session.flush()

        second = await run_search(session, settings, request)

        refreshed = await session.get(Prospect, suppressed_id)
        assert refreshed is not None
        assert refreshed.do_not_contact is True
        assert [p.do_not_contact for p in second.prospects if p.id == suppressed_id] == [True]

    async def test_searching_never_makes_anybody_callable(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """Finding someone and being allowed to phone them are separate questions.

        The provider returns no dialable digits by design, so every result
        comes back not-callable with a reason. A search that started
        producing callable people would mean the consent gate had been
        bypassed somewhere upstream of it.
        """
        response = await run_search(
            session,
            settings,
            SearchRequest(jd_text=BACKEND_JD, filters=SearchFilters(cities=["Bengaluru"])),
        )

        assert response.callable_count == 0
        assert response.prospects
        for prospect in response.prospects:
            assert prospect.callable is False
            assert prospect.not_callable_reason

    async def test_a_search_records_how_its_filters_were_derived(
        self, session: AsyncSession, settings: Settings
    ) -> None:
        """A poor result set should be traceable to the interpretation.

        The suite runs with no model key, so the extraction falls back to
        keyword matching, and the response has to say so rather than
        presenting guessed filters as read ones.
        """
        response = await run_search(session, settings, SearchRequest(jd_text=BACKEND_JD))

        assert response.extraction_method == "heuristic"
        assert response.provider == "fixture"
        assert response.provider_query
        assert response.credits_charged == 0
