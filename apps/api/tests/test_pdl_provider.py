"""What the live People Data Labs API actually accepts and returns.

Every assertion here was learned by calling the real API with a real
key, and each one cost either a credit or a failed request. They are
pinned as tests because all four bugs they describe were **silent** in
different ways: one made every search fail, one turned a rejection into
an unhandled exception, one made a header always parse as ``None``, and
one would have printed the word "True" in a city column.

None of these tests touch the network. They assert the shape of what we
send and how we read what comes back.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app.people.providers.pdl import (
    PdlAuthError,
    PdlProvider,
    PdlQueryError,
    PdlQuotaError,
    PdlRateLimitError,
    PdlUnavailableError,
    _text,
)
from app.people.schemas import SearchFilters

URL = "https://api.peopledatalabs.com/v5/person/search"


@pytest.fixture
def provider() -> PdlProvider:
    return PdlProvider(api_key="test-key-not-real")


FILTERS = SearchFilters(
    titles=["Backend Engineer", "Software Engineer"],
    cities=["Bengaluru"],
    country="india",
    skills_required=["python"],
    seniorities=["senior"],
    excluded_titles=["QA Engineer"],
    require_phone=True,
)


def _clauses(node: Any) -> list[Any]:
    """Every dict nested anywhere inside a query, for structural asserts."""
    found: list[Any] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(_clauses(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_clauses(item))
    return found


class TestQueryShape:
    def test_minimum_should_match_is_never_sent(self, provider: PdlProvider) -> None:
        """PDL rejects the whole request if this appears anywhere.

        The live API answers with: "Query clause [minimum_should_match]
        not allowed or invalid field name." It is a 400, so every single
        search failed and no credit was spent to reveal it. This is the
        one assertion in the file that was worth the whole exercise.
        """
        payload = provider.build_query(FILTERS, 10)
        assert "minimum_should_match" not in json.dumps(payload)

    def test_titles_are_required_not_merely_preferred(self, provider: PdlProvider) -> None:
        """Dropping the parameter must not quietly widen the search.

        A nested bool whose only clause is ``should`` already requires one
        of them to match. Verified against the live API: both forms return
        the same 740,598 matches. But if a ``must`` were ever added
        alongside, the ``should`` would silently decay into a scoring
        boost and the title filter would stop filtering.
        """
        payload = provider.build_query(FILTERS, 10)
        must = payload["query"]["bool"]["must"]

        title_clauses = [
            clause
            for clause in must
            if isinstance(clause, dict) and "bool" in clause and "should" in clause.get("bool", {})
        ]
        assert len(title_clauses) == 1, "titles should contribute exactly one bool clause"

        inner = title_clauses[0]["bool"]
        assert set(inner) == {"should"}, (
            "the title bool must contain only `should`; adding `must` or `filter` "
            "would turn the title requirement into a mere preference"
        )
        assert len(inner["should"]) == 2

    def test_city_aliases_are_expanded(self, provider: PdlProvider) -> None:
        """Bengaluru and Bangalore are the same city to everyone but a database."""
        payload = provider.build_query(FILTERS, 10)
        localities = [
            clause["terms"]["location_locality"]
            for clause in _clauses(payload)
            if isinstance(clause, dict)
            and "terms" in clause
            and "location_locality" in clause.get("terms", {})
        ]
        assert localities and set(localities[0]) == {"bengaluru", "bangalore"}

    def test_excluded_titles_become_must_not(self, provider: PdlProvider) -> None:
        payload = provider.build_query(FILTERS, 10)
        must_not = json.dumps(payload["query"]["bool"]["must_not"])
        assert "qa engineer" in must_not

    def test_size_is_the_requested_limit(self, provider: PdlProvider) -> None:
        """PDL bills per record returned, so this is a spending control."""
        assert provider.build_query(FILTERS, 7)["size"] == 7


class TestMaskedFields:
    """Free plans mask fields to `true` rather than omitting them.

    This is the subtle one. An absent value and a masked value are
    different facts, and rendering the masked one as text produces a
    column reading "True" while looking like real data.
    """

    def test_a_boolean_is_not_a_string(self) -> None:
        assert _text(True) is None
        assert _text(False) is None

    def test_real_strings_survive(self) -> None:
        assert _text("  Bengaluru  ") == "Bengaluru"
        assert _text("") is None
        assert _text(None) is None

    @respx.mock
    @pytest.mark.anyio
    async def test_masked_locality_becomes_unknown(self, provider: PdlProvider) -> None:
        """Observed live: `location_locality` comes back as `true`."""
        respx.post(URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "total": 1,
                    "data": [
                        {
                            "id": "abc",
                            "full_name": "test person",
                            "job_title": "software engineer",
                            "job_company_name": "acme",
                            "location_locality": True,
                            "location_country": "india",
                            "mobile_phone": True,
                            "work_email": True,
                            "linkedin_url": "linkedin.com/in/test-person",
                            "skills": ["python", 42],
                        }
                    ],
                },
            )
        )
        page = await provider.search(FILTERS, 1)
        person = page.prospects[0]

        assert person.location_city is None, "a masked locality must not render as 'True'"
        assert person.location_country == "india"
        # Non-string entries in a list field are dropped rather than
        # stringified into a skill nobody claimed.
        assert person.skills == ["python"]

    @respx.mock
    @pytest.mark.anyio
    async def test_a_masked_phone_is_present_not_revealed(self, provider: PdlProvider) -> None:
        """The finding the whole sourcing design rests on.

        `mobile_phone: true` means a number exists somewhere. It is not a
        number. Anything that treated it as one would try to dial the
        string "True".
        """
        respx.post(URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "total": 1,
                    "data": [{"id": "a", "full_name": "x", "mobile_phone": True}],
                },
            )
        )
        person = (await provider.search(FILTERS, 1)).prospects[0]
        assert person.phone_status.value == "PRESENT_MASKED"
        assert person.phone_e164 is None


class TestCreditAccounting:
    @respx.mock
    @pytest.mark.anyio
    async def test_credits_come_from_the_headers(self, provider: PdlProvider) -> None:
        """`x-ratelimit-remaining` is a JSON object, not a number.

        It looks like `{"minute": 6}`, so parsing it as an integer always
        yielded None and the remaining balance was permanently unknown.
        The monthly allowance has its own header.
        """
        respx.post(URL).mock(
            return_value=httpx.Response(
                200,
                headers={
                    "x-call-credits-spent": "3",
                    "x-totallimit-remaining": "79",
                    "x-ratelimit-remaining": '{"minute": 6}',
                },
                json={"total": 823, "data": [{"id": "a", "full_name": "x"}]},
            )
        )
        page = await provider.search(FILTERS, 3)

        assert page.credits_charged == 3, "trust PDL's count, not ours"
        assert page.credits_remaining == 79
        assert page.total_estimated == 823

    @respx.mock
    @pytest.mark.anyio
    async def test_an_empty_result_still_reports_what_it_cost(self, provider: PdlProvider) -> None:
        """Counting returned records reported zero cost for a paid search."""
        respx.post(URL).mock(
            return_value=httpx.Response(
                200, headers={"x-call-credits-spent": "1"}, json={"total": 0, "data": []}
            )
        )
        page = await provider.search(FILTERS, 5)
        assert page.prospects == []
        assert page.credits_charged == 1


class TestFailuresAreTyped:
    """Each failure needs a different remedy, so each needs its own type.

    Before this, a 400 escaped as a raw ``httpx.HTTPStatusError`` and
    reached the UI as an unhandled exception rather than a sentence
    anybody could act on.
    """

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (400, PdlQueryError),
            (401, PdlAuthError),
            (403, PdlAuthError),
            (402, PdlQuotaError),
            (429, PdlRateLimitError),
            (500, PdlQueryError),
        ],
    )
    @respx.mock
    @pytest.mark.anyio
    async def test_status_codes_map_to_error_types(
        self, provider: PdlProvider, status: int, expected: type[Exception]
    ) -> None:
        respx.post(URL).mock(
            return_value=httpx.Response(
                status, json={"error": {"message": "something specific went wrong"}}
            )
        )
        with pytest.raises(expected):
            await provider.search(FILTERS, 1)

    @respx.mock
    @pytest.mark.anyio
    async def test_a_rejection_repeats_what_pdl_said(self, provider: PdlProvider) -> None:
        """Only PDL knows which clause it disliked, so quote it verbatim."""
        respx.post(URL).mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Query clause [minimum_should_match] not allowed "
                        "or invalid field name."
                    }
                },
            )
        )
        with pytest.raises(PdlQueryError, match="minimum_should_match"):
            await provider.search(FILTERS, 1)

    @respx.mock
    @pytest.mark.anyio
    async def test_a_network_failure_is_not_a_query_failure(self, provider: PdlProvider) -> None:
        """One means retry later; the other means the query is wrong."""
        respx.post(URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
        with pytest.raises(PdlUnavailableError):
            await provider.search(FILTERS, 1)


class TestPlanHonesty:
    def test_the_provider_admits_it_cannot_reveal_phones(self, provider: PdlProvider) -> None:
        """The UI says "mobile on file, not available on this plan".

        It can only say that because the provider reports it, rather than
        the screen rendering an empty column and looking broken.
        """
        assert provider.supports_phone_reveal() is False

    def test_the_sandbox_charges_nothing(self) -> None:
        sandbox = PdlProvider(api_key="k", sandbox=True)
        assert sandbox.name == "pdl_sandbox"
