"""What counts as a searchable request.

There are two honest ways to start a search: paste a job description, or
say directly who you are looking for. The contract used to permit only
the first, which made the second a form you had to trick by typing
something into a box you did not want.

The other half of the rule matters more than it looks: filters that
narrow nothing are refused. A search with no constraints returns an
arbitrary slice of a database of hundreds of millions of people, one
credit per row, and the recruiter learns nothing from it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.people.schemas import SearchFilters, SearchRequest


class TestWhatCanBeSearched:
    def test_a_description_alone_is_enough(self) -> None:
        request = SearchRequest(jd_text="Hiring a backend engineer in Pune.")
        assert request.filters is None, "extraction still has to run"

    def test_filters_alone_are_enough(self) -> None:
        """The route that used to be impossible.

        `jd_text` was required, so a recruiter who already knew exactly
        who they wanted had to write a job advert to be allowed to say so.
        """
        request = SearchRequest(filters=SearchFilters(titles=["Data Engineer"]))
        assert request.jd_text == ""

    def test_both_together_are_fine(self) -> None:
        """The normal path: read a description, then correct the result."""
        request = SearchRequest(
            jd_text="Hiring a data engineer.",
            filters=SearchFilters(titles=["Data Engineer"], cities=["Pune"]),
        )
        assert request.filters is not None

    def test_neither_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="job description or some filters"):
            SearchRequest()

    def test_whitespace_is_not_a_description(self) -> None:
        with pytest.raises(ValidationError, match="job description or some filters"):
            SearchRequest(jd_text="   \n\t  ")


class TestEmptyFiltersAreRefused:
    def test_filters_that_narrow_nothing(self) -> None:
        with pytest.raises(ValidationError, match="nothing to narrow on"):
            SearchRequest(filters=SearchFilters())

    def test_country_alone_narrows_nothing(self) -> None:
        """Every record has a country, so filtering on it selects everybody."""
        with pytest.raises(ValidationError, match="nothing to narrow on"):
            SearchRequest(filters=SearchFilters(country="india"))

    def test_a_role_pitch_is_not_a_search_term(self) -> None:
        """These fields are spoken to the candidate, not sent to the provider.

        Counting them would let a recruiter run an unconstrained search by
        filling in the one field that has no effect on it.
        """
        with pytest.raises(ValidationError, match="nothing to narrow on"):
            SearchRequest(
                filters=SearchFilters(
                    hiring_title="Senior Data Engineer",
                    company_name="Razorpay",
                    role_pitch="a fintech hiring a data engineer",
                    comp_range_text="38 to 55 lakhs",
                    work_mode="hybrid",
                )
            )

    @pytest.mark.parametrize(
        "filters",
        [
            SearchFilters(titles=["Data Engineer"]),
            SearchFilters(cities=["Pune"]),
            SearchFilters(skills_required=["python"]),
            SearchFilters(skills_nice=["kafka"]),
            SearchFilters(seniorities=["senior"]),
            SearchFilters(industries=["fintech"]),
            SearchFilters(min_years=5),
            SearchFilters(max_years=9),
        ],
        ids=[
            "title",
            "city",
            "required skill",
            "nice skill",
            "seniority",
            "industry",
            "min years",
            "max years",
        ],
    )
    def test_any_single_real_constraint_is_enough(self, filters: SearchFilters) -> None:
        """One genuine constraint makes a search worth running."""
        assert SearchRequest(filters=filters).filters is not None

    def test_a_description_rescues_empty_filters(self) -> None:
        """Both halves of the rule, interacting.

        Empty filters are refused even alongside a description, because
        the recruiter has explicitly said "search for nothing in
        particular" and the description would be ignored.
        """
        with pytest.raises(ValidationError, match="nothing to narrow on"):
            SearchRequest(jd_text="Hiring a data engineer.", filters=SearchFilters())


class TestIsEmpty:
    """`SearchFilters.is_empty` is shared with the UI's disabled state.

    The button and the validator must agree, or the user gets a server
    error for something the screen let them do.
    """

    def test_default_filters_are_empty(self) -> None:
        assert SearchFilters().is_empty() is True

    def test_a_title_is_not_empty(self) -> None:
        assert SearchFilters(titles=["Backend Engineer"]).is_empty() is False

    def test_zero_years_counts_as_a_constraint(self) -> None:
        """Zero is a value, not an absence.

        `min_years=0` is a deliberate "no floor", which is different from
        not having said anything, and a truthiness check would lose it.
        """
        assert SearchFilters(min_years=0).is_empty() is False
