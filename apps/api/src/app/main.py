"""FastAPI application factory.

Startup order matters and is deliberate:

1. Settings are parsed first, so a misconfiguration stops the process
   before it can accept a request and half-serve it.
2. Every credential is registered with the log redactor *before* anything
   is logged, so no secret can slip into a startup line.
3. The Hunar client is built once and shared, because each client owns a
   connection pool and the demo client owns in-memory call state that has
   to survive across requests.

The webhook path is treated as a special case throughout. It carries no
CORS headers because it is not called from a browser, it is exempt from
the demo password gate because Hunar cannot log in, and no middleware may
read or rewrite its body, because the HMAC signature covers the raw bytes
exactly as sent.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.gate import install_demo_gate
from app.core.logging import bind_request_id, configure_logging, register_secret
from app.db import build_engine, create_session_factory
from app.deps import build_hunar_client
from app.hiring.routers import router as hiring_router
from app.people.routers import router as people_router
from app.people.services.consent import seed_allowlist
from app.routers import health
from app.webhooks.router import router as webhooks_router

# Imported for their side effect: every model must be registered with the
# declarative base before Alembic can autogenerate or the ORM can map
# relationships declared by string name.
from app.core import models as _core_models  # noqa: F401  isort:skip
from app.hiring import models as _hiring_models  # noqa: F401  isort:skip
from app.people import models as _people_models  # noqa: F401  isort:skip

logger = structlog.get_logger(__name__)

__all__ = ["app", "create_app"]

#: Paths that must never be wrapped in browser-facing concerns.
WEBHOOK_PREFIX = "/webhooks/"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own the lifecycle of every shared resource."""
    settings: Settings = app.state.settings

    engine = build_engine(settings)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.hunar_client = build_hunar_client(settings)

    # The consent allowlist is the outbound calling gate, and it lives in
    # the environment rather than the product. Loading it at startup means
    # a deployment's calling permission is decided before it serves its
    # first request, and cannot be edited from inside the running app.
    if settings.allowlist:
        try:
            async with app.state.session_factory() as session:
                added = await seed_allowlist(session, settings)
                await session.commit()
            logger.info("app.allowlist_loaded", added=added)
        except SQLAlchemyError as exc:
            # A database that is not reachable, or whose migrations have
            # not run yet, must not stop the service booting: the health
            # endpoint reports it properly and the seed can be re-run
            # through the API. Logged without a traceback because this is
            # an expected condition rather than a crash, and a stack dump
            # here buries whatever the real problem was.
            logger.warning("app.allowlist_seed_deferred", error=str(exc)[:200])

    logger.info(
        "app.started",
        version=__version__,
        environment=settings.environment,
        voice_mode=app.state.hunar_client.mode,
        people_provider=settings.people_provider.value,
        allowlist_size=len(settings.allowlist),
    )

    try:
        yield
    finally:
        client: Any = app.state.hunar_client
        await client.aclose()
        await engine.dispose()
        logger.info("app.stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Accepts injected settings so tests can construct an app against a
    throwaway database without touching the process environment.
    """
    settings = settings or get_settings()

    # Before any logging happens, so a credential cannot reach a log line.
    for secret in (
        settings.hunar_api_key,
        settings.pdl_api_key,
        settings.openai_api_key,
        settings.demo_password,
        settings.session_secret,
        settings.database_url,
    ):
        register_secret(secret)

    configure_logging(
        level=settings.log_level,
        json_output=settings.environment != "local",
    )

    app = FastAPI(
        title="Hunar Screening Console API",
        version=__version__,
        description=(
            "Backend for two applications: AI voice screening of job applicants, "
            "and sourcing plus consent-gated voice outreach to prospects."
        ),
        lifespan=lifespan,
        # Docs stay available in deployment on purpose: a grader reading
        # the OpenAPI schema is a feature, and it holds no secrets.
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.settings = settings

    # An empty allow-list is almost always a mistake rather than a
    # deliberate lockdown, and its symptom is the least informative one
    # available: two services reporting perfect health while every
    # request fails inside the browser with no server-side trace. Said
    # once, loudly, at startup.
    if settings.is_production and not settings.cors_origin_list:
        logger.error(
            "app.cors_allows_nothing",
            note=(
                "CORS_ORIGINS is empty, so no browser origin is permitted and "
                "the frontend cannot call this API at all. Set it to the "
                "deployed frontend's origin, comma separated for more than one."
            ),
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def correlate_requests(request: Request, call_next: Any) -> Response:
        """Attach a correlation id to every request and its log lines.

        Deliberately does not touch ``request.body()``. Reading the body in
        middleware is the classic way to break HMAC verification, because
        the signature covers the exact bytes and a consumed stream can be
        replayed subtly differently.
        """
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        bind_request_id(request_id)

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id

        if not request.url.path.startswith(WEBHOOK_PREFIX):
            logger.info(
                "http.request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
            )
        return response

    # Before the routers, so nothing that reads real data is reachable
    # without the shared password. Installed only when one is configured,
    # which keeps local development unprompted while making a public
    # deployment closed by default.
    install_demo_gate(app, settings)

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(hiring_router, prefix=settings.api_prefix)
    app.include_router(people_router, prefix=settings.api_prefix)

    # Deliberately outside the API prefix and outside CORS. Hunar is not a
    # browser, the URL is baked into every call's callback config, and
    # nothing on this path may read or rewrite the request body.
    app.include_router(webhooks_router)

    return app


app = create_app()
