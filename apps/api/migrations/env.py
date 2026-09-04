"""Alembic environment.

The database URL comes from the application's own validated settings
rather than from ``alembic.ini``. Two reasons: migrations and the running
app can then never disagree about which database they point at, and no
credential is written into a tracked file.

One Neon-specific detail matters. Migrations must run against the
*direct* endpoint, not the pooled one. PgBouncer in transaction mode
cannot hold the session state that DDL and advisory locks rely on, so a
migration against the pooled endpoint fails in confusing ways. The URL
normaliser detects a pooled host and disables prepared statements, but
for migrations the right answer is to use the direct URL.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Imported for their registration side effect. Without these, autogenerate
# sees an empty metadata and cheerfully proposes dropping every table.
from app.core import models as _core_models  # noqa: F401
from app.core.config import get_settings
from app.db import Base, direct_endpoint, normalise_database_url
from app.hiring import models as _hiring_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_settings = get_settings()
# Force the direct endpoint. Running DDL through Neon's pooler fails in
# ways that look like network problems rather than a wrong URL.
_url, _connect_args = normalise_database_url(direct_endpoint(_settings.database_url))
config.set_main_option("sqlalchemy.url", _url.replace("%", "%%"))


#: Tables that may exist in the database but are not owned by this app.
_NOT_OURS = frozenset({"spatial_ref_sys"})


def _include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Keep autogenerate focused on tables this application owns.

    Without this, an extension table that Neon or an add-on created would
    be proposed for deletion the first time anyone runs autogenerate.
    """
    return not (type_ == "table" and name in _NOT_OURS)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting, for review or manual apply."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Without these, a changed column type or default is silently
        # skipped by autogenerate and the schema quietly drifts.
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Apply migrations through an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=_connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
