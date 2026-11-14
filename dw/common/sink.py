"""Event sinks (lab to watchdog) and block list sources (watchdog to lab).

HTTP versions run in live mode. Direct versions call a watchdog Worker in the same process (offline mode).
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any, Protocol

import httpx

from dw.common.config import CFG
from dw.common.schema import RequestEvent

log = logging.getLogger("dw.sink")

# Block list format: account_id -> {"reason": str, "case_id": str}
Blocklist = dict[str, dict[str, str]]


class EventSink(Protocol):
    def emit(self, ev: RequestEvent) -> None: ...


class BlocklistSource(Protocol):
    def get(self) -> Blocklist: ...


class WorkerLike(Protocol):
    """The part of the watchdog Worker that the direct sink and block list use."""

    def ingest(self, ev: RequestEvent) -> None: ...
    def blocklist(self) -> Blocklist: ...


class DirectSink:
    """Give events straight to an in-process Worker."""

    def __init__(self, worker: WorkerLike) -> None:
        self.worker = worker

    def emit(self, ev: RequestEvent) -> None:
        self.worker.ingest(ev)


class DirectBlocklist:
    """Read the block list straight from an in-process Worker."""

    def __init__(self, worker: WorkerLike) -> None:
        self.worker = worker

    def get(self) -> Blocklist:
        return self.worker.blocklist()


class HttpSink:
    """Buffer events and POST them in batches to the watchdog `/ingest` (fire and forget)."""

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/") + "/ingest"
        self.buffer: deque[RequestEvent] = deque(maxlen=CFG["lab"]["ingest_buffer_max"])
        self.connected = False
        self.dropped = 0
        self._task: asyncio.Task[None] | None = None

    def emit(self, ev: RequestEvent) -> None:
        if len(self.buffer) == self.buffer.maxlen:
            self.dropped += 1
        self.buffer.append(ev)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        interval = CFG["lab"]["ingest_interval_s"]
        async with httpx.AsyncClient(timeout=5.0) as client:
            while True:
                await asyncio.sleep(interval)
                await self._flush(client)

    async def _flush(self, client: httpx.AsyncClient) -> None:
        if not self.buffer:
            return
        batch = [self.buffer.popleft() for _ in range(len(self.buffer))]
        payload: list[dict[str, Any]] = [ev.model_dump() for ev in batch]
        try:
            resp = await client.post(self.url, json={"events": payload})
            resp.raise_for_status()
            self.connected = True
        except httpx.HTTPError as exc:
            if self.connected:
                log.warning("watchdog ingest failed: %s", exc)
            self.connected = False
            self.dropped += len(batch)


class HttpBlocklist:
    """Poll the watchdog `GET /blocklist` every second. Keep the last good copy."""

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/") + "/blocklist"
        self.current: Blocklist = {}
        self.connected = False
        self._task: asyncio.Task[None] | None = None

    def get(self) -> Blocklist:
        return self.current

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        interval = CFG["lab"]["blocklist_interval_s"]
        async with httpx.AsyncClient(timeout=5.0) as client:
            while True:
                try:
                    resp = await client.get(self.url)
                    resp.raise_for_status()
                    self.current = resp.json().get("accounts", {})
                    self.connected = True
                except (httpx.HTTPError, ValueError) as exc:
                    if self.connected:
                        log.warning("watchdog blocklist failed: %s", exc)
                    self.connected = False
                await asyncio.sleep(interval)
