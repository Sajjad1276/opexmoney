from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.database.models import Base
from dotenv import load_dotenv

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
load_dotenv()
database_url = os.environ["DATABASE_URL"]
async_database_url = database_url
if async_database_url.startswith("postgresql://"):
    async_database_url = async_database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif async_database_url.startswith("postgres://"):
    async_database_url = async_database_url.replace("postgres://", "postgresql+asyncpg://", 1)
config.set_main_option("sqlalchemy.url", async_database_url)


def run_migrations_offline() -> None:
    context.configure(url=database_url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
