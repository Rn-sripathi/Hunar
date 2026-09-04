"""The client interface both the live client and the demo fake implement.

Defining this as a :class:`typing.Protocol` rather than an abstract base
class means the fake is structurally compatible without inheriting, and
mypy verifies at every call site that swapping one for the other is safe.
That guarantee matters more than usual here: the assignment's API key
expires within days, so the fake is not a testing convenience but the
path the deployed demo actually runs on.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from .enums import CallStatus
from .models import (
    Agent,
    AgentCreate,
    AgentUpdate,
    BulkCallCreate,
    BulkCallResult,
    Call,
    CallCreate,
    Page,
    PhoneNumber,
)

__all__ = ["HunarClient"]


@runtime_checkable
class HunarClient(Protocol):
    """Async client for the Hunar Voice Agents API."""

    @property
    def mode(self) -> str:
        """Which implementation is active, for logs and the UI banner.

        Declared read-only rather than as a plain attribute so an
        implementation may compute it. The degrading client does exactly
        that, reporting ``live`` until the key is rejected and ``mock``
        afterwards.
        """
        ...

    async def list_agents(self, *, page: int = 1, page_size: int = 50) -> Page[Agent]: ...

    async def get_agent(self, agent_id: UUID | str) -> Agent: ...

    async def create_agent(self, payload: AgentCreate) -> Agent: ...

    async def update_agent(self, agent_id: UUID | str, payload: AgentUpdate) -> Agent: ...

    async def create_call(self, payload: CallCreate) -> Call:
        """Place one call.

        Implementations must NOT retry this on a transport failure. A
        timed-out request may still have dialled, ``GET /calls/`` cannot be
        filtered by ``request_id``, and a duplicate is a second real phone
        call to a real person. Callers record the attempt as
        ``SubmitState.UNKNOWN`` and let the reconciler resolve it.
        """
        ...

    async def create_calls_bulk(self, payload: BulkCallCreate) -> BulkCallResult: ...

    async def list_calls(
        self,
        *,
        agent_id: list[UUID | str] | None = None,
        status: list[CallStatus] | None = None,
        campaign_id: UUID | str | None = None,
        page: int = 1,
        page_size: int = 200,
    ) -> Page[Call]:
        """List calls.

        These four filters are the only ones the API supports. In
        particular there is no ``request_id`` filter and no date filter,
        which is why reconciliation pages by agent and matches locally.
        """
        ...

    async def get_call(self, call_id: UUID | str) -> Call: ...

    async def list_numbers(self, *, page: int = 1, page_size: int = 200) -> Page[PhoneNumber]: ...

    async def aclose(self) -> None: ...
