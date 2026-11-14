"""Synthetic asset tests: parsing helpers, call matching, and the contract format of the real files."""
from __future__ import annotations

import json

import pytest

from dw.common.config import path
from dw.common.schema import Message
from dw.data.normalize import read_jsonl
from dw.data.synth import call_index, fake_prompt, parse_items, parse_object


def test_parse_items() -> None:
    assert parse_items('["a b c", "d e f"]') == ["a b c", "d e f"]
    assert parse_items("1. first item\n- second item\n") == ["first item", "second item"]
    assert parse_object('x {"paraphrase": "p", "followups": ["f"]} y') == {"paraphrase": "p", "followups": ["f"]}


def test_call_index_and_prompt() -> None:
    msgs = [Message(role="user", text="fix it"),
            Message(role="tool_call", tool="read", args={"path": "a.py"}),
            Message(role="tool_call", tool="shell", args={"command": "ls"}),
            Message(role="tool_result", tool="read", text="REAL CONTENT"),
            Message(role="tool_result", tool="shell", text="a.py")]
    assert call_index(msgs) == {3: 1, 4: 2}
    prompt = fake_prompt(msgs, 2)
    assert "REAL CONTENT" not in prompt and "ls" in prompt


def test_synth_files_contract() -> None:
    synth = path("synth")
    if not (synth / "fake_tool_outputs.jsonl").exists():
        pytest.skip("synthetic assets missing")
    sessions = {s.session_id: s for sp in ("train", "val", "holdout")
                for s in read_jsonl(path("splits") / f"{sp}.jsonl") if s.source == "openswe"}
    for line in (synth / "fake_tool_outputs.jsonl").read_text().splitlines():
        row = json.loads(line)
        assert sessions[row["session_id"]].messages[row["message_index"]].role == "tool_result"
    for line in (synth / "stealth.jsonl").read_text().splitlines():
        row = json.loads(line)
        assert row["session_id"] in sessions and row["paraphrase"] and isinstance(row["followups"], list)
    for name in ("bank_a.json", "bank_b.json"):
        bank = json.loads((synth / name).read_text())
        assert bank["elicit"] and bank["benign"] and all("category" in e for e in bank["elicit"])
    templates = json.loads((synth / "templates.json").read_text())
    assert [t["id"] for t in templates] == [1, 2, 3, 4, 5, 6]
    assert all("{repo}" in t["text"] and "{issue}" in t["text"] and "{files}" in t["text"] for t in templates)
