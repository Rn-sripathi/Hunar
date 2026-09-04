"""In-memory Hunar client for local development and the deployed demo.

This is not a test double bolted on at the end. The assignment's API key
expires within days of submission, so whoever opens the deployed link is
quite likely to be running on this code path. It is therefore written to
be honest about the real API's awkward parts rather than to make the app
look good:

* **Results are synthesised as STRINGS**, even for a field the agent
  declared ``"boolean"`` or ``"number"``, because that is what the real
  API returns. If the fake handed back native types it would hide the one
  bug the coercion layer exists to prevent.
* **Webhooks are genuinely HMAC-signed** and posted to our own endpoint
  over real HTTP. Signature verification, replay checks, idempotency and
  event processing all execute exactly as in production, instead of being
  bypassed by an in-process shortcut.
* **Status webhooks fire only on terminal statuses**, matching the real
  behaviour, so anything that depends on live in-progress state is forced
  to go through the reconciler here too.
* **Outcomes are weighted, not uniformly successful.** Roughly a fifth of
  calls go unanswered and a tenth fail outright, which is what a
  frontline calling campaign actually looks like and keeps the dashboard
  from presenting an implausibly perfect picture.

Determinism is available: pass a ``seed`` and the same campaign produces
the same outcomes, which is what the seeded demo relies on.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import re
import time
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID, uuid4

import httpx
import structlog

from .enums import (
    AgentStatus,
    CallStatus,
    EngagementStatus,
    LifecycleStatus,
)
from .errors import HunarNotFoundError, HunarValidationError
from .models import (
    Agent,
    AgentCreate,
    AgentUpdate,
    BulkCallCreate,
    BulkCallResult,
    Call,
    CallbackConfig,
    CallCreate,
    Page,
    PhoneNumber,
)
from .webhooks import compute_signature

logger = structlog.get_logger(__name__)

__all__ = ["FakeHunarClient"]

# Proportions chosen to look like a real frontline calling campaign rather
# than a demo where everything works.
_OUTCOME_WEIGHTS: list[tuple[CallStatus, int]] = [
    (CallStatus.COMPLETED, 70),
    (CallStatus.NOT_CONNECTED, 20),
    (CallStatus.FAILED, 10),
]

# (status, seconds spent in it) at speed 1.0.
_LIFECYCLE: list[tuple[CallStatus, float]] = [
    (CallStatus.INITIATED, 1.5),
    (CallStatus.RINGING, 3.5),
    (CallStatus.IN_PROGRESS, 7.0),
]

_TRUE_ISH = ("Yes", "Yes, I am", "Haan", "Yes sir")
_FALSE_ISH = ("No", "Not right now", "Nahi", "No, sorry")

_CITIES = (
    "Bengaluru",
    "Mumbai",
    "Hyderabad",
    "Pune",
    "Chennai",
    "Delhi",
    "Gurugram",
    "Ahmedabad",
)
_ROLES = (
    "Delivery Executive",
    "Warehouse Associate",
    "Retail Sales Executive",
    "Security Guard",
    "Telecaller",
    "Field Technician",
)
_COMPANIES = (
    "Swiggy",
    "Zepto",
    "Reliance Retail",
    "Flipkart",
    "Blinkit",
    "Delhivery",
)


class FakeHunarClient:
    """Drop-in replacement for :class:`~hunar_sdk.client.LiveHunarClient`."""

    mode = "mock"

    def __init__(
        self,
        *,
        api_key: str = "fake-local-key",
        speed: float = 1.0,
        seed: int | None = None,
        post_webhooks: bool = True,
        completion_rate: float | None = None,
    ) -> None:
        """
        Args:
            api_key: Used to sign outbound webhooks. Must match what the
                receiving app trusts, or verification will correctly fail.
            speed: Multiplier on the lifecycle clock. Above 1.0 is faster.
                Tests use a large value; the demo leaves it at 1.0 so the
                monitoring screen animates believably.
            seed: Fixes the random stream, making a seeded demo reproducible.
            post_webhooks: Whether to actually deliver signed webhooks.
                Disabled in unit tests that have no listener.
            completion_rate: Override the weighted outcomes to force a
                given success proportion. Used by the demo seeder to
                guarantee enough completed calls to populate a dashboard.
        """
        self._api_key = api_key
        self._speed = max(speed, 0.01)
        self._random = random.Random(seed)  # noqa: S311 - simulation, not crypto
        self._post_webhooks = post_webhooks
        self._completion_rate = completion_rate

        self._agents: dict[UUID, Agent] = {}
        self._calls: dict[UUID, Call] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

    # ── lifecycle ────────────────────────────────────────────
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Cancel in-flight simulations and close the HTTP client."""
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        await self._http.aclose()

    async def drain(self, *, deadline_seconds: float = 60.0) -> None:
        """Wait for every simulated call to reach a terminal status.

        Tests and the demo seeder use this instead of sleeping, so neither
        has to guess how long the simulation takes. Draining a task can
        spawn nothing further, so the loop terminates once the set empties;
        the deadline is a safety net against a pathological hang, and
        exceeding it is not an error worth propagating.
        """
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(deadline_seconds):
                while self._tasks:
                    await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ── agents ───────────────────────────────────────────────
    async def list_agents(self, *, page: int = 1, page_size: int = 50) -> Page[Agent]:
        agents = list(self._agents.values())
        start = (page - 1) * page_size
        window = agents[start : start + page_size]
        return Page[Agent](
            results=window,
            count=len(agents),
            next="next" if start + page_size < len(agents) else None,
        )

    async def get_agent(self, agent_id: UUID | str) -> Agent:
        agent = self._agents.get(_as_uuid(agent_id))
        if agent is None:
            raise HunarNotFoundError(f"no agent {agent_id}")
        return agent

    async def create_agent(self, payload: AgentCreate) -> Agent:
        agent = Agent(
            id=uuid4(),
            name=payload.name,
            voice_persona=payload.voice_persona,
            language=payload.language,
            agent_prompt=payload.agent_prompt,
            objective=payload.objective,
            introduction=payload.introduction,
            result_prompt=payload.result_prompt,
            result_schema=dict(payload.result_schema),
            persona_name=payload.persona_name,
            status=AgentStatus.ACTIVE,
            agent_code=f"FAKE-{self._random.randint(1000, 9999)}",
            # Mirrors the real API reporting which {variables} it found.
            custom_variables=_extract_variables(payload),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self._agents[agent.id] = agent
        logger.info("fake_hunar.agent_created", agent_id=str(agent.id), name=agent.name)
        return agent

    async def update_agent(self, agent_id: UUID | str, payload: AgentUpdate) -> Agent:
        existing = await self.get_agent(agent_id)
        updates = payload.model_dump(exclude_none=True)
        updated = existing.model_copy(update={**updates, "updated_at": datetime.now(UTC)})
        self._agents[existing.id] = updated
        return updated

    # ── calls ────────────────────────────────────────────────
    async def create_call(self, payload: CallCreate) -> Call:
        agent = self._agents.get(payload.agent_id)
        if agent is None:
            # The real API rejects an unknown agent, so the fake must too,
            # otherwise a broken agent reference passes silently in demo mode.
            raise HunarValidationError(f"agent {payload.agent_id} does not exist", status_code=400)

        call = Call(
            id=uuid4(),
            callee_name=payload.callee_name,
            mobile_number=payload.mobile_number,
            agent_id=payload.agent_id,
            language=agent.language,
            call_type="OUTBOUND",
            status=CallStatus.NOT_STARTED,
            lifecycle_status=LifecycleStatus.PENDING,
            request_id=payload.request_id,
            max_retries=payload.retry_config.max_retry_count if payload.retry_config else 0,
            retry_count=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self._calls[call.id] = call
        self._spawn(self._run_lifecycle(call.id, agent, payload.callback_config))
        logger.info("fake_hunar.call_created", call_id=str(call.id))
        return call

    async def create_calls_bulk(self, payload: BulkCallCreate) -> BulkCallResult:
        created: list[Call] = []
        rejected: list[dict[str, Any]] = []
        seen: set[str] = set()

        for row in payload.data:
            if payload.remove_duplicate_phone_numbers and row.mobile_number in seen:
                rejected.append({"mobile_number": row.mobile_number, "reason": "duplicate"})
                continue
            seen.add(row.mobile_number)
            created.append(
                await self.create_call(
                    CallCreate(
                        callee_name=row.callee_name,
                        mobile_number=row.mobile_number,
                        agent_id=payload.agent_id,
                        custom_data=row.custom_data,
                        timezone=payload.timezone,
                        from_phone_number=payload.from_phone_number,
                        callback_config=payload.callback_config,
                        retry_config=payload.retry_config,
                        guardrails=payload.guardrails,
                        request_id=payload.request_id,
                    )
                )
            )

        return BulkCallResult(
            request_id=payload.request_id,
            campaign_id=uuid4(),
            total_submitted=len(payload.data),
            total_accepted=len(created),
            total_rejected=len(rejected),
            calls=created,
            rejected_rows=rejected,
        )

    async def list_calls(
        self,
        *,
        agent_id: list[UUID | str] | None = None,
        status: list[CallStatus] | None = None,
        campaign_id: UUID | str | None = None,
        page: int = 1,
        page_size: int = 200,
    ) -> Page[Call]:
        calls = list(self._calls.values())
        if agent_id:
            wanted = {_as_uuid(value) for value in agent_id}
            calls = [c for c in calls if c.agent_id in wanted]
        if status:
            allowed = set(status)
            calls = [c for c in calls if c.status in allowed]

        page_size = min(page_size, 200)
        start = (page - 1) * page_size
        window = calls[start : start + page_size]
        return Page[Call](
            results=window,
            count=len(calls),
            next="next" if start + page_size < len(calls) else None,
        )

    async def get_call(self, call_id: UUID | str) -> Call:
        call = self._calls.get(_as_uuid(call_id))
        if call is None:
            raise HunarNotFoundError(f"no call {call_id}")
        return call

    async def list_numbers(self, *, page: int = 1, page_size: int = 200) -> Page[PhoneNumber]:
        return Page[PhoneNumber](
            results=[
                PhoneNumber(
                    id=uuid4(),
                    phone_number="+911140000000",
                    allowed_countries=["IN"],
                    is_validated=True,
                    provider="fake",
                    status="ACTIVE",
                )
            ],
            count=1,
        )

    # ── simulation ───────────────────────────────────────────
    def _spawn(self, coro: Any) -> None:
        """Track the task so it is not garbage collected mid-flight."""
        task: asyncio.Task[None] = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _pick_outcome(self) -> CallStatus:
        if self._completion_rate is not None:
            if self._random.random() < self._completion_rate:
                return CallStatus.COMPLETED
            return self._random.choice([CallStatus.NOT_CONNECTED, CallStatus.FAILED])
        population = [status for status, _ in _OUTCOME_WEIGHTS]
        weights = [weight for _, weight in _OUTCOME_WEIGHTS]
        return self._random.choices(population, weights=weights, k=1)[0]

    async def _run_lifecycle(
        self, call_id: UUID, agent: Agent, callbacks: CallbackConfig | None
    ) -> None:
        """Advance one call through a realistic status sequence."""
        try:
            for status, dwell in _LIFECYCLE:
                await asyncio.sleep(dwell / self._speed)
                self._patch(call_id, status=status, lifecycle_status=LifecycleStatus.IN_PROGRESS)

            outcome = self._pick_outcome()
            await asyncio.sleep(1.0 / self._speed)

            if outcome is CallStatus.COMPLETED:
                spoken = round(self._random.uniform(28, 95), 1)
                self._patch(
                    call_id,
                    status=outcome,
                    lifecycle_status=LifecycleStatus.COMPLETED,
                    engagement_status=EngagementStatus.ENGAGED,
                    answered_by="HUMAN",
                    call_ended_by="AGENT",
                    duration_seconds=spoken,
                    duration_minutes=round(spoken / 60, 2),
                    user_speech_duration=round(spoken * 0.4, 1),
                    recording_url=f"/api/v1/demo/recordings/{call_id}.mp3",
                    result=self._synthesize_result(agent),
                )
            elif outcome is CallStatus.NOT_CONNECTED:
                self._patch(
                    call_id,
                    status=outcome,
                    lifecycle_status=LifecycleStatus.EXHAUSTED,
                    engagement_status=EngagementStatus.NOT_ENGAGED,
                    answered_by="NONE",
                    duration_seconds=0.0,
                )
            else:
                self._patch(
                    call_id,
                    status=outcome,
                    lifecycle_status=LifecycleStatus.EXHAUSTED,
                    engagement_status=EngagementStatus.NOT_ENGAGED,
                    duration_seconds=0.0,
                )

            # Only now, at a terminal status, does the status webhook fire.
            # The real API behaves the same way, which is precisely why
            # live progress has to come from polling.
            call = self._calls[call_id]
            if callbacks:
                await self._deliver(
                    callbacks.call_status_callback_url,
                    {
                        "event_type": "call_status_updated",
                        "call_id": str(call.id),
                        "status": call.status.value,
                        "lifecycle_status": call.lifecycle_status.value,
                    },
                )
                if call.status is CallStatus.COMPLETED:
                    await self._deliver(
                        callbacks.call_recording_callback_url,
                        {
                            "event_type": "call_recording_done",
                            "call_id": str(call.id),
                            "recording_url": call.recording_url,
                        },
                    )
                    # Mirrors the real payload, which carries no call id at
                    # all. Correlation must come from the callback URL.
                    await self._deliver(
                        callbacks.call_result_callback_url,
                        {"event_type": "call_result_done", "result": call.result},
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("fake_hunar.lifecycle_failed", call_id=str(call_id), error=str(exc))

    def _patch(self, call_id: UUID, **changes: Any) -> None:
        current = self._calls[call_id]
        self._calls[call_id] = current.model_copy(
            update={**changes, "updated_at": datetime.now(UTC)}
        )

    async def _deliver(self, url: str | None, payload: dict[str, Any]) -> None:
        """Post a genuinely signed webhook to our own endpoint."""
        if not url or not self._post_webhooks:
            return
        body = json.dumps(payload, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        try:
            response = await self._http.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Hunar-Signature": compute_signature(self._api_key, timestamp, body),
                    "X-Hunar-Timestamp": timestamp,
                },
            )
            logger.info(
                "fake_hunar.webhook_delivered",
                event=payload.get("event_type"),
                status=response.status_code,
            )
        except httpx.HTTPError as exc:
            # A missing listener is normal in unit tests and during a
            # restart; it must never abort the simulation.
            logger.debug("fake_hunar.webhook_undeliverable", url=url, error=str(exc))

    def _synthesize_result(self, agent: Agent) -> dict[str, str]:
        """Build a plausible ``result`` for the agent's declared schema.

        Every value is a STRING, including for fields declared as boolean
        or number, because that is what the live API returns. Keeping the
        fake faithful here is what makes the coercion layer genuinely
        exercised rather than merely present.
        """
        result: dict[str, str] = {}
        for field, hint in agent.result_schema.items():
            result[field] = self._value_for(field, str(hint))
        return result

    def _value_for(self, field: str, hint: str) -> str:
        name = field.lower()
        declared = hint.lower()

        if "summary" in name or "outcome" in name:
            return self._random.choice(
                (
                    "Candidate sounded keen and is available to start soon.",
                    "Spoke briefly; wants a callback in the evening.",
                    "Has relevant experience but expects a higher salary.",
                    "Currently employed, open to the right opportunity.",
                )
            )
        if "notice" in name:
            return self._random.choice(("Immediate", "15 days", "30 days", "2 months"))
        if "salary" in name or "compensation" in name or "ctc" in name:
            return self._random.choice(("18000", "22000", "25000 per month", "3.6 LPA"))
        if "year" in name or "experience" in name:
            return str(self._random.randint(0, 12))
        if "city" in name or "location" in name:
            return self._random.choice(_CITIES)
        if "company" in name or "employer" in name:
            return self._random.choice(_COMPANIES)
        if "role" in name or "designation" in name or "title" in name:
            return self._random.choice(_ROLES)
        if "language" in name:
            return self._random.choice(("Hindi", "English", "Tamil", "Hindi and English"))
        if "callback" in name or "time" in name:
            return self._random.choice(("Tomorrow morning", "After 6 pm", "Weekend"))

        if "bool" in declared or name.startswith(("is_", "has_", "can_", "open_")):
            return self._random.choice(_TRUE_ISH + _FALSE_ISH)
        if "interest" in name or "willing" in name or "relocat" in name or "permission" in name:
            return self._random.choice(_TRUE_ISH + _FALSE_ISH)
        if "number" in declared or "int" in declared or "float" in declared:
            return str(self._random.randint(1, 20))

        return self._random.choice(("Yes", "No", "Maybe", "Not discussed"))


def _as_uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


_VARIABLE_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _extract_variables(payload: AgentCreate) -> list[str]:
    """Find the ``{variable}`` names our own prompt templates declared."""
    found: set[str] = set()
    for text in (
        payload.agent_prompt,
        payload.introduction,
        payload.objective,
        payload.result_prompt,
    ):
        found.update(_VARIABLE_PATTERN.findall(text))
    return sorted(found)
