"""Shared test fixtures for the backend.

Tests build the application through :func:`create_app` with injected
settings rather than reading the process environment, so a developer's
local ``.env`` can never change a test's outcome. Every test runs in
``mock`` voice mode, which needs no credentials and touches no network.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from app.core.config import HunarMode, PeopleProvider, Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Settings for a hermetic test run."""
    return Settings(
        environment="local",
        log_level="WARNING",
        hunar_mode=HunarMode.MOCK,
        hunar_api_key="test-key-not-real",
        people_provider=PeopleProvider.FIXTURE,
        # Never connected to in tests; SQLAlchemy builds its pool lazily.
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/hunar_test",
        demo_allowlist="+919000000001|Test line one,+919000000002|Test line two",
        public_api_base_url="https://test.invalid",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client with the application's lifespan actually run.

    ``LifespanManager`` matters here: the shared Hunar client and session
    factory are created during startup, so a test that skipped the
    lifespan would exercise a differently-wired application than production.
    """
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://testserver"
        ) as http_client:
            yield http_client


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """Stop a cached settings singleton leaking between tests."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def find_dependency(payload: dict[str, Any], name: str) -> dict[str, Any]:
    """Pull one dependency out of a readiness response."""
    for dependency in payload["dependencies"]:
        if dependency["name"] == name:
            return dict(dependency)
    raise AssertionError(f"no dependency named {name!r} in {payload}")
