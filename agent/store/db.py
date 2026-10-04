"""SQLite connection and versioned SQL migrations for ``data/agent.sqlite``."""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    found = []
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_RE.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    versions = [v for v, _ in found]
    if len(versions) != len(set(versions)):
        raise RuntimeError("duplicate migration versions")
    return found


class Database:
    """One shared connection guarded by a lock; operations are short and local."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()
        self.migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @property
    def schema_version(self) -> int:
        row = self._conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_version").fetchone()
        return int(row["v"])

    def migrate(self) -> int:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_ts INTEGER NOT NULL)"
            )
            current = self.schema_version
            for version, path in migration_files():
                if version <= current:
                    continue
                script = path.read_text(encoding="utf-8")
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    for statement in _split_sql(script):
                        self._conn.execute(statement)
                    self._conn.execute(
                        "INSERT INTO schema_version(version, applied_ts) VALUES(?, strftime('%s','now'))",
                        (version,),
                    )
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
            return self.schema_version

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def fetchone(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def scalar(self, sql: str, params: tuple | dict = ()) -> Any:
        row = self.fetchone(sql, params)
        return None if row is None else row[0]


def _split_sql(script: str) -> list[str]:
    lines = [line for line in script.splitlines() if not line.strip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]
