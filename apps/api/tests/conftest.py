"""Shared test fixtures for the backend.

Tests build the application through :func:`create_app` with injected
settings rather than reading the process environment, so a developer's
local ``.env`` can never change a test's outcome. Voice mode is always
``mock``, which needs no credentials and touches no network.

Persistence runs on in-memory SQLite. The models declare their JSON
columns as a variant that is JSONB on Postgres and plain JSON elsewhere,
so the schema under test is the same one that deploys, without the suite
depending on a running database server. Anything genuinely
Postgres-specific is exercised against Neon by the migration itself.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Imported so every table is registered on the metadata before create_all.
from app.core import models as _core_models  # noqa: F401
from app.core.config import HunarMode, PeopleProvider, Settings
from app.db import Base
from app.hiring import models as _hiring_models  # noqa: F401
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
        database_url="sqlite+aiosqlite:///:memory:",
        demo_allowlist="+919000000001|Test line one,+919000000002|Test line two",
        public_api_base_url="https://test.invalid",
        # Collapse the simulated call lifecycle and remove the reconcile
        # throttle, so a suite is not gated on thirteen seconds of
        # pretend ringing per call.
        hunar_fake_speed=400.0,
        reconcile_interval_seconds=0.0,
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    """An application wired to a fresh in-memory database.

    The engine is replaced after startup rather than before, because the
    lifespan builds its own from the settings and would otherwise
    overwrite ours. ``StaticPool`` keeps every connection pointed at the
    same in-memory database, which SQLite otherwise discards per
    connection.
    """
    application = create_app(settings)

    async with LifespanManager(application):
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        await application.state.engine.dispose()
        application.state.engine = engine
        application.state.session_factory = async_sessionmaker(
            bind=engine, expire_on_commit=False, autoflush=False
        )

        yield application

        await engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client bound to the application, with its lifespan running."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as http_client:
        yield http_client


@pytest.fixture
def session_factory(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    """Direct database access, for asserting on state the API does not expose."""
    factory: async_sessionmaker[AsyncSession] = app.state.session_factory
    return factory


@pytest.fixture
def voice_client(app: FastAPI) -> Any:
    """The demo Hunar client this application is running against."""
    return app.state.hunar_client


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """Stop a cached settings singleton leaking between tests."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


API = "/api/v1/hiring"


def sample_job(**overrides: Any) -> dict[str, Any]:
    """A realistic frontline role, used by most tests."""
    payload: dict[str, Any] = {
        "title": "Delivery Executive",
        "company_name": "Acme Logistics",
        "location": "Bengaluru",
        "description_raw": (
            "We are hiring delivery executives for Bengaluru. Own two-wheeler "
            "required. Salary 18,000 to 24,000 per month plus incentives."
        ),
        "language": "ENGLISH",
        "voice_persona": "NEHA",
        "questions": [
            {
                "label": "Years of experience",
                "text": "How many years of delivery experience do you have?",
                "answer_type": "NUMBER",
                "weight": 2.0,
                "scoring_rule": {"op": "gte", "value": 1},
            },
            {
                "label": "Owns two-wheeler",
                "text": "Do you own a two-wheeler with a valid licence?",
                "answer_type": "BOOLEAN",
                "weight": 3.0,
                "is_knockout": True,
                "scoring_rule": {"op": "is_true"},
            },
            {
                "label": "Notice period",
                "text": "How soon can you start?",
                "answer_type": "STRING",
                "weight": 1.0,
            },
        ],
    }
    payload.update(overrides)
    return payload
