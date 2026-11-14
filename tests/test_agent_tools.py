"""Tests for the agent sandbox: path escapes, file tools, and the shell environment without keys."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from dw.agent.tools import Sandbox, SandboxError
from dw.common.config import ROOT


@pytest.fixture()
def box(tmp_path: Path) -> Sandbox:
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("top secret")
    return Sandbox(tmp_path / "ws")


@pytest.mark.parametrize("bad", ["../outside/secret.txt", "a/../../outside/secret.txt", "..", "/etc/passwd"])
def test_path_escape_is_rejected(box: Sandbox, bad: str) -> None:
    with pytest.raises(SandboxError):
        box.resolve(bad)
    assert box.run_file_tool("read_file", {"path": bad}).startswith("Error:")
    assert box.run_file_tool("write_file", {"path": bad, "content": "x"}).startswith("Error:")


def test_symlink_escape_is_rejected(box: Sandbox) -> None:
    outside = box.root.parent / "outside"
    os.symlink(outside / "secret.txt", box.root / "link.txt")
    os.symlink(outside, box.root / "linkdir")
    for rel in ("link.txt", "linkdir/secret.txt", "linkdir/new.txt"):
        with pytest.raises(SandboxError):
            box.resolve(rel)
    assert "top secret" not in box.run_file_tool("read_file", '{"path": "link.txt"}')
    box.run_file_tool("write_file", {"path": "linkdir/new.txt", "content": "x"})
    assert not (outside / "new.txt").exists()
    assert all("secret" not in f["path"] for f in box.files())


def test_nul_byte_path_is_rejected(box: Sandbox) -> None:
    with pytest.raises(SandboxError, match="NUL"):
        box.resolve("a\x00b.txt")
    calls = {"read_file": {}, "list_dir": {}, "write_file": {"content": "x"}, "edit_file": {"old": "x", "new": "y"}}
    for name, extra in calls.items():
        out = box.run_file_tool(name, {"path": "a\x00b.txt", **extra})
        assert out.startswith("Error:") and "NUL" in out


def test_absolute_path_inside_is_allowed(box: Sandbox) -> None:
    assert box.resolve(str(box.root / "a.txt")) == box.root / "a.txt"


def test_file_tools(box: Sandbox) -> None:
    assert box.run_file_tool("write_file", {"path": "src/a.py", "content": "x = 1\n"}) == "Wrote 6 bytes to src/a.py"
    assert box.run_file_tool("edit_file", {"path": "src/a.py", "old": "x = 1", "new": "x = 2"}) == "Edited src/a.py"
    assert box.run_file_tool("read_file", {"path": "src/a.py"}) == "x = 2\n"
    assert box.run_file_tool("list_dir", {"path": "."}) == "src/"
    assert "exactly one time" in box.run_file_tool("edit_file", {"path": "src/a.py", "old": "zzz", "new": ""})
    assert box.run_file_tool("read_file", "not json").startswith("Error:")
    assert box.run_file_tool("run_shell", {"command": "ls"}).startswith("Error: unknown tool")
    assert box.files() == [{"path": "src/a.py", "size": 6}]


def test_shell_environment_has_no_keys(box: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-value-123")
    monkeypatch.setenv("HF_TOKEN", "hf_testvalue123")
    out = box.run_shell("env; pwd")
    assert out.startswith("exit code 0")
    assert "sk-or-test-value-123" not in out and "hf_testvalue123" not in out
    names = {line.split("=", 1)[0] for line in out.splitlines()[1:] if "=" in line}
    env_file_names = set(dotenv_values(ROOT / ".env")) if (ROOT / ".env").exists() else set()
    assert not names & (env_file_names | {"OPENROUTER_API_KEY", "HF_TOKEN"})
    assert str(box.root) in out


def test_shell_timeout_and_truncation(box: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    from dw.common.config import CFG
    monkeypatch.setitem(CFG["agent"], "shell_timeout_s", 1)
    assert box.run_shell("sleep 5").startswith("timeout after 1 s")
    monkeypatch.setitem(CFG["agent"], "output_max_chars", 100)
    assert "truncated" in box.run_shell("yes | head -c 1000")
