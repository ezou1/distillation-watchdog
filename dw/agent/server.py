"""Agent chat: a coding agent in the browser that is a normal customer of the lab gateway.

It sends the conversation and the standard agent tools to the lab with its own account key.
File tools run at once in the sandbox. Each shell command waits for Approve or Reject from the person.
Run `python -m dw.agent.server` to serve `static/agent.html` on the agent port.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from typing import Any, Awaitable, Callable

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from dw.agent.tools import Sandbox, SandboxError, parse_args
from dw.common.chat import tool_defs
from dw.common.config import CFG, ROOT, path
from dw.common.react_app import DEFAULT_DIST, mount_react_app
from dw.common.term import console

APP_DIST = DEFAULT_DIST  # the React build, served at /app

LabCall = Callable[[dict[str, Any]], Awaitable[tuple[int, dict[str, Any]]]]

SYSTEM_TOOLS = ("You are a coding agent. You work in a sandbox folder. Use relative paths. "
                "Use the tools to read, write, and edit files, and to run shell commands. "
                "Each shell command needs approval from the user. Give a short final answer when the work is done.")
SYSTEM_CHAT = "You are a helpful coding assistant. Answer clearly and briefly."
REJECTED = "The user rejected this command. It did not run."


class Agent:
    """One conversation with the agent loop, the tool queue, and the pending approval."""

    def __init__(self, sandbox: Sandbox, call_lab: LabCall) -> None:
        self.sandbox = sandbox
        self.call_lab = call_lab
        self.lock = asyncio.Lock()
        self.new_session()

    def new_session(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.queue: list[dict[str, Any]] = []
        self.pending: dict[str, str] | None = None
        self.status = "idle"
        self.error: str | None = None
        self.steps = 0
        self.tools_on = True

    async def chat(self, text: str, tools_on: bool) -> dict[str, Any]:
        """Add a user message and run the agent loop."""
        if self.pending:
            return self.view(error="Approve or reject the pending command first.")
        self.tools_on = tools_on
        self.messages.append({"role": "user", "content": text})
        self.steps = 0
        return await self._run()

    async def approve(self, approval_id: str, approve: bool) -> dict[str, Any]:
        """Run or reject the pending shell command, then continue the loop."""
        if not self.pending or self.pending["approval_id"] != approval_id:
            return self.view(error="No pending command has this approval ID.")
        call = self.queue.pop(0)
        if approve:
            try:
                output = await asyncio.to_thread(self.sandbox.run_shell, self.pending["command"])
            except Exception as exc:  # every tool call must get a tool result in the history
                output = f"Error: the command did not run: {exc}"
        else:
            output = REJECTED
        self.pending = None
        self._add_result(call, output)
        return await self._run()

    async def _run(self) -> dict[str, Any]:
        self.error = None
        while True:
            if self._run_queue():
                return self._finish("awaiting_approval")
            if self.steps >= CFG["agent"]["max_steps"]:
                return self._finish("step_limit")
            status, resp = await self._ask_lab()
            if status != 200:
                self.error = f"lab returned {status}: {json.dumps(resp)[:300]}"
                return self._finish("error")
            if not self._add_assistant(resp):
                return self._finish("done")

    def _run_queue(self) -> bool:
        """Run queued file tools. Return True when a shell command waits for approval."""
        while self.queue:
            call = self.queue[0]
            name = call["function"]["name"]
            if name != "run_shell":
                self.queue.pop(0)
                try:
                    output = self.sandbox.run_file_tool(name, call["function"].get("arguments"))
                except Exception as exc:  # every tool call must get a tool result in the history
                    output = f"Error: {exc}"
                self._add_result(call, output)
                continue
            try:
                command = str(parse_args(call["function"].get("arguments"))["command"])
            except (SandboxError, KeyError) as exc:
                self.queue.pop(0)
                self._add_result(call, f"Error: bad arguments for run_shell: {exc}")
                continue
            self.pending = {"approval_id": "ap-" + secrets.token_urlsafe(16), "command": command}
            return True
        return False

    async def _ask_lab(self) -> tuple[int, dict[str, Any]]:
        agent = CFG["agent"]
        system = SYSTEM_TOOLS if self.tools_on else SYSTEM_CHAT
        body: dict[str, Any] = {"model": CFG["models"]["lab_model"],
                                "messages": [{"role": "system", "content": system}, *self.messages],
                                "temperature": agent["temperature"], "max_tokens": agent["max_tokens"]}
        if self.tools_on:
            body["tools"] = tool_defs("standard_agent")
        status, resp = await self.call_lab(body)
        log_request(self.steps + 1, resp, status)
        return status, resp

    def _add_assistant(self, resp: dict[str, Any]) -> bool:
        """Add the model message. Return True if it has tool calls to run."""
        msg = (resp.get("choices") or [{}])[0].get("message") or {}
        assistant: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
        calls = [{"id": c.get("id") or f"call_{i}", "type": "function",
                  "function": {"name": c["function"]["name"], "arguments": c["function"].get("arguments") or "{}"}}
                 for i, c in enumerate(msg.get("tool_calls") or []) if self.tools_on]
        if calls:
            assistant["tool_calls"] = calls
        self.messages.append(assistant)
        self.steps += 1
        self.queue = list(calls)
        return bool(calls)

    def _add_result(self, call: dict[str, Any], output: str) -> None:
        self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})

    def _finish(self, status: str) -> dict[str, Any]:
        self.status = status
        return self.view()

    def view(self, error: str | None = None) -> dict[str, Any]:
        """The conversation in the API format of docs/API.md section 3."""
        out: dict[str, Any] = {"status": self.status, "steps": self.steps,
                               "messages": display_messages(self.messages), "pending": self.pending}
        if error or self.error:
            out["error"] = error or self.error
        return out


def display_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI messages to the UI format: parsed arguments and tool names on results."""
    names: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                names[c["id"]] = c["function"]["name"]
                try:
                    args = parse_args(c["function"]["arguments"])
                except SandboxError:
                    args = {"raw": c["function"]["arguments"]}
                calls.append({"id": c["id"], "name": c["function"]["name"], "arguments": args})
            out.append({"role": "assistant", "content": m["content"], "tool_calls": calls})
        elif m["role"] == "tool":
            out.append({**m, "name": names.get(m["tool_call_id"], "")})
        else:
            out.append(dict(m))
    return out


def log_request(step: int, resp: dict[str, Any], status: int) -> None:
    """Print one terminal line per lab request: time, step, tools used, status."""
    msg = (resp.get("choices") or [{}])[0].get("message") or {}
    tools = ",".join(c["function"]["name"] for c in msg.get("tool_calls") or []) or "-"
    console.print(f"{time.strftime('%H:%M:%S')}  step {step:>2}  tools {tools:<30}  status {status}")


def http_lab_call(client: httpx.AsyncClient, lab_url: str) -> LabCall:
    """Make a lab caller that uses the agent account key."""
    url = lab_url.rstrip("/") + "/v1/chat/completions"
    headers = {"Authorization": f"Bearer {CFG['agent']['account_key']}"}

    async def call(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            resp = await client.post(url, json=body, headers=headers)
            return resp.status_code, resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            return 0, {"error": f"lab not reachable: {exc}"}

    return call


def allowed_origins() -> set[str]:
    """The origins of the agent page itself. A POST from another origin is a cross-site request."""
    port = CFG["ports"]["agent"]
    return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


def post_guard(request: Request) -> JSONResponse | None:
    """Return an error response for a cross-site or non-JSON POST. Return None if the POST is good.

    A foreign Origin header gives 403. A Content-Type that is not application/json gives 415.
    A browser cannot send a cross-site application/json POST without a CORS preflight, and this app allows none.
    """
    origin = request.headers.get("origin")
    if origin is not None and origin not in allowed_origins():
        return JSONResponse({"error": "cross-origin request is not allowed"}, status_code=403)
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return JSONResponse({"error": "Content-Type must be application/json"}, status_code=415)
    return None


async def json_object(request: Request) -> dict[str, Any] | None:
    """The JSON object of the request body, or None if the body is not a JSON object."""
    try:
        data = await request.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def build_app(agent: Agent) -> FastAPI:
    app = FastAPI(title="agent chat")

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.method == "POST" and (error := post_guard(request)) is not None:
            return error
        return await call_next(request)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(ROOT / "static" / "agent.html")

    mount_react_app(app, APP_DIST)

    @app.post("/api/chat", response_model=None)
    async def chat(request: Request) -> dict | JSONResponse:
        data = await json_object(request)
        if data is None:
            return JSONResponse({"error": "the body must be a JSON object"}, status_code=400)
        async with agent.lock:
            return await agent.chat(str(data.get("message", "")), data.get("tools", True) is not False)

    @app.post("/api/approve", response_model=None)
    async def approve(request: Request) -> dict | JSONResponse:
        data = await json_object(request)
        if data is None:
            return JSONResponse({"error": "the body must be a JSON object"}, status_code=400)
        async with agent.lock:
            return await agent.approve(str(data.get("approval_id", "")), data.get("approve") is True)

    @app.get("/api/session")
    async def session() -> dict:
        return agent.view()

    @app.post("/api/session/new")
    async def new_session() -> dict:
        async with agent.lock:
            agent.new_session()
        return {"ok": True}

    @app.get("/api/files")
    async def files() -> dict:
        return {"files": agent.sandbox.files()}

    return app


def main() -> None:
    import uvicorn

    lab_url = os.environ.get("LAB_URL", f"http://127.0.0.1:{CFG['ports']['lab']}")
    client = httpx.AsyncClient(timeout=CFG["agent"]["lab_timeout_s"])
    agent = Agent(Sandbox(path("agent_workspace")), http_lab_call(client, lab_url))
    console.print(f"Agent chat: http://127.0.0.1:{CFG['ports']['agent']}  (lab {lab_url}, "
                  f"account {CFG['agent']['account_key']})")
    uvicorn.run(build_app(agent), host="127.0.0.1", port=CFG["ports"]["agent"],
                access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
