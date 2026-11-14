"""Tests for the sender: gzip bodies, headers, time order, counters, and throughput against a local server."""
from __future__ import annotations

import asyncio
import gzip
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from dw.sim.scenario import Scenario, build_scenario
from dw.sim.sender import Counters, Sender, make_clients

FIX = Path(__file__).parent / "fixtures" / "canonical_small"


@pytest.fixture(scope="module")
def scenario() -> Scenario:
    return build_scenario("holdout", 301, "small", data_dir=FIX)


class FakeLab:
    """Records requests. Accounts in `blocked` get 403."""

    def __init__(self, blocked: set[str] = frozenset()) -> None:
        self.blocked = blocked
        self.seen: list[tuple[str, float, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["content-encoding"] == "gzip"
        body = json.loads(gzip.decompress(request.content))
        key = request.headers["authorization"].removeprefix("Bearer ")
        self.seen.append((key, float(request.headers["x-sim-time"]), body))
        if key in self.blocked:
            return httpx.Response(403, json={"error": "blocked", "reason": "test", "case_id": "c1"})
        return httpx.Response(200, json={"choices": []})


async def _send(requests, lab: FakeLab, speed: float = 1e12, offset: float = 0.0) -> Counters:
    counters = Counters()
    clients = make_clients("http://lab", httpx.MockTransport(lab))
    await Sender(clients, counters).run(requests, speed, offset)
    return counters


def test_gzip_headers_and_counts(scenario: Scenario) -> None:
    reqs = [r for r in scenario.requests if r.persona == "solo_dev"]
    lab = FakeLab()
    counters = asyncio.run(_send(reqs, lab, offset=100.0))
    c = counters.snapshot()["solo_dev"]
    assert c["sent"] == c["served"] == len(reqs) == len(lab.seen)
    assert c["blocked"] == 0
    assert c["leaked"] == len({r.session_ref for r in reqs if r.final})
    assert {round(t, 3) for _, t, _ in lab.seen} == {round(r.sim_time + 100.0, 3) for r in reqs}
    assert all("x_replay" in body for _, _, body in lab.seen)


def test_blocked_sessions_stop_and_do_not_leak(scenario: Scenario) -> None:
    hydra = [r for r in scenario.requests if r.persona == "hydra"]
    blocked = {hydra[0].key}
    lab = FakeLab(blocked)
    counters = asyncio.run(_send(hydra, lab))
    c = counters.snapshot()["hydra"]
    assert c["blocked"] > 0
    blocked_refs = {r.session_ref for r in hydra if r.key in blocked}
    # A blocked session sends at most its first step (best-of-n copies may be in flight together).
    sent_blocked = [k for k, _, _ in lab.seen if k in blocked]
    assert len(sent_blocked) <= sum(1 for r in hydra if r.key in blocked)
    assert len(sent_blocked) < sum(1 for r in hydra if r.key in blocked)
    open_finals = {r.session_ref for r in hydra if r.final and r.session_ref not in blocked_refs}
    assert c["leaked"] == len(open_finals)


def test_time_order_and_speed(scenario: Scenario) -> None:
    reqs = [r for r in scenario.requests if r.persona == "solo_dev"][:40]
    span = reqs[-1].sim_time - reqs[0].sim_time
    speed = span / 0.5          # the span takes about 0.5 s
    lab = FakeLab()
    t0 = time.monotonic()
    asyncio.run(_send(reqs, lab, speed=speed))
    assert time.monotonic() - t0 >= 0.45
    times = [t for _, t, _ in lab.seen]
    assert times == sorted(times)


def test_errors_counted(scenario: Scenario) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")
    counters = Counters()

    async def go() -> None:
        await Sender(make_clients("http://lab", httpx.MockTransport(broken)), counters).run(scenario.requests[:20], 1e12)
    asyncio.run(go())
    assert sum(c["errors"] for c in counters.snapshot().values()) == 20


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def asgi_lab(scope, receive, send):
    """A minimal lab that reads the body and answers 200. The throughput test runs it in a subprocess."""
    if scope["type"] != "http":
        return
    more = True
    while more:
        more = (await receive()).get("more_body", False)
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": b"{}"})


def test_throughput_over_300_rps(scenario: Scenario) -> None:
    """The sender must reach more than 300 requests per second against a local server."""
    port = _free_port()
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "test_sim_sender:asgi_lab", "--app-dir",
                               str(Path(__file__).parent), "--port", str(port), "--log-level", "error"])
    reqs = scenario.requests[:3000]
    counters = Counters()

    async def go() -> float:
        clients = make_clients(f"http://127.0.0.1:{port}")
        for _ in range(100):
            try:
                await clients[0].get("/")
                break
            except httpx.ConnectError:
                await asyncio.sleep(0.1)
        t0 = time.perf_counter()
        await Sender(clients, counters).run(reqs, 1e12)
        elapsed = time.perf_counter() - t0
        for c in clients:
            await c.aclose()
        return elapsed
    try:
        elapsed = asyncio.run(go())
    finally:
        server.terminate()
        server.wait(5)
    served = sum(c["served"] for c in counters.snapshot().values())
    assert served == len(reqs)
    rate = len(reqs) / elapsed
    print(f"sender rate: {rate:.0f} req/s")
    assert rate > 300
