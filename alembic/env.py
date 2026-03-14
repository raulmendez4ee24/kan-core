import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

# Asegura que el root del proyecto esté en sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

from models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _normalize_database_url(raw_url: str, *, async_driver: bool) -> str:
    url_text = str(raw_url or "").strip()
    if not url_text:
        url_text = "postgresql+asyncpg://postgres:postgres@localhost:5432/kan_core"
    if url_text.startswith("postgres://"):
        url_text = url_text.replace("postgres://", "postgresql://", 1)
    parsed = make_url(url_text)
    driver = parsed.drivername
    if async_driver:
        if driver in {"postgres", "postgresql", "postgresql+psycopg2", "postgresql+psycopg"}:
            parsed = parsed.set(drivername="postgresql+asyncpg")
    else:
        if driver == "postgres":
            parsed = parsed.set(drivername="postgresql")
        elif driver == "postgresql+asyncpg":
            parsed = parsed.set(drivername="postgresql+psycopg2")
    return str(parsed)


def get_async_database_url() -> str:
    return _normalize_database_url(os.getenv("DATABASE_URL", ""), async_driver=True)


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = _normalize_database_url(os.getenv("DATABASE_URL", ""), async_driver=False)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    config.set_main_option("sqlalchemy.url", get_async_database_url())
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
