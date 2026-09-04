"""End-to-end tests for the hiring flow.

These run the whole stack: HTTP router, service layer, database and the
demo voice client, including its genuinely signed webhooks. What they
prove is that the pieces fit, which unit tests deliberately do not.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from conftest import API, sample_job


async def create_job(client: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await client.post(f"{API}/jobs", json=sample_job(**overrides))
    assert response.status_code == 201, response.text
    return dict(response.json())


async def add_candidate(
    client: httpx.AsyncClient, job_id: str, name: str, number: str
) -> dict[str, Any]:
    response = await client.post(
        f"{API}/jobs/{job_id}/candidates", json={"name": name, "mobile_number": number}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def settle(client: httpx.AsyncClient, job_id: str, attempts: int = 60) -> dict[str, Any]:
    """Poll results until no call is still running.

    Mirrors exactly what the frontend does, which is the only way live
    status works at all: Hunar pushes a status webhook only once a call
    has finished, so progress has to be polled for.
    """
    payload: dict[str, Any] = {}
    for _ in range(attempts):
        response = await client.get(f"{API}/jobs/{job_id}/results")
        assert response.status_code == 200, response.text
        payload = response.json()
        if not payload["in_progress"] and payload["completed"] >= 0:
            statuses = {row["status"] for row in payload["rows"]}
            if statuses and statuses <= {
                "COMPLETED",
                "NOT_CONNECTED",
                "FAILED",
                "CANCELLED",
                "NOT_STARTED",
            }:
                return payload
        await asyncio.sleep(0.4)
    return payload


class TestJobLifecycle:
    async def test_creates_a_job_and_derives_its_agent_script(
        self, client: httpx.AsyncClient
    ) -> None:
        job = await create_job(client)

        assert job["status"] == "DRAFT"
        assert len(job["questions"]) == 3
        # Columns are derived, not declared: three questions plus the
        # system fields every job carries.
        assert len(job["field_spec"]) > 3

        preview = job["preview"]
        assert preview is not None
        assert "{candidate_name}" in preview["agent_prompt"]
        assert "two-wheeler" in preview["agent_prompt"]
        assert "years_of_experience" in preview["result_schema"]

    async def test_preview_needs_no_persistence(self, client: httpx.AsyncClient) -> None:
        """The create-job form previews the script before anything is saved."""
        response = await client.post(
            f"{API}/jobs/preview",
            json={
                "title": "Warehouse Picker",
                "company_name": "Acme",
                "description_raw": "Night shift warehouse work.",
                "questions": [
                    {
                        "label": "Night shift",
                        "text": "Can you work night shifts?",
                        "answer_type": "BOOLEAN",
                    }
                ],
            },
        )
        assert response.status_code == 200
        assert "night" in response.json()["agent_prompt"].lower()

        listing = await client.get(f"{API}/jobs")
        assert listing.json() == []

    async def test_job_description_braces_cannot_reach_the_prompt(
        self, client: httpx.AsyncClient
    ) -> None:
        """A pasted brace would otherwise corrupt what the agent says aloud."""
        job = await create_job(client, description_raw='Stack is {"lang": "python"}, pay {35 LPA}')
        prompt = job["preview"]["agent_prompt"]
        assert '{"lang"' not in prompt
        assert "{35 LPA}" not in prompt
        assert "35 LPA" in prompt

    async def test_rejects_duplicate_question_labels(self, client: httpx.AsyncClient) -> None:
        """Two questions sharing a label would collapse into one column."""
        payload = sample_job()
        payload["questions"][1]["label"] = payload["questions"][0]["label"]
        response = await client.post(f"{API}/jobs", json=payload)
        assert response.status_code == 422

    async def test_lists_jobs_with_counts(self, client: httpx.AsyncClient) -> None:
        job = await create_job(client)
        await add_candidate(client, job["id"], "Asha", "+919876543210")

        listing = (await client.get(f"{API}/jobs")).json()
        assert len(listing) == 1
        assert listing[0]["candidate_count"] == 1
        assert listing[0]["call_count"] == 0

    async def test_archives_rather_than_deletes(self, client: httpx.AsyncClient) -> None:
        """Screening results describe real conversations with real people."""
        job = await create_job(client)
        assert (await client.delete(f"{API}/jobs/{job['id']}")).status_code == 204

        assert (await client.get(f"{API}/jobs")).json() == []
        # Still retrievable directly, so a past decision remains auditable.
        assert (await client.get(f"{API}/jobs/{job['id']}")).status_code == 200


class TestCandidates:
    async def test_normalises_an_indian_mobile_number(self, client: httpx.AsyncClient) -> None:
        job = await create_job(client)
        candidate = await add_candidate(client, job["id"], "Asha", "+91 98765 43210")
        assert candidate["mobile_number"] == "+919876543210"

    async def test_masks_the_number_for_display(self, client: httpx.AsyncClient) -> None:
        """Nobody needs a full phone number rendered in a browser."""
        job = await create_job(client)
        candidate = await add_candidate(client, job["id"], "Asha", "+919876543210")
        assert candidate["mobile_masked"].endswith("3210")
        assert "9876543210" not in candidate["mobile_masked"]

    async def test_refuses_the_same_number_twice_on_one_role(
        self, client: httpx.AsyncClient
    ) -> None:
        """Adding a duplicate would mean phoning one person twice."""
        job = await create_job(client)
        await add_candidate(client, job["id"], "Asha", "+919876543210")

        response = await client.post(
            f"{API}/jobs/{job['id']}/candidates",
            json={"name": "Asha again", "mobile_number": "+919876543210"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"

    async def test_imports_a_spreadsheet(self, client: httpx.AsyncClient) -> None:
        job = await create_job(client)
        csv = (
            "Name,Mobile,City\n"
            "Asha Rao,9876543210,Bengaluru\n"
            "Ravi Kumar,+91 98765 43211,Pune\n"
            "Priya S,98765 43212,Chennai\n"
        )
        response = await client.post(
            f"{API}/jobs/{job['id']}/candidates/import",
            files={"file": ("candidates.csv", csv, "text/csv")},
        )
        assert response.status_code == 200, response.text

        report = response.json()
        assert report["imported"] == 3
        assert report["rejected"] == []
        # An unmapped column rides along so a prompt can reference it.
        assert report["candidates"][0]["mobile_number"] == "+919876543210"

    async def test_import_reports_bad_rows_rather_than_dropping_them(
        self, client: httpx.AsyncClient
    ) -> None:
        """Someone who uploads 200 rows needs to know which ones were lost."""
        job = await create_job(client)
        csv = (
            "Name,Mobile\nGood One,9876543210\nBad One,not-a-number\nEmpty,\nDuplicate,9876543210\n"
        )
        response = await client.post(
            f"{API}/jobs/{job['id']}/candidates/import",
            files={"file": ("candidates.csv", csv, "text/csv")},
        )
        report = response.json()

        assert report["imported"] == 1
        assert report["skipped_duplicates"] == 1
        assert len(report["rejected"]) == 2
        assert all("reason" in row for row in report["rejected"])

    async def test_import_requires_a_phone_column(self, client: httpx.AsyncClient) -> None:
        job = await create_job(client)
        response = await client.post(
            f"{API}/jobs/{job['id']}/candidates/import",
            files={"file": ("bad.csv", "Name,Email\nAsha,a@b.com\n", "text/csv")},
        )
        assert response.status_code == 422
        assert "phone" in response.json()["error"]["message"].lower()

    async def test_records_a_recruiter_decision(self, client: httpx.AsyncClient) -> None:
        """The score ranks; a person decides."""
        job = await create_job(client)
        candidate = await add_candidate(client, job["id"], "Asha", "+919876543210")

        response = await client.patch(
            f"{API}/jobs/{job['id']}/candidates/{candidate['id']}/decision",
            json={"decision": "SHORTLISTED", "note": "Strong on experience"},
        )
        assert response.status_code == 200
        assert response.json()["decision"] == "SHORTLISTED"


class TestScreeningEndToEnd:
    async def test_runs_a_full_screening_round(self, client: httpx.AsyncClient) -> None:
        """The headline test: job to agent to calls to scored results."""
        job = await create_job(client)
        job_id = job["id"]

        for index in range(6):
            await add_candidate(client, job_id, f"Candidate {index}", f"+91987654321{index}")

        launch = await client.post(f"{API}/jobs/{job_id}/calls/launch", json={})
        assert launch.status_code == 200, launch.text
        assert launch.json()["launched"] == 6

        # The agent is created on first launch, not at job creation.
        detail = (await client.get(f"{API}/jobs/{job_id}")).json()
        assert detail["hunar_agent_id"] is not None
        assert detail["status"] in {"CALLING", "DONE"}

        results = await settle(client, job_id)
        assert results["total"] == 6
        assert results["in_progress"] is False

        completed = [row for row in results["rows"] if row["status"] == "COMPLETED"]
        assert completed, "no call completed"

        for row in completed:
            # Coerced values and the literal strings behind them are both
            # present, so a recruiter can always check the interpretation.
            assert row["values"]
            assert row["raw_values"]
            assert row["score"] is not None or row["disqualified"]

    async def test_results_carry_their_own_column_definitions(
        self, client: httpx.AsyncClient
    ) -> None:
        """One endpoint and one table serve every differently shaped role."""
        job = await create_job(client)
        await add_candidate(client, job["id"], "Asha", "+919876543210")
        await client.post(f"{API}/jobs/{job['id']}/calls/launch", json={})
        results = await settle(client, job["id"])

        keys = {column["key"] for column in results["columns"]}
        assert "years_of_experience" in keys
        assert "owns_two_wheeler" in keys
        assert any(column["system"] for column in results["columns"])

        for row in results["rows"]:
            assert set(row["values"]) <= keys

    async def test_coerces_answers_into_typed_values(self, client: httpx.AsyncClient) -> None:
        """The demo client returns strings, exactly as the real API does."""
        job = await create_job(client)
        for index in range(8):
            await add_candidate(client, job["id"], f"C{index}", f"+91987654322{index}")
        await client.post(f"{API}/jobs/{job['id']}/calls/launch", json={})
        results = await settle(client, job["id"])

        checked = 0
        for row in results["rows"]:
            if row["status"] != "COMPLETED":
                continue
            checked += 1
            experience = row["values"].get("years_of_experience")
            assert experience is None or isinstance(experience, int | float)
            owns = row["values"].get("owns_two_wheeler")
            assert owns is None or isinstance(owns, bool)
            # The raw string survives beside the coerced value.
            assert isinstance(row["raw_values"].get("years_of_experience"), str)
        assert checked

    async def test_explains_every_score(self, client: httpx.AsyncClient) -> None:
        """A recruiter must never see a number without its reasoning."""
        job = await create_job(client)
        for index in range(6):
            await add_candidate(client, job["id"], f"C{index}", f"+91987654323{index}")
        await client.post(f"{API}/jobs/{job['id']}/calls/launch", json={})
        results = await settle(client, job["id"])

        scored = [row for row in results["rows"] if row["score_breakdown"]]
        assert scored
        for row in scored:
            contributions = row["score_breakdown"]["contributions"]
            assert contributions
            for contribution in contributions:
                assert contribution["explanation"]

    async def test_does_not_call_the_same_candidate_twice(self, client: httpx.AsyncClient) -> None:
        """Being phoned twice by one employer about one role is a bad experience."""
        job = await create_job(client)
        await add_candidate(client, job["id"], "Asha", "+919876543210")

        first = await client.post(f"{API}/jobs/{job['id']}/calls/launch", json={})
        assert first.json()["launched"] == 1

        second = await client.post(f"{API}/jobs/{job['id']}/calls/launch", json={})
        assert second.json()["launched"] == 0
        assert second.json()["skipped"] == 1

    async def test_launching_with_no_candidates_is_not_an_error(
        self, client: httpx.AsyncClient
    ) -> None:
        job = await create_job(client)
        response = await client.post(f"{API}/jobs/{job['id']}/calls/launch", json={})
        assert response.status_code == 200
        assert response.json()["launched"] == 0


class TestQuestionEditingSafety:
    async def test_allows_editing_before_any_call(self, client: httpx.AsyncClient) -> None:
        job = await create_job(client)
        response = await client.patch(
            f"{API}/jobs/{job['id']}",
            json={
                "questions": [
                    {
                        "label": "Can start immediately",
                        "text": "Can you start this week?",
                        "answer_type": "BOOLEAN",
                    }
                ]
            },
        )
        assert response.status_code == 200
        assert len(response.json()["questions"]) == 1

    async def test_refuses_editing_once_results_exist(self, client: httpx.AsyncClient) -> None:
        """Stored answers only mean anything against the schema that produced them.

        Silently changing the questions would change what past results
        mean, which is worse than refusing the edit.
        """
        job = await create_job(client)
        await add_candidate(client, job["id"], "Asha", "+919876543210")
        await client.post(f"{API}/jobs/{job['id']}/calls/launch", json={})

        response = await client.patch(
            f"{API}/jobs/{job['id']}",
            json={
                "questions": [
                    {"label": "Different", "text": "Something else?", "answer_type": "STRING"}
                ]
            },
        )
        assert response.status_code == 409
        assert "duplicate the role" in response.json()["error"]["message"].lower()

    async def test_still_allows_editing_descriptive_fields(self, client: httpx.AsyncClient) -> None:
        """Fixing a typo in the title must not be blocked by the same rule."""
        job = await create_job(client)
        await add_candidate(client, job["id"], "Asha", "+919876543210")
        await client.post(f"{API}/jobs/{job['id']}/calls/launch", json={})

        response = await client.patch(
            f"{API}/jobs/{job['id']}", json={"title": "Delivery Executive (Night)"}
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Delivery Executive (Night)"


class TestErrorHandling:
    async def test_unknown_job_returns_the_standard_envelope(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(f"{API}/jobs/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    @pytest.mark.parametrize("number", ["9876543210", "not a number", "+91", "+9198765432109999"])
    async def test_rejects_an_undialable_number(
        self, client: httpx.AsyncClient, number: str
    ) -> None:
        job = await create_job(client)
        response = await client.post(
            f"{API}/jobs/{job['id']}/candidates",
            json={"name": "Asha", "mobile_number": number},
        )
        assert response.status_code == 422
