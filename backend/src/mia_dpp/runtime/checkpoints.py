"""LangGraph checkpoint backends: SQLite locally, PostgreSQL in production."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from mia_dpp.config import Settings


@asynccontextmanager
async def open_checkpointer(
    settings: Settings,
) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    if settings.database_url:
        from langgraph.checkpoint.postgres.aio import (
            AsyncPostgresSaver,
        )

        async with AsyncPostgresSaver.from_conn_string(settings.database_url) as saver:
            await saver.setup()
            yield saver
        return

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    settings.thread_store_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(settings.thread_store_path)) as saver:
        yield saver
