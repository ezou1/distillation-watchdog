"""The lab gateway: an OpenAI-compatible model API that reports each request to the watchdog.

`Lab.handle()` is the one core function for the HTTP server and for the offline evaluation.
It removes the private `x_replay` field first. No event that leaves the lab contains `x_replay` content.
Run `python -m dw.lab.gateway` to start the server on the lab port with a terminal view.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from dw.common.chat import from_openai, schema_id_of
from dw.common.config import CFG
from dw.common.hashing import prefix_chain
from dw.common.schema import RequestEvent
from dw.common.sink import BlocklistSource, EventSink
from dw.lab.backends import LiveBackend, ReplayBackend

log = logging.getLogger("dw.lab")


@dataclass
class Account:
    declared_use: str | None = None
    quota: int | None = None


def percentile(values: list[float], q: float) -> float:
    """Return the q-quantile (0..1) of the values, 0.0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


class LabStats:
    """Counters and a rolling window of handle() latencies."""

    def __init__(self) -> None:
        self.served = self.blocked = self.errors = self.replay = self.live = 0
        self.latencies: deque[float] = deque(maxlen=CFG["lab"]["stats_window"])
        self.stamps: deque[float] = deque(maxlen=CFG["lab"]["stats_window"])

    def record(self, status: int, backend: str, latency_ms: float) -> None:
        if status == 200:
            self.served += 1
        elif status == 403:
            self.blocked += 1
        else:
            self.errors += 1
        if backend == "replay":
            self.replay += 1
        elif backend == "live":
            self.live += 1
        self.latencies.append(latency_ms)
        self.stamps.append(time.monotonic())

    def rps(self) -> float:
        window = CFG["lab"]["rps_window_s"]
        cutoff = time.monotonic() - window
        return sum(1 for t in self.stamps if t >= cutoff) / window

    def snapshot(self) -> dict[str, Any]:
        lat = list(self.latencies)
        return {"rps": round(self.rps(), 1), "served": self.served, "blocked": self.blocked,
                "errors": self.errors, "replay": self.replay, "live": self.live,
                "p50_ms": round(percentile(lat, 0.50), 3), "p95_ms": round(percentile(lat, 0.95), 3)}


class Lab:
    """Accounts, inline duplicate checks, backend selection, and event emission."""

    def __init__(self, sink: EventSink, blocklist: BlocklistSource, live: LiveBackend | None = None) -> None:
        self.sink = sink
        self.blocklist = blocklist
        self.live = live or LiveBackend()
        self.replay = ReplayBackend()
        self.accounts: dict[str, Account] = {}
        self.dup: dict[tuple[str, str], int] = {}
        self.global_dup: dict[str, int] = {}
        self.stats = LabStats()
        self._run_tag = secrets.token_hex(2)
        self._next_id = 0
        self._start_real = time.time()

    def sim_now(self) -> float:
        """The lab clock: simulation time that runs `sim.speed` times faster than real time."""
        sim = CFG["sim"]
        return sim["epoch_start"] + (time.time() - self._start_real) * sim["speed"]

    def register(self, account_id: str, declared_use: str | None, quota: int | None) -> None:
        self.accounts[account_id] = Account(declared_use, quota)

    async def handle(self, key: str | None, body: dict[str, Any], sim_time: float | None) -> tuple[int, dict]:
        """Serve one chat completion request. Return (HTTP status, JSON body)."""
        t0 = time.perf_counter()
        if not key:
            return 401, {"error": "missing bearer key"}
        body = dict(body)
        x_replay = body.pop("x_replay", None)
        try:
            ev = self._make_event(key, body, sim_time if sim_time is not None else self.sim_now())
        except (TypeError, ValueError, AttributeError) as exc:
            return 400, {"error": "bad request", "detail": str(exc)[:200]}
        block = self.blocklist.get().get(key)
        if block is not None:
            status, resp = 403, {"error": "blocked", "reason": block.get("reason", ""),
                                 "case_id": block.get("case_id", "")}
        else:
            status, resp = await self._respond(ev, body, x_replay)
        ev.status = status
        ev.latency_ms = (time.perf_counter() - t0) * 1000.0
        self.sink.emit(ev)
        self.stats.record(status, ev.backend, ev.latency_ms)
        return status, resp

    def _make_event(self, key: str, body: dict[str, Any], sim_time: float) -> RequestEvent:
        """Build the event from the request without `x_replay`. Update the duplicate counters."""
        acct = self.accounts.setdefault(key, Account())
        messages = from_openai(body.get("messages") or [])
        schema_id = schema_id_of(body.get("tools"))
        chain = prefix_chain(schema_id, messages)
        temperature = float(body["temperature"]) if body.get("temperature") is not None else 1.0
        dup, global_dup = self._count(key, chain[-1])
        self._next_id += 1
        return RequestEvent(
            request_id=f"req-{self._run_tag}{self._next_id:07d}", account_id=key, sim_time=float(sim_time),
            model=str(body.get("model") or CFG["models"]["lab_model"]), tools_schema_id=schema_id,
            temperature=temperature, n=int(body.get("n") or 1), max_tokens=body.get("max_tokens"),
            reasoning_effort=body.get("reasoning_effort"), user=body.get("user"), messages=messages,
            prefix_hashes=chain, key=chain[-1], dup_count=dup, global_dup_count=global_dup,
            inline_flags=inline_flags(dup, global_dup, temperature),
            declared_use=acct.declared_use, quota=acct.quota)

    def _count(self, key: str, full_key: str) -> tuple[int, int]:
        dup = self.dup.get((key, full_key), 0) + 1
        self.dup[(key, full_key)] = dup
        global_dup = self.global_dup.get(full_key, 0) + 1
        self.global_dup[full_key] = global_dup
        return dup, global_dup

    async def _respond(self, ev: RequestEvent, body: dict[str, Any],
                       x_replay: dict[str, Any] | None) -> tuple[int, dict]:
        if x_replay is not None:
            ev.backend = "replay"
            resp = self.replay.respond(x_replay, ev.tools_schema_id, ev.model)
        else:
            ev.backend = "live"
            try:
                resp = await self.live.respond(body)
            except Exception as exc:  # the upstream model failed; the lab keeps serving
                log.warning("live backend failed: %s", exc)
                return 502, {"error": "upstream", "detail": str(exc)[:300]}
        choices = resp.get("choices") or [{}]
        ev.finish_reason = choices[0].get("finish_reason")
        return 200, resp


def inline_flags(dup: int, global_dup: int, temperature: float) -> list[str]:
    """Cheap flags of the inline check. The watchdog uses them as hints only."""
    flags: list[str] = []
    if dup > 1:
        flags.append("dup")
        if temperature >= CFG["detectors"]["d1"]["high_temp"]:
            flags.append("dup_hot")
    if global_dup > dup:
        flags.append("cross_dup")
    return flags


class GzipRequestMiddleware:
    """Decompress request bodies that have `Content-Encoding: gzip`."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        headers = dict(scope.get("headers") or [])
        if scope["type"] != "http" or headers.get(b"content-encoding", b"").lower() != b"gzip":
            await self.app(scope, receive, send)
            return
        raw = b""
        more = True
        while more:
            message = await receive()
            raw += message.get("body", b"")
            more = message.get("more_body", False)
        data = gzip.decompress(raw)
        new_headers = [(k, v) for k, v in scope["headers"] if k not in (b"content-encoding", b"content-length")]
        new_headers.append((b"content-length", str(len(data)).encode()))
        sent = False

        async def replay_receive() -> dict:
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": data, "more_body": False}

        await self.app(dict(scope, headers=new_headers), replay_receive, send)


def bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    return auth[7:].strip() or None


def watchdog_connected(lab: Lab) -> bool:
    return bool(getattr(lab.blocklist, "connected", False))


Lifespan = Callable[[FastAPI], Any]


def build_app(lab: Lab, lifespan: Lifespan | None = None) -> FastAPI:
    """Make the FastAPI app of the lab. The live server adds a lifespan for sinks and the view."""
    app = FastAPI(title="lab", lifespan=lifespan)
    app.add_middleware(GzipRequestMiddleware)

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> JSONResponse:
        try:
            body = json.loads(await request.body())
        except ValueError:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        sim_header = request.headers.get("x-sim-time")
        sim_time = float(sim_header) if sim_header else None
        status, resp = await lab.handle(bearer(request), body, sim_time)
        return JSONResponse(resp, status_code=status)

    @app.post("/admin/accounts")
    async def accounts(request: Request) -> dict:
        data = await request.json()
        lab.register(data["account_id"], data.get("declared_use"), data.get("quota"))
        return {"ok": True}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "sim_time": lab.sim_now(), "watchdog_connected": watchdog_connected(lab)}

    @app.get("/stats")
    async def stats() -> dict:
        return {**lab.stats.snapshot(), "watchdog_connected": watchdog_connected(lab), "sim_time": lab.sim_now()}

    return app


def render_view(lab: Lab) -> Any:
    from dw.common.term import kv_table
    s = lab.stats.snapshot()
    sim = time.strftime("%Y-%m-%d %H:%M", time.gmtime(lab.sim_now()))
    state = "connected" if watchdog_connected(lab) else "NOT CONNECTED"
    return kv_table(f"Lab gateway :{CFG['ports']['lab']}", [
        ("requests/s", s["rps"]), ("served", s["served"]), ("blocked", s["blocked"]),
        ("errors", s["errors"]), ("handle() p50 ms", s["p50_ms"]), ("handle() p95 ms", s["p95_ms"]),
        ("replay / live", f"{s['replay']} / {s['live']}"), ("sim time (UTC)", sim), ("watchdog", state)])


async def warn_if_down(lab: Lab) -> None:
    """Log a warning when the watchdog is not reachable. The lab continues to serve."""
    was_up = True
    while True:
        await asyncio.sleep(3 * CFG["lab"]["blocklist_interval_s"])
        up = watchdog_connected(lab)
        if was_up and not up:
            log.warning("watchdog is not reachable; the lab continues to serve")
        was_up = up


def main() -> None:
    import uvicorn

    from dw.common.sink import HttpBlocklist, HttpSink
    from dw.common.term import run_live

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    url = os.environ.get("WATCHDOG_URL", f"http://127.0.0.1:{CFG['ports']['watchdog']}")
    sink, blocklist = HttpSink(url), HttpBlocklist(url)
    lab = Lab(sink, blocklist)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await sink.start()
        await blocklist.start()
        view = asyncio.create_task(run_live(lambda: render_view(lab)))
        check = asyncio.create_task(warn_if_down(lab))
        yield
        view.cancel()
        check.cancel()
        await sink.stop()
        await blocklist.stop()

    app = build_app(lab, lifespan)
    uvicorn.run(app, host="127.0.0.1", port=CFG["ports"]["lab"], access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
