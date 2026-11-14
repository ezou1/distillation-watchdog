"""Tests for the shared contracts: chat conversion, prefix hash chain, detector view, scrubbing."""
from __future__ import annotations

import dataclasses

from dw.common.chat import from_openai, schema_id_of, split_steps, step_to_response, to_openai, tool_defs
from dw.common.hashing import prefix_chain
from dw.common.schema import DetectorView, Message, RequestEvent
from dw.common.scrub import scrub
from dw.watchdog.detectors.base import make_view

SESSION = [
    Message(role="user", text="Fix parse()"),
    Message(role="assistant", text="Let me look."),
    Message(role="tool_call", tool="read", args={"path": "<REPO>/a.py"}),
    Message(role="tool_call", tool="shell", args={"command": "ls"}),
    Message(role="tool_result", tool="read", text="def parse(): pass"),
    Message(role="tool_result", tool="shell", text="a.py"),
    Message(role="tool_call", tool="write", args={"path": "<REPO>/a.py", "content": "x = 1"}),
    Message(role="tool_result", tool="write", text="ok"),
    Message(role="assistant", text="Done."),
]


def test_openai_round_trip_all_schemas() -> None:
    for sid in ("standard_agent", "saas_custom", "bench_harness"):
        assert from_openai(to_openai(SESSION, sid)) == SESSION
        assert schema_id_of(tool_defs(sid)) == sid
    assert schema_id_of([]) == "none"


def test_split_steps_and_response() -> None:
    steps = split_steps(SESSION)
    assert [s for s, _ in steps] == [1, 6, 8]
    resp = step_to_response(steps[0][1], "standard_agent", "m")
    assert resp["choices"][0]["finish_reason"] == "tool_calls"
    assert len(resp["choices"][0]["message"]["tool_calls"]) == 2
    assert step_to_response(steps[2][1], "standard_agent", "m")["choices"][0]["finish_reason"] == "stop"


def test_prefix_chain_continuation() -> None:
    a = prefix_chain("standard_agent", SESSION[:4])
    b = prefix_chain("standard_agent", SESSION[:8])
    assert b[4] == a[-1] and len(b) == 9
    assert prefix_chain("saas_custom", SESSION[:4])[-1] != a[-1]


def test_detector_view_has_no_prose_or_private_fields() -> None:
    ev = RequestEvent(request_id="r", account_id="a", sim_time=0.0, messages=SESSION)
    view = make_view(ev, "s", 0, True)
    names = {f.name for f in dataclasses.fields(DetectorView)}
    assert not names & {"x_replay", "source", "label", "persona", "role_label"}
    assert all(m.text == "" for m in view.messages if m.role == "assistant")
    assert "Let me look." not in repr(view)


def test_scrub() -> None:
    s = scrub("mail bob@x.com key sk-abcdefghijkl ghp_abcdefghijk path /Users/bob/code and /home/alice/x")
    assert "bob@x.com" not in s and "sk-abc" not in s and "ghp_" not in s
    assert "/Users/bob" not in s and "/home/alice" not in s and "/home/<user>/code" in s
