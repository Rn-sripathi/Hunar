"""Live HTTP client for the Hunar Voice Agents API.

The retry policy here is deliberately asymmetric, and it is the most
important decision in this module.

Reads are retried with exponential backoff and jitter, because they are
idempotent and a transient 503 should not surface as a broken dashboard.

Writes that create calls are NEVER retried. ``POST /calls/`` that times
out may or may not have dialled, and because ``GET /calls/`` offers no
``request_id`` filter there is no cheap way to find out. Retrying would
risk placing a second real phone call to a real person, so the failure is
handed back to the caller, which parks the attempt as
``SubmitState.UNKNOWN`` for the reconciler to resolve by paging the call
list and matching on ``request_id``.
"""

from __future__ import annotations

import json
from types import TracebackType
from typing import Any, Self
from uuid import UUID

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .enums import CallStatus
from .errors import (
    HunarAuthError,
    HunarError,
    HunarNotFoundError,
    HunarQuotaError,
    HunarServerError,
    HunarTransportError,
    HunarValidationError,
)
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

logger = structlog.get_logger(__name__)

__all__ = ["DEFAULT_BASE_URL", "LiveHunarClient"]

DEFAULT_BASE_URL = "https://api.voice.hunar.ai/external/v1/"

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_READ_MAX_ATTEMPTS = 4


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, HunarError) and exc.retryable


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        return {"raw": response.text[:2000]}


def _extract_message(body: Any, fallback: str) -> str:
    """Pull the most useful human-readable message out of an error body."""
    if isinstance(body, dict):
        for key in ("message", "detail", "error", "non_field_errors"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, str):
                    return first
                if isinstance(first, dict) and "msg" in first:
                    return str(first["msg"])
    return fallback


class LiveHunarClient:
    """Async client talking to the real Hunar API."""

    mode = "live"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("a Hunar API key is required")
        self._api_key = api_key
        self._http = httpx.AsyncClient(
            base_url=base_url if base_url.endswith("/") else base_url + "/",
            headers={
                "X-API-Key": api_key,
                "Accept": "application/json",
                "User-Agent": "hunar-sdk/0.1 (+assignment)",
            },
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=transport,
        )

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
        await self._http.aclose()

    # ── transport ────────────────────────────────────────────
    async def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send one request and translate failure into a typed exception."""
        try:
            response = await self._http.request(
                method, path.lstrip("/"), json=json_body, params=params
            )
        except httpx.TimeoutException as exc:
            raise HunarTransportError(f"request to {path} timed out") from exc
        except httpx.TransportError as exc:
            raise HunarTransportError(f"could not reach Hunar: {exc}") from exc

        if response.is_success:
            if not response.content:
                return None
            return _safe_json(response)

        body = _safe_json(response)
        status = response.status_code
        logger.warning(
            "hunar.request_failed",
            method=method,
            path=path,
            status=status,
            # The body may echo request fields; it never contains the key,
            # which travels only in the header.
            body=body,
        )

        if status in (400, 422):
            raise HunarValidationError(
                _extract_message(body, "Hunar rejected the request"),
                status_code=status,
                payload=body,
            )
        if status == 401:
            raise HunarAuthError(payload=body)
        if status == 402:
            raise HunarQuotaError(payload=body)
        if status == 404:
            raise HunarNotFoundError(_extract_message(body, "resource not found"), payload=body)
        if status in _RETRYABLE_STATUSES:
            raise HunarServerError(
                _extract_message(body, f"Hunar returned {status}"),
                status_code=status,
                payload=body,
            )
        raise HunarError(
            _extract_message(body, f"unexpected Hunar response {status}"),
            status_code=status,
            payload=body,
        )

    async def _read(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """GET with retry. Safe because reads are idempotent."""
        cleaned = {k: v for k, v in (params or {}).items() if v is not None}
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_is_retryable),
            wait=wait_exponential_jitter(initial=0.5, max=8.0),
            stop=stop_after_attempt(_READ_MAX_ATTEMPTS),
            reraise=True,
        ):
            with attempt:
                return await self._send("GET", path, params=cleaned)
        raise AssertionError("unreachable: tenacity always returns or raises")

    @staticmethod
    def _dump(payload: Any) -> Any:
        return json.loads(payload.model_dump_json(exclude_none=True))

    # ── agents ───────────────────────────────────────────────
    async def list_agents(self, *, page: int = 1, page_size: int = 50) -> Page[Agent]:
        data = await self._read("agents/", params={"page": page, "page_size": page_size})
        return Page[Agent].model_validate(data)

    async def get_agent(self, agent_id: UUID | str) -> Agent:
        return Agent.model_validate(await self._read(f"agents/{agent_id}/"))

    async def create_agent(self, payload: AgentCreate) -> Agent:
        data = await self._send("POST", "agents/", json_body=self._dump(payload))
        agent = Agent.model_validate(data)
        logger.info("hunar.agent_created", agent_id=str(agent.id), name=agent.name)
        return agent

    async def update_agent(self, agent_id: UUID | str, payload: AgentUpdate) -> Agent:
        data = await self._send("PUT", f"agents/{agent_id}/", json_body=self._dump(payload))
        return Agent.model_validate(data)

    # ── calls ────────────────────────────────────────────────
    async def create_call(self, payload: CallCreate) -> Call:
        """Place one call. Never retried; see the module docstring."""
        data = await self._send("POST", "calls/", json_body=self._dump(payload))
        call = Call.model_validate(data)
        logger.info(
            "hunar.call_created",
            call_id=str(call.id),
            request_id=call.request_id,
            status=call.status.value,
        )
        return call

    async def create_calls_bulk(self, payload: BulkCallCreate) -> BulkCallResult:
        """Submit many calls at once. Also never retried."""
        data = await self._send("POST", "calls/bulk/", json_body=self._dump(payload))
        result = BulkCallResult.model_validate(data)
        logger.info(
            "hunar.bulk_calls_created",
            submitted=result.total_submitted,
            accepted=result.total_accepted,
            rejected=result.total_rejected,
        )
        return result

    async def list_calls(
        self,
        *,
        agent_id: list[UUID | str] | None = None,
        status: list[CallStatus] | None = None,
        campaign_id: UUID | str | None = None,
        page: int = 1,
        page_size: int = 200,
    ) -> Page[Call]:
        params: dict[str, Any] = {"page": page, "page_size": min(page_size, 200)}
        if agent_id:
            # httpx serialises a list value as repeated query keys, which is
            # what the API's array-typed filters expect.
            params["agent_id"] = [str(value) for value in agent_id]
        if status:
            params["status"] = [s.value for s in status]
        if campaign_id:
            params["campaign_id"] = str(campaign_id)
        return Page[Call].model_validate(await self._read("calls/", params=params))

    async def get_call(self, call_id: UUID | str) -> Call:
        return Call.model_validate(await self._read(f"calls/{call_id}/"))

    # ── numbers ──────────────────────────────────────────────
    async def list_numbers(self, *, page: int = 1, page_size: int = 200) -> Page[PhoneNumber]:
        data = await self._read("numbers/", params={"page": page, "page_size": page_size})
        return Page[PhoneNumber].model_validate(data)
