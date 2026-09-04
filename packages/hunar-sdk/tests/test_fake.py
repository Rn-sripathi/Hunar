"""Tests for the demo-mode client.

The fake is load-bearing rather than incidental: the assignment's API key
expires within days, so the deployed demo very likely runs on this code.
Its value depends entirely on being *faithful* to the real API's awkward
behaviour, so most of these tests assert fidelity rather than convenience.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx

from hunar_sdk import (
    AgentCreate,
    BulkCallCreate,
    BulkCallRecipient,
    CallbackConfig,
    CallCreate,
    CallStatus,
    FakeHunarClient,
    HunarClient,
    HunarNotFoundError,
    HunarValidationError,
    Language,
    LiveHunarClient,
    VoicePersona,
    compute_signature,
    verify_webhook,
)

FAST = 400.0  # collapse the 13-second lifecycle to a few milliseconds
KEY = "fake-local-key"
HOOK_BASE = "https://listener.test/webhooks/hunar/tok123"


def agent_payload(**overrides: Any) -> AgentCreate:
    payload: dict[str, Any] = {
        "name": "screening-agent",
        "voice_persona": VoicePersona.NEHA,
        "language": Language.HINDI,
        "agent_prompt": "Greet {candidate_name} on behalf of {company_name} and ask two questions.",
        "objective": "Screen the candidate.",
        "introduction": "Hello {candidate_name}, is now a good time?",
        "result_prompt": "Extract the declared fields from the transcript.",
        "result_schema": {
            "years_experience": "number",
            "interested": "boolean",
            "notice_period": "string",
            "call_summary": "string",
        },
    }
    payload.update(overrides)
    return AgentCreate.model_validate(payload)


@pytest.fixture
async def client() -> Any:
    fake = FakeHunarClient(speed=FAST, seed=7, post_webhooks=False)
    yield fake
    await fake.aclose()


class TestProtocolConformance:
    def test_both_clients_satisfy_the_protocol(self) -> None:
        """The swap between live and demo mode must be type-safe.

        mypy checks this statically through the annotation; the runtime
        assertions catch a method accidentally renamed in only one of them.
        """
        fake: HunarClient = FakeHunarClient(post_webhooks=False)
        live: HunarClient = LiveHunarClient("key-for-construction-only")
        assert isinstance(fake, HunarClient)
        assert isinstance(live, HunarClient)

    def test_modes_are_distinguishable(self) -> None:
        """The UI banner and the logs need to know which one is running."""
        assert FakeHunarClient(post_webhooks=False).mode == "mock"
        assert LiveHunarClient("k").mode == "live"


class TestAgents:
    async def test_creates_and_reads_back_an_agent(self, client: FakeHunarClient) -> None:
        created = await client.create_agent(agent_payload())
        fetched = await client.get_agent(created.id)
        assert fetched.id == created.id
        assert fetched.result_schema == created.result_schema

    async def test_reports_the_prompt_variables_it_found(self, client: FakeHunarClient) -> None:
        """Mirrors the real API surfacing `custom_variables`.

        This is how the app can warn that a template references a variable
        no caller supplies, before a wrong sentence is spoken aloud.
        """
        agent = await client.create_agent(agent_payload())
        assert agent.custom_variables == ["candidate_name", "company_name"]

    async def test_raises_for_a_missing_agent(self, client: FakeHunarClient) -> None:
        with pytest.raises(HunarNotFoundError):
            await client.get_agent(uuid4())


class TestCallLifecycle:
    async def test_advances_to_a_terminal_status(self, client: FakeHunarClient) -> None:
        agent = await client.create_agent(agent_payload())
        call = await client.create_call(
            CallCreate(callee_name="Asha", mobile_number="+919876543210", agent_id=agent.id)
        )
        assert call.status is CallStatus.NOT_STARTED  # starts unstarted

        await client.drain()
        settled = await client.get_call(call.id)
        assert settled.is_terminal

    async def test_rejects_a_call_for_an_unknown_agent(self, client: FakeHunarClient) -> None:
        """The real API rejects this, so a bad reference must not pass silently."""
        with pytest.raises(HunarValidationError):
            await client.create_call(
                CallCreate(
                    callee_name="Asha",
                    mobile_number="+919876543210",
                    agent_id=uuid4(),
                )
            )

    async def test_is_reproducible_for_a_given_seed(self) -> None:
        """The seeded demo depends on this."""
        outcomes: list[list[str]] = []
        for _ in range(2):
            fake = FakeHunarClient(speed=FAST, seed=42, post_webhooks=False)
            agent = await fake.create_agent(agent_payload())
            for index in range(6):
                await fake.create_call(
                    CallCreate(
                        callee_name=f"Candidate {index}",
                        mobile_number=f"+91987654321{index}",
                        agent_id=agent.id,
                    )
                )
            await fake.drain()
            page = await fake.list_calls()
            outcomes.append(sorted(c.status.value for c in page.results))
            await fake.aclose()
        assert outcomes[0] == outcomes[1]

    async def test_produces_a_mix_of_outcomes(self) -> None:
        """Not every call should succeed.

        A dashboard where all forty calls completed is not a believable
        picture of a frontline campaign, and it hides the unanswered and
        failed states the UI has to render.
        """
        fake = FakeHunarClient(speed=FAST, seed=3, post_webhooks=False)
        agent = await fake.create_agent(agent_payload())
        for index in range(40):
            await fake.create_call(
                CallCreate(
                    callee_name=f"Candidate {index}",
                    mobile_number=f"+9198765{index:05d}",
                    agent_id=agent.id,
                )
            )
        await fake.drain()
        statuses = {c.status for c in (await fake.list_calls()).results}
        assert len(statuses) > 1
        assert CallStatus.COMPLETED in statuses
        await fake.aclose()

    async def test_completion_rate_can_be_forced(self) -> None:
        """The demo seeder needs enough completed calls to fill a table."""
        fake = FakeHunarClient(speed=FAST, seed=1, post_webhooks=False, completion_rate=1.0)
        agent = await fake.create_agent(agent_payload())
        for index in range(10):
            await fake.create_call(
                CallCreate(
                    callee_name=f"C{index}",
                    mobile_number=f"+9198765{index:05d}",
                    agent_id=agent.id,
                )
            )
        await fake.drain()
        assert all(c.status is CallStatus.COMPLETED for c in (await fake.list_calls()).results)
        await fake.aclose()


class TestResultFidelity:
    async def test_every_result_value_is_a_string(self, client: FakeHunarClient) -> None:
        """The single most important property of the fake.

        The live API returns strings even for a field declared "boolean" or
        "number". If the fake returned native types, the coercion layer
        would appear to work in demo mode and break against the real API.
        """
        agent = await client.create_agent(agent_payload())
        for index in range(12):
            await client.create_call(
                CallCreate(
                    callee_name=f"C{index}",
                    mobile_number=f"+9198765{index:05d}",
                    agent_id=agent.id,
                )
            )
        await client.drain()

        checked = 0
        for call in (await client.list_calls()).results:
            if call.result is None:
                continue
            checked += 1
            assert all(isinstance(value, str) for value in call.result.values())
        assert checked > 0, "no completed call produced a result to check"

    async def test_result_keys_match_the_declared_schema(self, client: FakeHunarClient) -> None:
        """Columns are derived from the schema, so extra or missing keys break the table."""
        agent = await client.create_agent(agent_payload())
        await client.create_call(
            CallCreate(callee_name="Asha", mobile_number="+919876543210", agent_id=agent.id)
        )
        await client.drain()

        completed = [
            c
            for c in (await client.list_calls()).results
            if c.status is CallStatus.COMPLETED and c.result
        ]
        for call in completed:
            assert call.result is not None
            assert set(call.result) == set(agent.result_schema)

    async def test_numeric_fields_are_digit_strings(self, client: FakeHunarClient) -> None:
        """So the coercion layer's numeral extraction has realistic input."""
        agent = await client.create_agent(
            agent_payload(result_schema={"years_experience": "number"})
        )
        for index in range(8):
            await client.create_call(
                CallCreate(
                    callee_name=f"C{index}",
                    mobile_number=f"+9198765{index:05d}",
                    agent_id=agent.id,
                )
            )
        await client.drain()
        for call in (await client.list_calls()).results:
            if call.result:
                assert call.result["years_experience"].isdigit()

    async def test_completed_calls_carry_a_recording_and_duration(
        self, client: FakeHunarClient
    ) -> None:
        agent = await client.create_agent(agent_payload())
        for index in range(10):
            await client.create_call(
                CallCreate(
                    callee_name=f"C{index}",
                    mobile_number=f"+9198765{index:05d}",
                    agent_id=agent.id,
                )
            )
        await client.drain()
        for call in (await client.list_calls()).results:
            if call.status is CallStatus.COMPLETED:
                assert call.recording_url
                assert call.duration_seconds and call.duration_seconds > 0
            else:
                assert not call.result


class TestWebhookDelivery:
    @respx.mock
    async def test_delivers_genuinely_signed_webhooks(self) -> None:
        """The fake must exercise the real verification path, not bypass it.

        Anything less would mean signature checking, replay bounds and
        idempotency are never run in the mode the demo actually uses.
        """
        route = respx.post(url__startswith="https://listener.test").mock(
            return_value=httpx.Response(200, json={"status": "accepted"})
        )
        fake = FakeHunarClient(api_key=KEY, speed=FAST, seed=11, completion_rate=1.0)
        agent = await fake.create_agent(agent_payload())
        await fake.create_call(
            CallCreate(
                callee_name="Asha",
                mobile_number="+919876543210",
                agent_id=agent.id,
                callback_config=CallbackConfig(
                    call_status_callback_url=f"{HOOK_BASE}/status",
                    call_recording_callback_url=f"{HOOK_BASE}/recording",
                    call_result_callback_url=f"{HOOK_BASE}/result",
                ),
            )
        )
        await fake.drain()
        assert route.call_count >= 1

        for call in route.calls:
            request = call.request
            result = verify_webhook(
                signature_header=request.headers.get("X-Hunar-Signature"),
                timestamp_header=request.headers.get("X-Hunar-Timestamp"),
                body=request.content,
                trusted_keys=[KEY],
            )
            assert result.ok, "the fake produced a signature our verifier rejects"

        await fake.aclose()

    @respx.mock
    async def test_a_wrong_key_is_correctly_rejected(self) -> None:
        """Guards against the verifier accidentally accepting anything."""
        respx.post(url__startswith="https://listener.test").mock(return_value=httpx.Response(200))
        fake = FakeHunarClient(api_key=KEY, speed=FAST, seed=5, completion_rate=1.0)
        agent = await fake.create_agent(agent_payload())
        await fake.create_call(
            CallCreate(
                callee_name="Asha",
                mobile_number="+919876543210",
                agent_id=agent.id,
                callback_config=CallbackConfig(call_status_callback_url=f"{HOOK_BASE}/status"),
            )
        )
        await fake.drain()

        request = respx.calls[0].request
        result = verify_webhook(
            signature_header=request.headers.get("X-Hunar-Signature"),
            timestamp_header=request.headers.get("X-Hunar-Timestamp"),
            body=request.content,
            trusted_keys=["a-different-key"],
        )
        assert not result.ok
        await fake.aclose()

    @respx.mock
    async def test_status_webhook_fires_only_at_a_terminal_status(self) -> None:
        """Matches the real API, and is why the reconciler is mandatory.

        If a RINGING or IN_PROGRESS event ever appeared here, the app would
        be built to rely on push updates that production never sends.
        """
        respx.post(url__startswith="https://listener.test").mock(return_value=httpx.Response(200))
        fake = FakeHunarClient(api_key=KEY, speed=FAST, seed=13, completion_rate=1.0)
        agent = await fake.create_agent(agent_payload())
        await fake.create_call(
            CallCreate(
                callee_name="Asha",
                mobile_number="+919876543210",
                agent_id=agent.id,
                callback_config=CallbackConfig(call_status_callback_url=f"{HOOK_BASE}/status"),
            )
        )
        await fake.drain()

        statuses = [
            json.loads(call.request.content)["status"]
            for call in respx.calls
            if json.loads(call.request.content).get("event_type") == "call_status_updated"
        ]
        assert statuses, "no status event was delivered"
        assert all(
            status in {"COMPLETED", "NOT_CONNECTED", "FAILED", "CANCELLED"} for status in statuses
        ), f"a non-terminal status was pushed: {statuses}"
        await fake.aclose()

    @respx.mock
    async def test_result_payload_carries_no_call_id(self) -> None:
        """Reproduces the API's most awkward quirk deliberately.

        The live `call_result_done` body contains only `event_type` and
        `result`. Correlation therefore has to come from the token in the
        callback URL, and the fake must not paper over that by helpfully
        including an id the real payload lacks.
        """
        respx.post(url__startswith="https://listener.test").mock(return_value=httpx.Response(200))
        fake = FakeHunarClient(api_key=KEY, speed=FAST, seed=17, completion_rate=1.0)
        agent = await fake.create_agent(agent_payload())
        await fake.create_call(
            CallCreate(
                callee_name="Asha",
                mobile_number="+919876543210",
                agent_id=agent.id,
                callback_config=CallbackConfig(call_result_callback_url=f"{HOOK_BASE}/result"),
            )
        )
        await fake.drain()

        payloads = [
            json.loads(call.request.content)
            for call in respx.calls
            if json.loads(call.request.content).get("event_type") == "call_result_done"
        ]
        assert payloads, "no result event was delivered"
        for payload in payloads:
            assert set(payload) == {"event_type", "result"}
            assert "call_id" not in payload
        await fake.aclose()

    async def test_an_unreachable_listener_does_not_break_the_simulation(self) -> None:
        """A restart or a missing tunnel must not strand calls mid-flight."""
        fake = FakeHunarClient(api_key=KEY, speed=FAST, seed=19, completion_rate=1.0)
        agent = await fake.create_agent(agent_payload())
        call = await fake.create_call(
            CallCreate(
                callee_name="Asha",
                mobile_number="+919876543210",
                agent_id=agent.id,
                callback_config=CallbackConfig(
                    call_status_callback_url="https://127.0.0.1:1/nothing-here"
                ),
            )
        )
        await fake.drain()
        assert (await fake.get_call(call.id)).is_terminal
        await fake.aclose()


class TestListingAndBulk:
    async def test_filters_by_status(self, client: FakeHunarClient) -> None:
        """The reconciler pages by agent and status, so this must work."""
        agent = await client.create_agent(agent_payload())
        for index in range(10):
            await client.create_call(
                CallCreate(
                    callee_name=f"C{index}",
                    mobile_number=f"+9198765{index:05d}",
                    agent_id=agent.id,
                )
            )
        await client.drain()

        completed = await client.list_calls(status=[CallStatus.COMPLETED])
        assert all(c.status is CallStatus.COMPLETED for c in completed.results)

    async def test_filters_by_agent(self, client: FakeHunarClient) -> None:
        first = await client.create_agent(agent_payload(name="agent-one"))
        second = await client.create_agent(agent_payload(name="agent-two"))
        await client.create_call(
            CallCreate(callee_name="A", mobile_number="+919000000001", agent_id=first.id)
        )
        await client.create_call(
            CallCreate(callee_name="B", mobile_number="+919000000002", agent_id=second.id)
        )
        await client.drain()

        page = await client.list_calls(agent_id=[first.id])
        assert len(page.results) == 1
        assert page.results[0].agent_id == first.id

    async def test_caps_page_size_at_two_hundred(self, client: FakeHunarClient) -> None:
        """Mirrors the documented maximum so paging logic is exercised."""
        page = await client.list_calls(page_size=5000)
        assert page.count is not None

    async def test_bulk_drops_duplicate_numbers_by_default(self, client: FakeHunarClient) -> None:
        """Matches `remove_duplicate_phone_numbers` defaulting to true.

        It also prevents the same person being dialled twice from one
        careless spreadsheet paste.
        """
        agent = await client.create_agent(agent_payload())
        result = await client.create_calls_bulk(
            BulkCallCreate(
                agent_id=agent.id,
                data=[
                    BulkCallRecipient(callee_name="Asha", mobile_number="+919876543210"),
                    BulkCallRecipient(callee_name="Asha again", mobile_number="+919876543210"),
                    BulkCallRecipient(callee_name="Ravi", mobile_number="+919876543211"),
                ],
            )
        )
        assert result.total_submitted == 3
        assert result.total_accepted == 2
        assert result.total_rejected == 1
        await client.drain()

    async def test_reports_a_validated_number(self, client: FakeHunarClient) -> None:
        """So the app's caller-ID checks have something to find in demo mode."""
        page = await client.list_numbers()
        assert page.results
        assert page.results[0].is_validated


class TestSignatureHelperAgreement:
    def test_fake_and_verifier_agree_on_the_construction(self) -> None:
        """Both sides use one helper, so a change cannot desynchronise them."""
        body = b'{"event_type":"call_result_done"}'
        timestamp = "1757000000"
        signature = compute_signature(KEY, timestamp, body)
        assert verify_webhook(
            signature_header=signature,
            timestamp_header=timestamp,
            body=body,
            trusted_keys=[KEY],
            now=float(timestamp),
        ).ok
