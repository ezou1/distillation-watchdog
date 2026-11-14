"""Scaffold denylist test (leakage control 1): no canonical record holds a strip-list string or a wrapper fragment."""
from __future__ import annotations

import datetime
import itertools
import json
from pathlib import Path
from typing import Iterable

import pytest

from dw.common.config import path
from dw.common.schema import Session
from dw.data.normalize import STRIP_STRINGS, WRAPPER_FRAGMENTS, openswe_session, read_jsonl, swechat_session

FIX = Path(__file__).parent / "fixtures"
FORBIDDEN = [*STRIP_STRINGS, *WRAPPER_FRAGMENTS, "<system-reminder>", "/testbed"]
MAX_PER_FILE = 3000


def _texts(s: Session) -> Iterable[str]:
    for m in s.messages:
        yield m.text
        for v in (m.args or {}).values():
            yield str(v)


def _assert_clean(sessions: Iterable[Session]) -> int:
    n = 0
    for s in sessions:
        n += 1
        for text in _texts(s):
            for bad in FORBIDDEN:
                assert bad not in text, f"{s.session_id}: found {bad!r}"
    return n


def test_fixture_openswe_clean() -> None:
    rows = [json.loads(line) for name in ("openswe_claudecode_sample.jsonl", "openswe_sweagent_sample.jsonl")
            for line in (FIX / name).read_text().splitlines()]
    sessions = [s for s in (openswe_session(r, "openswe") for r in rows) if s]
    assert _assert_clean(sessions) > 0


def test_fixture_swechat_clean() -> None:
    src = FIX / "private" / "swechat_sample.jsonl"
    if not src.exists():
        pytest.skip("private SWE-chat fixture missing")
    keep = ("user_prompt", "assistant_response", "tool_use", "tool_result")
    rows = [r for r in map(json.loads, src.read_text().splitlines()) if r["turn_type"] in keep]
    for r in rows:
        if isinstance(r["timestamp"], str):
            r["timestamp"] = datetime.datetime.fromisoformat(r["timestamp"])
    rows.sort(key=lambda r: (r["session_id"], r["turn_number"], r["content_block_index"]))
    sessions = [swechat_session(sid, list(g)) for sid, g in itertools.groupby(rows, key=lambda r: r["session_id"])]
    assert _assert_clean(s for s in sessions if s) > 0


@pytest.mark.parametrize("name", ["swechat", "openswe", "openswe_xharness"])
def test_canonical_files_clean(name: str) -> None:
    src = path("canonical") / f"{name}.jsonl"
    if not src.exists():
        pytest.skip(f"{src} missing")
    assert _assert_clean(itertools.islice(read_jsonl(src), MAX_PER_FILE)) > 0
