"""Unit tests for normalization: paths, line numbers, wrapper, tool mapping, truncation, scrub."""
from __future__ import annotations

import json
from pathlib import Path

from dw.common.config import CFG
from dw.common.schema import Message
from dw.data.normalize import (clean_text, clean_tool_output, extract_issue, finish_messages, map_tool,
                               normalize_paths, openswe_session, remove_line_numbers, repo_root, truncate)

FIX = Path(__file__).parent / "fixtures"


def test_path_normalization() -> None:
    assert normalize_paths("cd /testbed && ls /testbed/src") == "cd <REPO> && ls <REPO>/src"
    assert normalize_paths("/workspace/proj/a.py") == "<REPO>/a.py"
    assert normalize_paths("/home/<user>/workspace/x/a.py") == "/home/<user>/workspace/x/a.py"
    assert normalize_paths("/x/y/repo/a.py", root="/x/y/repo") == "<REPO>/a.py"
    assert normalize_paths("/private/tmp/claude-501/-Users-bob-w/t.out") == "<REPO>/t.out"
    assert "/home/<user>" in clean_text("open /Users/alice/code/x.py")


def test_repo_root() -> None:
    paths = ["/home/<user>/w/app/src/a.py", "/home/<user>/w/app/src/b.py", "/home/<user>/w/app/README.md"]
    assert repo_root(paths) == "/home/<user>/w/app"
    assert repo_root(["/home/<user>/a.txt", "/home/<user>/b.txt"]) is None
    assert repo_root([]) is None


def test_line_number_removal() -> None:
    assert remove_line_numbers("     1→import os\n     2→x = 1") == "import os\nx = 1"
    assert remove_line_numbers("1\tfoo\n2\tbar") == "foo\nbar"
    assert remove_line_numbers("result 1\nok") == "result 1\nok"


def test_wrapper_strip() -> None:
    raw = "We are addressing...\n--- BEGIN ISSUE ---\nThe bug text\n--- END ISSUE ---\nThe repository is at /testbed"
    assert extract_issue(raw) == "The bug text"
    assert extract_issue("plain") == "plain"


def test_tool_mapping() -> None:
    assert map_tool("Read", {"file_path": "/a"}) == ("read", {"path": "/a"})
    assert map_tool("Edit", {"file_path": "/a", "old_string": "x", "new_string": "y"}) == \
        ("edit", {"path": "/a", "old": "x", "new": "y"})
    assert map_tool("Bash", {"command": "ls"}) == ("shell", {"command": "ls"})
    assert map_tool("Grep", {"pattern": "foo"}) == ("search", {"pattern": "foo", "path": "."})
    assert map_tool("TodoWrite", {"todos": []}) is None
    assert map_tool("str_replace_editor", {"command": "view", "path": "/a/b.py"}) == ("read", {"path": "/a/b.py"})
    assert map_tool("str_replace_editor", {"command": "create", "path": "/a.py", "file_text": "z"}) == \
        ("write", {"path": "/a.py", "content": "z"})
    assert map_tool("bash", {"command": "ls"}) == ("shell", {"command": "ls"})
    assert map_tool("submit", {}) is None


def test_truncation_same_marker() -> None:
    n = CFG["normalize"]["max_tool_chars"]
    out = clean_tool_output("x" * (n + 500))
    assert out == "x" * n + CFG["normalize"]["truncation_marker"]
    assert truncate("short") == "short"


def test_tags_and_scrub() -> None:
    text = "hi <system-reminder>secret harness</system-reminder> mail a@b.com key sk-abcdefghijkl"
    out = clean_text(text)
    assert "system-reminder" not in out and "a@b.com" not in out and "sk-abcdefghijkl" not in out
    assert clean_text("OBSERVATION:\nfoo") == "foo"
    assert clean_text("\x1b[31mred\x1b[0m") == "red"


def test_max_steps() -> None:
    steps = CFG["normalize"]["max_steps"]
    msgs = [Message(role="user", text="go")]
    for _ in range(steps + 5):
        msgs += [Message(role="tool_call", tool="shell", args={"command": "ls"}),
                 Message(role="tool_result", tool="shell", text="a")]
    out, _ = finish_messages(msgs)
    assert sum(m.role == "tool_call" for m in out) == steps
    assert out[-1].role == "tool_result"


def test_openswe_fixture_session() -> None:
    row = json.loads((FIX / "openswe_claudecode_sample.jsonl").read_text().splitlines()[0])
    s = openswe_session(row, "openswe")
    assert s is not None and s.messages[0].role == "user"
    assert "BEGIN ISSUE" not in s.messages[0].text
    results = [m for m in s.messages if m.role == "tool_result"]
    assert results and all(m.tool in ("read", "write", "edit", "shell", "search", "other") for m in results)
