"""Append-only SQLite event log of the watchdog (WAL mode, batch insert, zlib-compressed bodies).

The store also keeps the most recent events in memory, so that evidence lookup works without SQLite.
"""
from __future__ import annotations

import sqlite3
import time
import zlib
from collections import OrderedDict
from pathlib import Path

from dw.common.config import CFG
from dw.common.schema import RequestEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    request_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    sim_time REAL NOT NULL,
    dropped INTEGER NOT NULL DEFAULT 0,
    body BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS events_request_id ON events(request_id);
"""


def _compress(ev: RequestEvent) -> bytes:
    return zlib.compress(ev.model_dump_json().encode("utf-8"))


def _decompress(blob: bytes) -> RequestEvent:
    return RequestEvent.model_validate_json(zlib.decompress(blob))


class EventStore:
    """Event log. With `path=None` it keeps events in memory only."""

    def __init__(self, path: Path | None = None, memory_max: int | None = None,
                 flush_s: float | None = None) -> None:
        self.memory: OrderedDict[str, RequestEvent] = OrderedDict()
        self.memory_max = memory_max or CFG["watchdog"]["memory_events"]
        self.flush_s = CFG["watchdog"]["sqlite_flush_s"] if flush_s is None else flush_s
        self.pending: list[tuple[str, str, float, int, bytes]] = []
        self.last_flush = time.monotonic()
        self.conn: sqlite3.Connection | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(path), check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(_SCHEMA)

    def append(self, ev: RequestEvent, dropped: bool = False) -> None:
        """Add one event. A dropped event goes only to SQLite (the worker never saw it)."""
        if not dropped:
            self._remember(ev)
        if self.conn is None:
            return
        self.pending.append((ev.request_id, ev.account_id, ev.sim_time, int(dropped), _compress(ev)))
        if time.monotonic() - self.last_flush >= self.flush_s:
            self.flush()

    def _remember(self, ev: RequestEvent) -> None:
        self.memory[ev.request_id] = ev
        while len(self.memory) > self.memory_max:
            self.memory.popitem(last=False)

    def flush(self) -> None:
        """Write the pending batch to SQLite in one transaction."""
        self.last_flush = time.monotonic()
        if self.conn is None or not self.pending:
            return
        batch, self.pending = self.pending, []
        with self.conn:
            self.conn.executemany(
                "INSERT INTO events (request_id, account_id, sim_time, dropped, body) VALUES (?, ?, ?, ?, ?)",
                batch)

    def get(self, request_id: str) -> RequestEvent | None:
        """Return the event with this request ID, from memory first, then from SQLite."""
        ev = self.memory.get(request_id)
        if ev is not None or self.conn is None:
            return ev
        self.flush()
        row = self.conn.execute(
            "SELECT body FROM events WHERE request_id = ? ORDER BY rowid DESC LIMIT 1", (request_id,)).fetchone()
        return _decompress(row[0]) if row else None

    def count(self) -> int:
        """Number of rows in SQLite (after a flush)."""
        if self.conn is None:
            return 0
        self.flush()
        return int(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def clear_memory(self) -> None:
        """Forget the in-memory events. The SQLite log stays (append-only)."""
        self.memory.clear()

    def close(self) -> None:
        self.flush()
        if self.conn is not None:
            self.conn.close()
            self.conn = None
