"""Model backends of the lab gateway.

The replay backend returns the recorded assistant step of a simulated request and calls no model.
The live backend sends the request to the lab model on OpenRouter. If DW_LIVE_FIXTURE names a JSON file,
the live backend returns that recorded response and does not call OpenRouter.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from dw.common.chat import step_to_response
from dw.common.config import CFG
from dw.common.llm import LLMClient
from dw.common.schema import Message

# Request fields that the live backend sends to the model unchanged.
PASS_THROUGH = ("tools", "tool_choice", "temperature", "max_tokens", "n", "reasoning_effort", "user")


class ChatClient(Protocol):
    async def chat(self, messages: list[dict[str, Any]], *, cache: bool = True, **params: Any) -> dict[str, Any]: ...


class ReplayBackend:
    """Return the recorded step from `x_replay` as an OpenAI response."""

    def respond(self, x_replay: dict[str, Any], schema_id: str, model: str) -> dict[str, Any]:
        step = [Message.model_validate(m) for m in x_replay.get("step", [])]
        return step_to_response(step, schema_id, model)


class LiveBackend:
    """Call the lab model on OpenRouter through the shared LLM client (no cache)."""

    def __init__(self, client: ChatClient | None = None) -> None:
        self.model: str = CFG["models"]["lab_model"]
        self._client = client
        fixture = os.environ.get("DW_LIVE_FIXTURE")
        self.fixture: Path | None = Path(fixture) if fixture else None

    @property
    def client(self) -> ChatClient:
        if self._client is None:
            self._client = LLMClient(model=self.model)
        return self._client

    async def respond(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.fixture is not None:
            return json.loads(self.fixture.read_text(encoding="utf-8"))
        params = {k: body[k] for k in PASS_THROUGH if body.get(k) is not None}
        return await self.client.chat(body.get("messages", []), cache=False, model=self.model, **params)
