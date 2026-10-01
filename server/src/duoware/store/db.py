"""state.db: the small, precious SQLite file (DECISIONS.md D9).

Schema is versioned with `PRAGMA user_version`; `MIGRATIONS[i]` upgrades version i to i + 1, so opening an
existing file is idempotent. One connection is shared by the whole process behind `StateDb.lock`; use
`tx()` for writes (commit on success, roll back on any exception).
"""

import json
import secrets
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SERVER_ID_BYTES = 8          # PROTOCOL.md §1: server_id is 16 lowercase hex characters
TAG_ID_MAX = 49              # PROTOCOL.md §1: DICT_4X4_50

_SCHEMA_V1 = f"""
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE tags (
    id INTEGER PRIMARY KEY CHECK (id BETWEEN 0 AND {TAG_ID_MAX}),
    role TEXT NOT NULL, doc TEXT NOT NULL, version INTEGER NOT NULL,
    updated_wall_ms INTEGER NOT NULL, updated_by TEXT NOT NULL);
CREATE TABLE layout (id INTEGER PRIMARY KEY CHECK (id = 1), doc TEXT NOT NULL);
CREATE TABLE venue_presets (
    name TEXT PRIMARY KEY, description TEXT NOT NULL, doc TEXT NOT NULL, created_wall_ms INTEGER NOT NULL);
CREATE TABLE pairings (
    device_id TEXT PRIMARY KEY, cam INTEGER UNIQUE NOT NULL, token_sha256 TEXT NOT NULL,
    model TEXT, created_wall_ms INTEGER NOT NULL, last_seen_wall_ms INTEGER);
CREATE TABLE camera_overrides (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE camera_caps (cam INTEGER PRIMARY KEY, doc TEXT NOT NULL, updated_wall_ms INTEGER NOT NULL);
"""


def _migrate_to_1(conn: sqlite3.Connection) -> None:
    for statement in _SCHEMA_V1.split(";"):
        if statement.strip():
            conn.execute(statement)


def _migrate_to_2(conn: sqlite3.Connection) -> None:
    """M2: benchmark runs from the phones (PROTOCOL.md §4.7)."""
    conn.execute("CREATE TABLE benchmarks (id INTEGER PRIMARY KEY, cam INTEGER NOT NULL, run_id TEXT NOT NULL, "
                 "received_wall_ms INTEGER NOT NULL, doc TEXT NOT NULL)")
    conn.execute("CREATE INDEX benchmarks_cam ON benchmarks (cam, id)")


MIGRATIONS = [_migrate_to_1, _migrate_to_2]


class StateDb:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()
        if self.get_meta("server_id") is None:
            self.set_meta("server_id", secrets.token_hex(SERVER_ID_BYTES))

    def _migrate(self) -> None:
        with self.lock:
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            for target in range(version, len(MIGRATIONS)):
                self.conn.execute("BEGIN")
                try:
                    MIGRATIONS[target](self.conn)
                    self.conn.execute(f"PRAGMA user_version = {target + 1}")
                    self.conn.execute("COMMIT")
                except BaseException:
                    self.conn.execute("ROLLBACK")
                    raise

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Holds the lock for the whole transaction. Commits on success, rolls back on any exception."""
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    @property
    def schema_version(self) -> int:
        return self.query("PRAGMA user_version")[0][0]

    def get_meta(self, key: str) -> str | None:
        rows = self.query("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0][0] if rows else None

    def set_meta(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      (key, value))

    def get_int_meta(self, key: str, default: int = 0) -> int:
        v = self.get_meta(key)
        return int(v) if v is not None else default

    @property
    def server_id(self) -> str:
        return self.get_meta("server_id") or ""

    def get_json(self, sql: str, params: tuple = ()) -> Any:
        rows = self.query(sql, params)
        return json.loads(rows[0][0]) if rows else None

    def close(self) -> None:
        with self.lock:
            self.conn.close()
