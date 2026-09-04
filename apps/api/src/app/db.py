"""Async database engine, session factory and declarative base.

Two details here exist specifically because the database is Neon:

* Neon offers a pooled and a direct endpoint. The pooled one runs
  PgBouncer in transaction mode, which is incompatible with asyncpg's
  prepared-statement cache, so the cache is disabled rather than left to
  fail intermittently under load. Alembic should use the direct endpoint.
* ``sslmode`` is libpq's spelling and asyncpg does not understand it, so
  a connection string copied from a dashboard is normalised here instead
  of failing with a confusing driver error.

The constraint naming convention matters more than it looks: without it
Alembic generates anonymous constraint names, and a later migration that
needs to drop one has nothing to refer to.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import structlog
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings

logger = structlog.get_logger(__name__)

__all__ = ["Base", "build_engine", "create_session_factory", "normalise_database_url"]

#: Deterministic constraint names so migrations can reference them later.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

_PGBOUNCER_HINTS = ("-pooler.", "pgbouncer=true")


class Base(AsyncAttrs, DeclarativeBase):
    """Declarative base for every model in both applications."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def normalise_database_url(url: str) -> tuple[str, dict[str, Any]]:
    """Return an asyncpg-compatible URL plus its connect arguments.

    Accepts the connection string Neon's dashboard hands out, which uses
    libpq conventions asyncpg rejects, and returns something that works.
    """
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    connect_args: dict[str, Any] = {}

    # libpq's `sslmode` and asyncpg's `ssl` mean the same thing here.
    ssl_setting = query.pop("sslmode", None) or query.pop("ssl", None)
    if ssl_setting in {"require", "verify-ca", "verify-full", "true", "1"}:
        connect_args["ssl"] = True

    # Not an asyncpg argument; it only signals that we are behind a pooler.
    is_pooled = query.pop("pgbouncer", None) == "true" or any(
        hint in url for hint in _PGBOUNCER_HINTS
    )

    if is_pooled:
        # PgBouncer in transaction mode cannot hold prepared statements
        # across a connection it may hand to a different session.
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_cache_size"] = 0
        logger.info("db.pooled_endpoint_detected", note="prepared statements disabled")

    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return cleaned, connect_args


def build_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine for this process."""
    url, connect_args = normalise_database_url(settings.database_url)
    return create_async_engine(
        url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=2,
        # Neon suspends idle compute, so a pooled connection can be dead by
        # the time it is reused. Recycling and pre-ping turn what would be a
        # user-visible error into a transparent reconnect.
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args=connect_args,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        expire_on_commit=False,  # keeps ORM objects usable after commit
        autoflush=False,
    )


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session, committing on success and rolling back on failure."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
