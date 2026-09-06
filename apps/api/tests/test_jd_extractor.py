"""Tests for filling the create-role form from a pasted job description.

The heuristic path gets most of the attention here, because it is what
runs when there is no model key, when the key runs out of credit, and
when the model is slow. A recruiter who pastes a description must always
get a filled form back, and it must always say how it was filled in.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import API

from app.hiring.services.jd_extractor import heuristic_draft

DELIVERY_JD = """
Delivery Executive - Bengaluru

Acme Logistics is hiring delivery executives for same-day parcel delivery
across Bengaluru. You will need your own two-wheeler and a valid driving
licence. Shifts are 9 hours with one weekly off. Earnings 18,000 to 24,000
per month including incentives.
"""

WAREHOUSE_JD = """
Night shift warehouse associates for our Hyderabad dark store. Picking,
packing and stock counts. Lifting up to 20kg is part of the job.
"""


class TestHeuristicDraft:
    def test_recognises_a_delivery_role(self) -> None:
        draft = heuristic_draft(DELIVERY_JD)
        assert draft.questions
        labels = {question.label.lower() for question in draft.questions}
        assert any("two-wheeler" in label for label in labels)

    def test_recognises_a_warehouse_role(self) -> None:
        draft = heuristic_draft(WAREHOUSE_JD)
        labels = {question.label.lower() for question in draft.questions}
        assert any("lift" in label or "shift" in label for label in labels)

    def test_finds_the_city(self) -> None:
        assert heuristic_draft(DELIVERY_JD).location == "Bengaluru"

    def test_infers_a_plausible_call_language_from_the_city(self) -> None:
        """Frontline screening in Hyderabad is not conducted in English."""
        assert heuristic_draft(WAREHOUSE_JD).language == "TELUGU"

    def test_finds_the_company(self) -> None:
        assert "Acme" in heuristic_draft(DELIVERY_JD).company_name

    def test_never_returns_an_empty_question_set(self) -> None:
        """An empty form helps nobody, so even nonsense gets a starting point."""
        draft = heuristic_draft("asdfghjkl")
        assert draft.questions

    def test_handles_empty_input(self) -> None:
        assert heuristic_draft("").questions

    def test_always_marks_itself_as_heuristic(self) -> None:
        """The UI warns more loudly for keyword-derived fields, so this
        label has to be honest."""
        draft = heuristic_draft(DELIVERY_JD)
        assert draft.source == "heuristic"
        assert draft.note

    def test_produces_labels_the_form_will_accept(self) -> None:
        for jd in (DELIVERY_JD, WAREHOUSE_JD, "generic office role"):
            draft = heuristic_draft(jd)
            labels = [question.label for question in draft.questions]
            assert len(labels) == len(set(labels)), "duplicate labels would merge columns"
            for question in draft.questions:
                assert 1 <= len(question.label) <= 120
                assert 3 <= len(question.text) <= 500
                assert 0 <= question.weight <= 10


class TestExtractEndpoint:
    async def test_fills_the_form_without_a_model_key(self, client: httpx.AsyncClient) -> None:
        """Tests run with no model key, so this exercises the fallback."""
        response = await client.post(f"{API}/jobs/extract", json={"jd_text": DELIVERY_JD})
        assert response.status_code == 200, response.text

        draft = response.json()
        assert draft["source"] == "heuristic"
        assert draft["note"], "a keyword-filled form must say so"
        assert draft["location"] == "Bengaluru"
        assert len(draft["questions"]) >= 3

    async def test_rejects_an_empty_description(self, client: httpx.AsyncClient) -> None:
        response = await client.post(f"{API}/jobs/extract", json={"jd_text": ""})
        assert response.status_code == 422

    async def test_saves_nothing(self, client: httpx.AsyncClient) -> None:
        """Extraction is a draft, not a commitment."""
        await client.post(f"{API}/jobs/extract", json={"jd_text": DELIVERY_JD})
        assert (await client.get(f"{API}/jobs")).json() == []

    async def test_the_draft_can_create_a_real_role(self, client: httpx.AsyncClient) -> None:
        """The whole point: what comes out must go straight back in.

        A draft the create endpoint rejects would leave the recruiter
        fixing validation errors on a form they did not fill in.
        """
        draft = (await client.post(f"{API}/jobs/extract", json={"jd_text": DELIVERY_JD})).json()

        created = await client.post(
            f"{API}/jobs",
            json={
                "title": draft["title"] or "Delivery Executive",
                "company_name": draft["company_name"] or "Acme",
                "location": draft["location"],
                "description_raw": DELIVERY_JD,
                "language": draft["language"],
                "voice_persona": draft["voice_persona"],
                "questions": [
                    {
                        "label": question["label"],
                        "text": question["text"],
                        "answer_type": question["answer_type"],
                        "enum_options": question["enum_options"] or None,
                        "weight": question["weight"],
                        "is_knockout": question["is_knockout"],
                    }
                    for question in draft["questions"]
                ],
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["preview"]["agent_prompt"]

    @pytest.mark.parametrize(
        ("jd", "expected_city"),
        [
            ("Hiring riders in Mumbai for parcel delivery.", "Mumbai"),
            ("Warehouse staff needed in Pune.", "Pune"),
            ("Security guards for Chennai stores.", "Chennai"),
            ("Fully remote role, no location given.", ""),
        ],
    )
    async def test_finds_the_city_across_descriptions(
        self, client: httpx.AsyncClient, jd: str, expected_city: str
    ) -> None:
        response = await client.post(f"{API}/jobs/extract", json={"jd_text": jd})
        assert response.json()["location"] == expected_city
