"""Health and runtime-mode endpoints.

``/healthz`` is intentionally shallow and never fails on a dependency: a
platform health check that flaps because an upstream is briefly slow
causes more downtime than it prevents. ``/readyz`` is the deep one and is
what a human or a deploy gate should look at.

``/meta`` exists for the frontend. It is how the UI knows whether it is
showing live or recorded data, which is what the demo-mode banner is
driven by. Answering that honestly matters more than it might seem: the
assignment key expires within days, and a dashboard that silently serves
simulated calls as though they were real would be misleading.
"""

from __future__ import annotations

from typing import Any, Literal

import structlog
from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.deps import DbSession, SettingsDep

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    environment: str


class DependencyState(BaseModel):
    name: str
    ok: bool
    detail: str | None = None


class ReadinessResponse(BaseModel):
    ready: bool
    dependencies: list[DependencyState]


class VoiceModeState(BaseModel):
    """What the UI needs to describe the current data source truthfully."""

    mode: Literal["live", "mock"]
    configured_mode: str
    degraded: bool
    degrade_reason: str | None = None
    banner: str | None = None


class MetaResponse(BaseModel):
    version: str
    environment: str
    voice: VoiceModeState
    people_provider: str
    calling_window: str
    allowlist_size: int


@router.get("/healthz", response_model=HealthResponse)
async def healthz(settings: SettingsDep) -> HealthResponse:
    """Liveness. Answers if the process is running, nothing more."""
    return HealthResponse(status="ok", version=__version__, environment=settings.environment)


@router.get("/readyz", response_model=ReadinessResponse)
async def readyz(
    request: Request, session: DbSession, response: Response, settings: SettingsDep
) -> ReadinessResponse:
    """Readiness. Actually touches the database and reports the voice mode."""
    dependencies: list[DependencyState] = []

    try:
        await session.execute(text("SELECT 1"))
        dependencies.append(DependencyState(name="database", ok=True))
    except Exception as exc:
        logger.warning("health.database_unreachable", error=str(exc))
        dependencies.append(DependencyState(name="database", ok=False, detail=type(exc).__name__))

    client: Any = request.app.state.hunar_client
    degraded = bool(getattr(client, "degraded", False))
    dependencies.append(
        DependencyState(
            name="hunar",
            # Demo mode is a working state, not a failure. Reporting it as
            # unhealthy would make a deliberately degraded deploy look broken.
            ok=True,
            detail=f"mode={client.mode}" + (" (degraded)" if degraded else ""),
        )
    )

    ready = all(dependency.ok for dependency in dependencies)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(ready=ready, dependencies=dependencies)


@router.get("/meta", response_model=MetaResponse)
async def meta(request: Request, settings: SettingsDep) -> MetaResponse:
    """Runtime facts the frontend needs, including whether data is simulated."""
    client: Any = request.app.state.hunar_client
    degraded = bool(getattr(client, "degraded", False))
    reason: str | None = getattr(client, "degrade_reason", None)

    banner: str | None = None
    if client.mode == "mock":
        if degraded and reason == "hunar_quota_error":
            banner = (
                "Demo mode. The Hunar call-minute quota is exhausted, so calls "
                "shown here are simulated from recorded conversations."
            )
        elif degraded and reason == "hunar_auth_error":
            banner = (
                "Demo mode. The Hunar API key has expired, so calls shown here "
                "are simulated from recorded conversations."
            )
        else:
            banner = (
                "Demo mode. Calls shown here are simulated, so the dashboard can "
                "be explored without placing real phone calls."
            )

    return MetaResponse(
        version=__version__,
        environment=settings.environment,
        voice=VoiceModeState(
            mode="mock" if client.mode == "mock" else "live",
            configured_mode=settings.hunar_mode.value,
            degraded=degraded,
            degrade_reason=reason,
            banner=banner,
        ),
        people_provider=settings.people_provider.value,
        calling_window=(
            f"{settings.calling_hours_start} to {settings.calling_hours_end} "
            f"{settings.calling_timezone}"
        ),
        # The count, never the numbers. The UI states that calling is
        # restricted without publishing anybody's phone number.
        allowlist_size=len(settings.allowlist),
    )


__all__ = ["router"]
