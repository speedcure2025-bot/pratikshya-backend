import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.config import settings
from app.models.base import Base
import app.models  # noqa: F401 — registers all SQLAlchemy models into Base.metadata

# Allow overriding DATABASE_URL via shell environment (takes priority over .env)
# This lets you run: DATABASE_URL=... alembic upgrade head  to target a different DB
_db_url = os.environ.get("DATABASE_URL") or settings.DATABASE_URL

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = _db_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table="pratikshya_alembic_version",
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # Ensure the pratikshya schema exists and commit it immediately.
    # Using COMMIT + re-BEGIN so the CREATE SCHEMA is not rolled back if the
    # migration transaction is later aborted.
    connection.execute(text("COMMIT"))
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS pratikshya"))
    connection.execute(text("COMMIT"))
    connection.execute(text("SET search_path TO pratikshya, public"))
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        version_table_schema="pratikshya",
        version_table="pratikshya_alembic_version",
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _db_url

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
