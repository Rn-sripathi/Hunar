"""Tests for turning a pasted job description into search filters.

The keyword tier is what gets exercised here, and that is deliberate. It
runs when there is no model key, when the key runs out of credit, and
when the model is slow, which between them covers most of the time this
product will actually be used. A recruiter who pastes a description must
always get an editable filter set back, and it must always say how it was
filled in.

The role-agnostic case matters more than it looks. Every fixture in the
brief is an engineering role, so an extractor that quietly assumes one
would pass a whole suite and then produce nonsense the first time
somebody sources a warehouse supervisor.
"""

from __future__ import annotations

from typing import Any, NoReturn

import pytest

from app.core.config import Settings
from app.people.services.extraction import extract_filters, heuristic_filters

BACKEND_JD = """
Job Title: Senior Backend Engineer
Location: Bangalore

We are looking for someone with at least 6 years of experience building
Python and PostgreSQL services for our payments platform.
"""

WAREHOUSE_JD = """
Warehouse Supervisor - Coimbatore

We need a shift supervisor for our Coimbatore fulfilment centre. At least 4 years
of warehouse or logistics supervision. You will run a team of thirty pickers,
stock counts and safety compliance. On-site, six days a week.
"""

SALES_JD = """
Field Sales Executive - Jaipur

Selling point-of-sale terminals to kirana stores across Jaipur. At least
2 years of field sales experience and your own two-wheeler. Fixed pay plus
a monthly incentive on targets achieved.
"""


class TestHeuristicFilters:
    def test_finds_the_city(self) -> None:
        assert heuristic_filters(BACKEND_JD).cities == ["Bengaluru"]

    def test_canonicalises_the_names_indian_cities_go_by(self) -> None:
        """Descriptions say Bangalore; the provider indexes Bengaluru.

        Passing the pasted spelling straight through returns nothing at
        all, which looks like an empty city rather than a naming mismatch.
        """
        assert heuristic_filters("Hiring in Bangalore.").cities == ["Bengaluru"]
        assert heuristic_filters("Hiring in Gurgaon.").cities == ["Gurugram"]

    def test_finds_the_years_of_experience(self) -> None:
        """The floor is a filter the provider charges to get wrong."""
        assert heuristic_filters(BACKEND_JD).min_years == 6

    def test_never_invents_a_floor_that_was_not_stated(self) -> None:
        """A guessed minimum silently excludes people the recruiter wanted."""
        assert heuristic_filters("Hiring backend engineers in Pune.").min_years is None

    def test_reads_the_title_off_a_labelled_line(self) -> None:
        assert heuristic_filters(BACKEND_JD).titles == ["Senior Backend Engineer"]

    @pytest.mark.parametrize("jd", [BACKEND_JD, WAREHOUSE_JD, SALES_JD, "", "asdfghjkl"])
    def test_always_returns_something_the_search_form_can_render(self, jd: str) -> None:
        """The screen must never dead-end on a description it could not read."""
        filters = heuristic_filters(jd)

        assert len(filters.role_pitch) <= 240
        assert len(filters.titles) <= 1
        assert all(title.strip() for title in filters.titles)


class TestRoleAgnostic:
    """The product is for frontline and non-technical hiring too."""

    def test_reads_a_warehouse_role_without_inventing_engineering(self) -> None:
        """Nothing in this description is a programming language.

        A skills list that came back with anything in it would mean the
        keyword tier was matching substrings rather than words, which is
        how "go" ends up in every description ever written.
        """
        filters = heuristic_filters(WAREHOUSE_JD)

        assert filters.titles == ["Warehouse Supervisor"]
        assert filters.cities == ["Coimbatore"]
        assert filters.min_years == 4
        assert filters.work_mode == "onsite"
        assert filters.skills_required == []
        assert filters.skills_nice == []

    def test_reads_a_sales_role_the_same_way(self) -> None:
        filters = heuristic_filters(SALES_JD)

        assert filters.titles == ["Field Sales Executive"]
        assert filters.cities == ["Jaipur"]
        assert filters.min_years == 2
        assert filters.skills_required == []
        assert "Field Sales Executive" in filters.role_pitch
        assert "Jaipur" in filters.role_pitch


class TestExtractFilters:
    async def test_without_a_key_it_uses_keywords_and_admits_it(self, settings: Settings) -> None:
        """The UI warns more loudly for guessed filters, so the label must be honest."""
        result = await extract_filters(BACKEND_JD, settings)

        assert result.method == "heuristic"
        assert result.note
        assert "keyword" in result.note.lower()
        assert result.filters.cities == ["Bengaluru"]

    async def test_without_a_key_no_client_is_ever_constructed(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A paid API must not be reachable from a test run at all.

        Constructing the client is the step before spending money, so
        exploding on construction catches the mistake one move earlier
        than asserting on the returned method would.
        """

        def refuse(*args: Any, **kwargs: Any) -> NoReturn:
            raise AssertionError("the extractor tried to build an OpenAI client")

        monkeypatch.setattr("app.people.services.extraction.AsyncOpenAI", refuse)

        result = await extract_filters(BACKEND_JD, settings)

        assert result.method == "heuristic"

    async def test_an_empty_description_costs_nothing_and_returns_empty_filters(
        self, settings: Settings
    ) -> None:
        """Whitespace is not worth a model call, and must not raise either."""
        result = await extract_filters("   \n  ", settings)

        assert result.method == "heuristic"
        assert result.filters.titles == []
        assert result.filters.cities == []

    async def test_a_non_technical_description_survives_the_whole_path(
        self, settings: Settings
    ) -> None:
        """End to end on the tier that actually runs, for a role that is not code."""
        result = await extract_filters(WAREHOUSE_JD, settings)

        assert result.filters.titles == ["Warehouse Supervisor"]
        assert result.filters.skills_required == []
