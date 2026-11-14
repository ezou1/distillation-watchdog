"""Tests for the lab gateway: x_replay exclusion, block list, inline checks, clock, backends, gzip."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from dw.common.chat import tool_defs
from dw.common.config import CFG
from dw.common.schema import RequestEvent
from dw.common.sink import Blocklist, DirectBlocklist, DirectSink, HttpSink
from dw.lab.backends import LiveBackend
from dw.lab.gateway import Lab, build_app

FIXTURES = Path(__file__).parent / "fixtures"
MARKER = "XREPLAY-SECRET-MARKER-7f3a9"


class StubWorker:
    """A watchdog worker stand-in that stores events and serves a fixed block list."""

    def __init__(self, blocked: Blocklist | None = None) -> None:
        self.events: list[RequestEvent] = []
        self.blocked = blocked or {}

    def ingest(self, ev: RequestEvent) -> None:
        self.events.append(ev)

    def blocklist(self) -> Blocklist:
        return self.blocked


class FakeClient:
    """An LLM client stand-in that returns the recorded OpenRouter response."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages: list[dict[str, Any]], *, cache: bool = True, **params: Any) -> dict[str, Any]:
        self.calls.append({"messages": messages, "cache": cache, **params})
        return json.loads((FIXTURES / "openrouter_lab_toolcall.json").read_text())


def make_lab(blocked: Blocklist | None = None) -> tuple[Lab, StubWorker, FakeClient]:
    worker, client = StubWorker(blocked), FakeClient()
    return Lab(DirectSink(worker), DirectBlocklist(worker), LiveBackend(client)), worker, client


def replay_body(text: str = "Fix the bug in parser.py", temperature: float = 1.0) -> dict[str, Any]:
    return {"model": "m", "messages": [{"role": "user", "content": text}],
            "tools": tool_defs("standard_agent"), "temperature": temperature, "max_tokens": 1000,
            "x_replay": {"step": [
                {"role": "assistant", "text": MARKER},
                {"role": "tool_call", "text": "", "tool": "read", "args": {"path": MARKER + ".py"}}]}}


async def test_x_replay_never_leaves_the_lab() -> None:
    lab, worker, _ = make_lab()
    body = replay_body()
    status, resp = await lab.handle("acct-a", body, 100.0)
    assert status == 200
    assert MARKER in json.dumps(resp)            # the caller gets the recorded step
    assert "x_replay" in body                     # the caller's dict is not changed
    ev = worker.events[0]
    assert MARKER not in ev.model_dump_json() and "x_replay" not in ev.model_dump_json()
    assert ev.backend == "replay" and ev.finish_reason == "tool_calls"

    sink = HttpSink("http://watchdog")
    sink.emit(ev)
    sent: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        return httpx.Response(200, json={"accepted": 1, "dropped": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await sink._flush(client)
    assert len(sent) == 1 and b"acct-a" in sent[0]
    assert MARKER.encode() not in sent[0] and b"x_replay" not in sent[0]


async def test_blocklist_returns_403_and_still_emits_event() -> None:
    blocked = {"acct-bad": {"reason": "score 0.97", "case_id": "cmp-0001"}}
    lab, worker, _ = make_lab(blocked)
    status, resp = await lab.handle("acct-bad", replay_body(), 5.0)
    assert status == 403
    assert resp == {"error": "blocked", "reason": "score 0.97", "case_id": "cmp-0001"}
    ev = worker.events[-1]
    assert ev.status == 403 and ev.finish_reason is None and ev.backend == "none"
    assert ev.account_id == "acct-bad" and ev.messages[0].text == "Fix the bug in parser.py"
    assert (await lab.handle("acct-good", replay_body(), 5.0))[0] == 200
    assert lab.stats.blocked == 1 and lab.stats.served == 1


async def test_live_backend_only_for_agent_account() -> None:
    lab, worker, client = make_lab()
    body = replay_body()
    body.pop("x_replay")
    status, resp = await lab.handle("acct-other", body, 5.0)
    assert status == 400 and resp["error"] == "live backend not allowed" and "x_replay" in resp["detail"]
    assert client.calls == [] and lab.stats.live == 0 and lab.stats.errors == 1
    ev = worker.events[-1]
    assert ev.status == 400 and ev.backend == "none" and ev.account_id == "acct-other"
    assert (await lab.handle(CFG["agent"]["account_key"], body, 5.0))[0] == 200
    assert len(client.calls) == 1 and lab.stats.live == 1


async def test_block_check_comes_before_live_check() -> None:
    lab, worker, client = make_lab({"acct-hydra": {"reason": "r", "case_id": "c"}})
    body = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
    status, resp = await lab.handle("acct-hydra", body, 5.0)
    assert status == 403 and resp["error"] == "blocked" and client.calls == []
    assert worker.events[-1].status == 403


async def test_dup_counters_and_inline_flags() -> None:
    lab, worker, _ = make_lab()
    for _ in range(2):
        await lab.handle("acct-a", replay_body(temperature=1.0), 1.0)
    await lab.handle("acct-b", replay_body(temperature=1.0), 1.0)
    await lab.handle("acct-c", replay_body("other task", temperature=0.2), 1.0)
    await lab.handle("acct-c", replay_body("other task", temperature=0.2), 1.0)
    a1, a2, b1, c1, c2 = worker.events
    assert (a1.dup_count, a1.global_dup_count, a1.inline_flags) == (1, 1, [])
    assert (a2.dup_count, a2.global_dup_count, a2.inline_flags) == (2, 2, ["dup", "dup_hot"])
    assert (b1.dup_count, b1.global_dup_count, b1.inline_flags) == (1, 3, ["cross_dup"])
    assert c2.inline_flags == ["dup"] and c1.key != a1.key
    assert len({e.request_id for e in worker.events}) == 5


async def test_prefix_key_continues_across_steps() -> None:
    lab, worker, _ = make_lab()
    first = replay_body()
    _, resp = await lab.handle("acct-a", first, 1.0)
    assistant = resp["choices"][0]["message"]
    call_id = assistant["tool_calls"][0]["id"]
    second = dict(first, messages=[*first["messages"], assistant,
                                   {"role": "tool", "tool_call_id": call_id, "content": "def parse(): pass"}])
    await lab.handle("acct-a", second, 2.0)
    e1, e2 = worker.events
    assert e2.prefix_hashes[: len(e1.prefix_hashes)] == e1.prefix_hashes
    assert e2.key != e1.key and e1.key == e1.prefix_hashes[-1]


async def test_registration_and_sim_clock() -> None:
    lab, worker, _ = make_lab()
    lab.register("acct-bench", "benchmark", 20000)
    await lab.handle("acct-bench", replay_body(), 1234.5)
    await lab.handle("acct-new", replay_body(), None)
    bench, new = worker.events
    assert (bench.declared_use, bench.quota, bench.sim_time) == ("benchmark", 20000, 1234.5)
    assert new.declared_use is None
    assert CFG["sim"]["epoch_start"] <= new.sim_time < CFG["sim"]["epoch_start"] + 3600


async def test_live_backend_passes_parameters_without_cache() -> None:
    lab, worker, client = make_lab()
    body = replay_body()
    body.pop("x_replay")
    status, resp = await lab.handle(CFG["agent"]["account_key"], body, None)
    assert status == 200 and resp["choices"][0]["finish_reason"] == "tool_calls"
    call = client.calls[0]
    assert call["cache"] is False and call["model"] == CFG["models"]["lab_model"]
    assert call["tools"] == body["tools"] and call["temperature"] == 1.0 and call["max_tokens"] == 1000
    assert worker.events[0].backend == "live" and lab.stats.live == 1


async def test_live_backend_fixture_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DW_LIVE_FIXTURE", str(FIXTURES / "openrouter_lab_final.json"))
    client = FakeClient()
    resp = await LiveBackend(client).respond({"messages": [{"role": "user", "content": "hi"}]})
    assert resp["choices"][0]["finish_reason"] == "stop" and client.calls == []


async def test_live_backend_failure_gives_502() -> None:
    class Broken:
        async def chat(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("LLM call failed")

    worker = StubWorker()
    lab = Lab(DirectSink(worker), DirectBlocklist(worker), LiveBackend(Broken()))
    status, _ = await lab.handle(CFG["agent"]["account_key"], {"messages": [{"role": "user", "content": "x"}]}, 1.0)
    assert status == 502 and worker.events[0].status == 502


async def test_http_routes_and_gzip_body() -> None:
    lab, worker, _ = make_lab()
    app = build_app(lab)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://lab") as client:
        data = gzip.compress(json.dumps(replay_body()).encode())
        resp = await client.post("/v1/chat/completions", content=data, headers={
            "Authorization": "Bearer acct-z", "Content-Encoding": "gzip",
            "Content-Type": "application/json", "X-Sim-Time": "777.0"})
        assert resp.status_code == 200 and resp.json()["object"] == "chat.completion"
        plain = await client.post("/v1/chat/completions", json=replay_body(), headers={"Authorization": "Bearer acct-z"})
        assert plain.status_code == 200
        assert (await client.post("/v1/chat/completions", json=replay_body())).status_code == 401
        assert (await client.post("/admin/accounts", json={"account_id": "acct-q", "declared_use": "benchmark",
                                                           "quota": 5})).json() == {"ok": True}
        health = (await client.get("/health")).json()
        stats = (await client.get("/stats")).json()
    assert worker.events[0].sim_time == 777.0 and worker.events[1].dup_count == 2
    assert health["status"] == "ok" and health["sim_time"] >= CFG["sim"]["epoch_start"]
    assert stats["served"] == 2 and stats["replay"] == 2 and "p95_ms" in stats
    assert lab.accounts["acct-q"].quota == 5
