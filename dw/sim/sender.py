"""Send scenario requests to the lab in simulation-time order at a speed factor.

The sender uses async httpx, a limit of requests in flight, and gzip request bodies.
One Counters class counts sent, served, blocked, and leaked trajectories per persona.
"""
from __future__ import annotations

import asyncio
import gzip
import itertools
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Optional

import httpx

from dw.common.config import CFG
from dw.sim.scenario import SimRequest


@dataclass
class PersonaCounts:
    sent: int = 0
    served: int = 0
    blocked: int = 0
    leaked: int = 0
    errors: int = 0


class Counters:
    """Counts per persona. A session stops after its first 403, as a real client cannot continue it."""

    def __init__(self) -> None:
        self.by_persona: dict[str, PersonaCounts] = defaultdict(PersonaCounts)
        self._leaked: set[str] = set()
        self._stopped: set[str] = set()
        self._sent: set[int] = set()

    def skip(self, r: SimRequest) -> bool:
        return r.session_ref in self._stopped

    def sent(self, r: SimRequest) -> None:
        self.by_persona[r.persona].sent += 1
        self._sent.add(id(r))

    def was_sent(self, r: SimRequest) -> bool:
        """True if this request object went out already. A resumed persona leaves these requests out.

        The key is the object, not its content: best-of-n copies have equal content and each must go out once.
        The generator keeps one scenario per scale for its whole life, so the objects do not change.
        """
        return id(r) in self._sent

    def record(self, r: SimRequest, status: int) -> None:
        c = self.by_persona[r.persona]
        if status == 200:
            c.served += 1
            if r.final and r.session_ref not in self._leaked:
                self._leaked.add(r.session_ref)
                c.leaked += 1
        elif status == 403:
            c.blocked += 1
            self._stopped.add(r.session_ref)
        else:
            c.errors += 1

    def snapshot(self) -> dict[str, dict[str, int]]:
        return {name: asdict(c) for name, c in self.by_persona.items()}


def encode(body: dict) -> bytes:
    """JSON body, gzip-compressed (fast level)."""
    return gzip.compress(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), compresslevel=1)


def make_clients(lab_url: str, transport: Optional[httpx.AsyncBaseTransport] = None) -> list[httpx.AsyncClient]:
    """Several small clients share the in-flight limit. One large httpx pool is slow at high concurrency."""
    count = CFG["sim"]["client_pool"]
    per_client = max(1, CFG["sim"]["max_in_flight"] // count)
    limits = httpx.Limits(max_connections=per_client, max_keepalive_connections=per_client)
    return [httpx.AsyncClient(base_url=lab_url, transport=transport, timeout=CFG["sim"]["send_timeout_s"],
                              limits=limits) for _ in range(count)]


class Sender:
    """Replay request lists against the lab. Several runs can share one sender and its in-flight limit."""

    def __init__(self, clients: list[httpx.AsyncClient], counters: Counters) -> None:
        self.clients = clients
        self.counters = counters
        self._next = itertools.count()
        self.sim_time: Optional[float] = None
        self._slots = asyncio.Semaphore(CFG["sim"]["max_in_flight"])

    async def run(self, requests: list[SimRequest], speed: float, offset: float = 0.0) -> None:
        """Send all requests in time order. `offset` moves all simulation times. Cancel the task to stop."""
        if not requests:
            return
        loop = asyncio.get_running_loop()
        real0, sim0 = loop.time(), requests[0].sim_time
        pending: set[asyncio.Task[None]] = set()
        try:
            for r in requests:
                delay = real0 + (r.sim_time - sim0) / speed - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                if self.counters.skip(r):
                    continue
                await self._slots.acquire()
                task = asyncio.create_task(self._send(r, r.sim_time + offset))
                pending.add(task)
                task.add_done_callback(pending.discard)
        finally:
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def _send(self, r: SimRequest, sim_time: float) -> None:
        try:
            self.sim_time = sim_time
            self.counters.sent(r)
            headers = {"Authorization": f"Bearer {r.key}", "X-Sim-Time": f"{sim_time:.3f}",
                       "Content-Type": "application/json", "Content-Encoding": "gzip"}
            try:
                client = self.clients[next(self._next) % len(self.clients)]
                resp = await client.post("/v1/chat/completions", content=encode(r.body), headers=headers)
                self.counters.record(r, resp.status_code)
            except httpx.HTTPError:
                self.counters.record(r, 0)
        finally:
            self._slots.release()
