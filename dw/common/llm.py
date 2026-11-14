"""One OpenRouter client for all LLM calls: retry, concurrency limit, disk cache, and cost log.

Tests must not call OpenRouter. Set DW_LLM_OFFLINE=1 to make cache misses raise an error.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

import httpx

from dw.common.config import CFG, ROOT

log = logging.getLogger("dw.llm")


class FatalLLMError(RuntimeError):
    """OpenRouter refused the key (401) or the credit is used up (402). The build must stop."""


class LLMOfflineError(RuntimeError):
    """A cache miss happened while DW_LLM_OFFLINE=1."""


_cost_lock = threading.Lock()


def _log_cost(model: str, usage: dict[str, Any]) -> None:
    path = ROOT / CFG["llm"]["cost_log"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with _cost_lock:
        data = json.loads(path.read_text()) if path.exists() else {"models": {}}
        row = data["models"].setdefault(model, {"calls": 0, "prompt_tokens": 0,
                                                "completion_tokens": 0, "cost_usd": 0.0})
        row["calls"] += 1
        row["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        row["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        row["cost_usd"] += float(usage.get("cost") or 0.0)
        data["total_cost_usd"] = round(sum(r["cost_usd"] for r in data["models"].values()), 6)
        path.write_text(json.dumps(data, indent=2))


class LLMClient:
    """Async chat client for one model. Results are cached by a hash of the request."""

    def __init__(self, model: str | None = None, max_parallel: int | None = None) -> None:
        self.model = model or CFG["models"]["helper_model"]
        self.sem = asyncio.Semaphore(max_parallel or CFG["llm"]["max_parallel"])
        self.cache_dir = ROOT / CFG["llm"]["cache_dir"]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.deny_collection = bool(CFG["llm"]["data_collection_deny"])

    def _cache_path(self, body: dict[str, Any]) -> Path:
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        return self.cache_dir / f"{digest}.json"

    async def chat(self, messages: list[dict[str, Any]], *, cache: bool = True, **params: Any) -> dict[str, Any]:
        """Send one chat completion. Return the full OpenAI-format response."""
        body: dict[str, Any] = {"model": params.pop("model", self.model), "messages": messages, **params}
        path = self._cache_path(body)
        if cache and path.exists():
            return json.loads(path.read_text())
        if os.environ.get("DW_LLM_OFFLINE") == "1":
            raise LLMOfflineError("LLM cache miss in offline mode")
        async with self.sem:
            result = await self._post_with_retry(body)
        _log_cost(body["model"], result.get("usage") or {})
        if cache:
            path.write_text(json.dumps(result))
        return result

    async def text(self, prompt: str, **params: Any) -> str:
        """Send one user prompt. Return the assistant text."""
        resp = await self.chat([{"role": "user", "content": prompt}], **params)
        return resp["choices"][0]["message"].get("content") or ""

    async def _post_with_retry(self, body: dict[str, Any]) -> dict[str, Any]:
        send = dict(body, usage={"include": True})
        if self.deny_collection:
            send["provider"] = {"data_collection": "deny"}
        headers = {"Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY', '')}"}
        url = CFG["llm"]["base_url"] + "/chat/completions"
        last: Exception | None = None
        async with httpx.AsyncClient(timeout=CFG["llm"]["timeout_s"]) as client:
            for attempt in range(CFG["llm"]["max_retries"] + 1):
                try:
                    resp = await client.post(url, json=send, headers=headers)
                except httpx.HTTPError as exc:
                    last = exc
                    await asyncio.sleep(2 ** attempt)
                    continue
                if resp.status_code in (401, 402):
                    raise FatalLLMError(
                        f"STOP: OpenRouter returned {resp.status_code}. Check the key or add credit at "
                        "https://openrouter.ai/settings/credits")
                if resp.status_code == 400 and "provider" in send and "provider" in resp.text.lower():
                    log.warning("OpenRouter rejected provider.data_collection; sending without it")
                    send.pop("provider")
                    self.deny_collection = False
                    continue
                if resp.status_code == 200:
                    data = resp.json()
                    if "choices" in data:
                        return data
                    last = RuntimeError(f"no choices: {str(data)[:300]}")
                else:
                    last = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError(f"LLM call failed: {last}")
