"""SQLite FSM storage so conversation state survives restarts."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey


class SqliteStorage(BaseStorage):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fsm (
                    key TEXT PRIMARY KEY,
                    state TEXT,
                    data TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.commit()

    @staticmethod
    def _key(key: StorageKey) -> str:
        destiny = getattr(key, "destiny", "")
        thread = getattr(key, "thread_id", None)
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{thread}:{destiny}"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        state_s = state.state if hasattr(state, "state") else state  # type: ignore[union-attr]
        k = self._key(key)
        async with self._lock:
            await asyncio.to_thread(self._set_state, k, state_s)

    def _set_state(self, k: str, state: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fsm(key, state, data) VALUES(?, ?, '{}')
                ON CONFLICT(key) DO UPDATE SET state=excluded.state
                """,
                (k, state),
            )
            conn.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        k = self._key(key)
        async with self._lock:
            return await asyncio.to_thread(self._get_state, k)

    def _get_state(self, k: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT state FROM fsm WHERE key=?", (k,)).fetchone()
            return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        k = self._key(key)
        payload = json.dumps(dict(data), ensure_ascii=False)
        async with self._lock:
            await asyncio.to_thread(self._set_data, k, payload)

    def _set_data(self, k: str, payload: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fsm(key, state, data) VALUES(?, NULL, ?)
                ON CONFLICT(key) DO UPDATE SET data=excluded.data
                """,
                (k, payload),
            )
            conn.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        k = self._key(key)
        async with self._lock:
            raw = await asyncio.to_thread(self._get_data, k)
        return json.loads(raw) if raw else {}

    def _get_data(self, k: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM fsm WHERE key=?", (k,)).fetchone()
            return row["data"] if row else None

    async def close(self) -> None:
        return None
