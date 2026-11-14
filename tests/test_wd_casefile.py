"""Tests for the case-file validator (required) and the retry and fallback flow."""
from __future__ import annotations

import asyncio
from typing import Any

from dw.common.schema import Evidence
from dw.watchdog.casefile import citations, paragraphs, safe_subject, validate, write_casefile

KNOWN = {"ev-0000001", "ev-0000002", "fx-d1_dup_rate"}
GOOD = ("## Findings\nThe account sent the same context 8 times [ev-0000001].\n\n"
        "## Evidence\n- Duplicates at high temperature [ev-0000001, ev-0000002].\n"
        "- The duplicate rate adds to the score [fx-d1_dup_rate].\n\n"
        "## Recommended action\nBlock the account [ev-0000002].")


def test_valid_text() -> None:
    ok, cited, reason = validate(GOOD, KNOWN)
    assert ok, reason
    assert cited == ["ev-0000001", "ev-0000002", "fx-d1_dup_rate"]


def test_unknown_id_is_rejected() -> None:
    ok, _, reason = validate("The account is bad [ev-9999999].", KNOWN)
    assert not ok and "ev-9999999" in reason


def test_uncited_paragraph_is_rejected() -> None:
    ok, _, reason = validate("Duplicates [ev-0000001].\n\nThis paragraph has no citation.", KNOWN)
    assert not ok and "without citation" in reason


def test_uncited_list_item_is_rejected() -> None:
    ok, _, _ = validate("- cited [ev-0000001]\n- not cited", KNOWN)
    assert not ok


def test_empty_text_is_rejected() -> None:
    assert not validate("## Findings\n", KNOWN)[0]


def test_helpers() -> None:
    assert paragraphs("# H\nA [x-1]\n- b\n- c\n\nD") == ["A [x-1]", "- b", "- c", "D"]
    assert citations("a [ev-1, fx-a] b [see here] [ev-1]") == ["ev-1", "fx-a"]


class FakeClient:
    def __init__(self, texts: list[str] | None, fail: bool = False) -> None:
        self.texts = texts or []
        self.calls: list[list[dict[str, Any]]] = []
        self.fail = fail

    async def chat(self, messages: list[dict[str, Any]], **params: Any) -> dict[str, Any]:
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("offline")
        return {"choices": [{"message": {"content": self.texts.pop(0)}}]}


SECRET = "SWECHAT-SECRET-7c1e /home/<user>/proj/parser.py def parse(): pass"
EVIDENCE = [Evidence(id="ev-0000001", detector="d1", account_id="a", request_ids=["r1"],
                     summary="dups " + SECRET, weight=1.0),
            Evidence(id="ev-0000002", detector="d4", account_id="a", request_ids=["r2", "r3"],
                     summary="elicit", weight=0.5)]
SUBJECT = {"kind": "account", "pseudonym": "dev-3f2a", "score": 0.9, "state": "block", "requests": 12,
           "campaign": None, "declared_use": SECRET, "top_edge_types": ["shared_task", SECRET],
           "member_states": {"block": 3, SECRET: 1}}


def _run(client: FakeClient) -> dict[str, Any]:
    return asyncio.run(write_casefile("a", SUBJECT, EVIDENCE, {"d1_dup_rate": 2.0}, client))


def test_retry_then_ok() -> None:
    client = FakeClient(["No citations here.", GOOD])
    out = _run(client)
    assert out["status"] == "ok" and out["text"] == GOOD and len(client.calls) == 2
    assert "Rejected" in client.calls[1][-1]["content"]


def test_fallback_after_two_rejections_and_no_raw_text() -> None:
    client = FakeClient(["bad", "still bad [ev-123]"])
    out = _run(client)
    assert out["status"] == "fallback" and out["text"] == "" and out["cited"] == []
    assert {e["id"] for e in out["evidence"]} == KNOWN
    prompt = client.calls[0][1]["content"]
    assert "dups" not in prompt and "request_ids" not in prompt


def test_llm_prompt_has_no_free_text() -> None:
    client = FakeClient([GOOD])
    out = _run(client)
    prompt = "".join(m["content"] for m in client.calls[0])
    assert "SWECHAT-SECRET" not in prompt and "parser.py" not in prompt and "dev-3f2a" not in prompt
    for item_id in KNOWN:
        assert item_id in prompt
    assert '"d1"' in prompt and '"weight": 0.5' in prompt and '"requests": 2' in prompt
    assert "shared_task" in prompt and '"log_odds": 2.0' in prompt
    shown = {e["id"]: e["summary"] for e in out["evidence"]}
    assert SECRET in shown["ev-0000001"] and "+2.00" in shown["fx-d1_dup_rate"]
    assert out["status"] == "ok"


def test_safe_subject_keeps_numbers_and_labels() -> None:
    assert safe_subject(SUBJECT) == {"kind": "account", "score": 0.9, "state": "block", "requests": 12,
                                     "top_edge_types": ["shared_task"], "member_states": {"block": 3}}


def test_llm_error_gives_fallback() -> None:
    assert _run(FakeClient(None, fail=True))["status"] == "fallback"
