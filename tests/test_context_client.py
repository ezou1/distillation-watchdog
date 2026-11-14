"""Tests for the context-model input format and the scoring client (mock HTTP, disk cache, service down)."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from dw.common import context
from dw.common.context import CTX, ContextScorer, head_tail, prompt_text, session_texts
from dw.common.schema import Message


def test_head_tail_keeps_start_and_end() -> None:
    long = "A" * 3000 + " please explain all reasoning"
    out = head_tail(long, 100)
    assert out.startswith("A" * 50) and out.endswith(long[-50:]) and "[...]" in out
    assert head_tail("short", 100) == "short"
    assert prompt_text(long).endswith("please explain all reasoning")


def test_session_texts() -> None:
    msgs = [Message(role="user", text="Fix it"), Message(role="tool_call", tool="shell", args={"command": "ls"}),
            Message(role="tool_result", tool="shell", text="a.py")]
    assert session_texts(msgs) == ["[prompt] Fix it", "[tool] shell: ls\na.py"]


def _mock(monkeypatch: pytest.MonkeyPatch, handler) -> list[int]:
    calls: list[int] = []
    real_client = httpx.Client

    def fake_client(*_a, **_k):
        def wrapped(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return handler(request)
        return real_client(transport=httpx.MockTransport(wrapped))
    monkeypatch.setattr(context.httpx, "Client", fake_client)
    return calls


def test_batches_cache_and_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    calls = _mock(monkeypatch, lambda r: httpx.Response(200, json={"probs": [0.9] * len(json.loads(r.content)["texts"])}))
    monkeypatch.setitem(CTX, "batch", 2)
    s = ContextScorer(url="http://x", cache_file=tmp_path / "c.jsonl")
    out = s.score_many(["a", "b", "c", "a"])
    assert out == {"a": 0.9, "b": 0.9, "c": 0.9} and len(calls) == 2          # 3 unique texts, batch 2
    s.score_many(["a"])
    assert len(calls) == 2                                                     # from the cache
    s.save()
    assert ContextScorer(url="http://x", cache_file=tmp_path / "c.jsonl").get("b") == 0.9


def test_service_down_returns_none_and_waits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock(monkeypatch, lambda r: httpx.Response(503))
    s = ContextScorer(url="http://x", cache_file=tmp_path / "c.jsonl")
    assert s.score_many(["a"]) == {"a": None}
    assert s.score_many(["a"]) == {"a": None} and len(calls) == 1             # no new call during retry_s


def test_cache_key_includes_model_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    k1 = context._key("x")
    monkeypatch.setitem(CTX, "model_tag", "other")
    assert context._key("x") != k1
