"""Tests for the agent loop: recorded fixtures, shell approval, step limit, tools off, and the HTTP API."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from dw.agent.server import REJECTED, Agent, build_app
from dw.agent.tools import Sandbox
from dw.common.config import CFG
from dw.common.sink import DirectBlocklist, DirectSink
from dw.lab.backends import LiveBackend
from dw.lab.gateway import Lab

FIXTURES = Path(__file__).parent / "fixtures"
TOOLCALL = json.loads((FIXTURES / "openrouter_lab_toolcall.json").read_text())
FINAL = json.loads((FIXTURES / "openrouter_lab_final.json").read_text())


def shell_call(command: str) -> dict[str, Any]:
    return {"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None, "tool_calls": [{"id": "call_sh", "type": "function", "function": {
            "name": "run_shell", "arguments": json.dumps({"command": command})}}]}}]}


class ScriptedLab:
    """A lab stand-in that returns a list of responses in order and records the bodies."""

    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.bodies: list[dict[str, Any]] = []

    async def __call__(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.bodies.append(body)
        resp = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return 200, resp


@pytest.fixture()
def box(tmp_path: Path) -> Sandbox:
    sandbox = Sandbox(tmp_path / "ws")
    (sandbox.root / "README.md").write_text("# Demo project\n")
    return sandbox


async def test_loop_with_recorded_fixture(box: Sandbox) -> None:
    lab = ScriptedLab(TOOLCALL, FINAL)
    out = await Agent(box, lab).chat("Summarize the README.", True)
    assert out["status"] == "done" and out["steps"] == 2 and out["pending"] is None
    tool_msg = out["messages"][2]
    assert tool_msg["role"] == "tool" and tool_msg["name"] == "read_file" and "Demo project" in tool_msg["content"]
    assert out["messages"][1]["tool_calls"][0]["arguments"] == {"path": "README.md"}
    assert out["messages"][-1]["content"] == FINAL["choices"][0]["message"]["content"]
    second = lab.bodies[1]
    assert second["tools"] and second["messages"][0]["role"] == "system"
    assert second["messages"][-1]["role"] == "tool"


async def test_shell_waits_for_approval(box: Sandbox) -> None:
    agent = Agent(box, ScriptedLab(shell_call("touch made.txt"), FINAL))
    out = await agent.chat("Make a file.", True)
    assert out["status"] == "awaiting_approval" and out["pending"]["command"] == "touch made.txt"
    assert not (box.root / "made.txt").exists()
    blocked = await agent.chat("Another message.", True)
    assert "pending" in blocked["error"] and not (box.root / "made.txt").exists()
    assert (await agent.approve("ap-9999", True))["error"]
    out = await agent.approve(out["pending"]["approval_id"], True)
    assert (box.root / "made.txt").exists()
    assert out["status"] == "done" and out["messages"][2]["content"].startswith("exit code 0")


async def test_shell_reject_does_not_run(box: Sandbox) -> None:
    agent = Agent(box, ScriptedLab(shell_call("touch made.txt"), FINAL))
    pending = (await agent.chat("Make a file.", True))["pending"]
    out = await agent.approve(pending["approval_id"], False)
    assert not (box.root / "made.txt").exists()
    assert out["messages"][2]["content"] == REJECTED and out["status"] == "done"


async def test_step_limit(box: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(CFG["agent"], "max_steps", 3)
    out = await Agent(box, ScriptedLab(TOOLCALL)).chat("Loop.", True)
    assert out["status"] == "step_limit" and out["steps"] == 3


async def test_tools_off_sends_no_tools(box: Sandbox) -> None:
    lab = ScriptedLab(FINAL)
    out = await Agent(box, lab).chat("Hello", False)
    assert out["status"] == "done" and "tools" not in lab.bodies[0]


async def test_lab_error_is_reported(box: Sandbox) -> None:
    async def blocked(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 403, {"error": "blocked", "reason": "r", "case_id": "c"}

    out = await Agent(box, blocked).chat("Hi", True)
    assert out["status"] == "error" and "403" in out["error"]


async def test_agent_through_lab_with_live_fixture(box: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DW_LIVE_FIXTURE", str(FIXTURES / "openrouter_lab_final.json"))

    class Worker:
        events: list = []
        def ingest(self, ev: Any) -> None: self.events.append(ev)
        def blocklist(self) -> dict: return {}

    worker = Worker()
    lab = Lab(DirectSink(worker), DirectBlocklist(worker), LiveBackend())

    async def call(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return await lab.handle(CFG["agent"]["account_key"], body, None)

    out = await Agent(box, call).chat("Hello", True)
    assert out["status"] == "done"
    ev = worker.events[0]
    assert ev.account_id == "acct-human-demo" and ev.backend == "live" and ev.tools_schema_id == "standard_agent"


async def test_http_api(box: Sandbox) -> None:
    agent = Agent(box, ScriptedLab(shell_call("echo hi > hi.txt"), FINAL))
    transport = httpx.ASGITransport(app=build_app(agent))
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
        assert (await client.get("/api/session")).json()["status"] == "idle"
        out = (await client.post("/api/chat", json={"message": "hi", "tools": True})).json()
        assert out["status"] == "awaiting_approval"
        out = (await client.post("/api/approve", json={"approval_id": out["pending"]["approval_id"],
                                                       "approve": True})).json()
        assert out["status"] == "done"
        files = (await client.get("/api/files")).json()["files"]
        assert {"path": "hi.txt", "size": 3} in files
        assert (await client.post("/api/session/new")).json() == {"ok": True}
        assert (await client.get("/api/session")).json()["messages"] == []
        assert (await client.get("/")).status_code == 200
