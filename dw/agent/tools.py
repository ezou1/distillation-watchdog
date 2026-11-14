"""Sandboxed tools of the agent chat: list_dir, read_file, write_file, edit_file, run_shell.

All paths resolve inside the sandbox folder. Symlinks and `..` are resolved before the check.
The server calls `run_shell` only after the person approves the command. The shell gets no keys.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Any, Callable

from dw.common.config import CFG

SAFE_PATH = "/usr/local/bin:/usr/bin:/bin"


class SandboxError(ValueError):
    """A path is outside the sandbox, or the tool arguments are wrong."""


def truncate(text: str) -> str:
    limit = CFG["agent"]["output_max_chars"]
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... truncated, {len(text) - limit} more characters ...]"


def shell_env(root: Path) -> dict[str, str]:
    """A minimal environment for shell commands. It has no variable from `.env` and no key."""
    return {"PATH": SAFE_PATH, "HOME": str(root), "LANG": "C.UTF-8"}


class Sandbox:
    """File and shell tools that work only inside one folder."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()

    def resolve(self, rel: str) -> Path:
        """Return the real path for `rel`. Raise SandboxError if it is outside the sandbox."""
        full = (self.root / (rel or ".")).resolve()
        if full != self.root and self.root not in full.parents:
            raise SandboxError(f"path is outside the sandbox: {rel}")
        return full

    def list_dir(self, path: str = ".") -> str:
        folder = self.resolve(path)
        if not folder.is_dir():
            raise SandboxError(f"not a directory: {path}")
        names = sorted(p.name + ("/" if p.is_dir() else "") for p in folder.iterdir())
        return "\n".join(names) or "(empty)"

    def read_file(self, path: str) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise SandboxError(f"not a file: {path}")
        return truncate(target.read_text(encoding="utf-8", errors="replace"))

    def write_file(self, path: str, content: str) -> str:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content.encode('utf-8'))} bytes to {path}"

    def edit_file(self, path: str, old: str, new: str) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise SandboxError(f"not a file: {path}")
        text = target.read_text(encoding="utf-8")
        count = text.count(old) if old else 0
        if count != 1:
            raise SandboxError(f"the old text must occur exactly one time in {path} (found {count})")
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"Edited {path}"

    def run_shell(self, command: str) -> str:
        """Run an approved command in the sandbox with a timeout and an environment without keys."""
        proc = subprocess.Popen(["bash", "-c", command], cwd=self.root, env=shell_env(self.root),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True, text=True, errors="replace")
        try:
            out, _ = proc.communicate(timeout=CFG["agent"]["shell_timeout_s"])
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, _ = proc.communicate()
            return truncate(f"timeout after {CFG['agent']['shell_timeout_s']} s\n{out}")
        return truncate(f"exit code {proc.returncode}\n{out}")

    def files(self) -> list[dict[str, Any]]:
        """All files in the sandbox, as relative paths with sizes."""
        out: list[dict[str, Any]] = []
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and self.root in p.resolve().parents:
                out.append({"path": p.relative_to(self.root).as_posix(), "size": p.stat().st_size})
            if len(out) >= CFG["agent"]["max_files_listed"]:
                break
        return out

    def run_file_tool(self, name: str, raw_args: str | dict[str, Any] | None) -> str:
        """Run one file tool call of the model. Errors become text for the model."""
        tools: dict[str, Callable[..., str]] = {"list_dir": self.list_dir, "read_file": self.read_file,
                                                "write_file": self.write_file, "edit_file": self.edit_file}
        if name not in tools:
            return f"Error: unknown tool {name}"
        try:
            args = parse_args(raw_args)
            return tools[name](**args)
        except (SandboxError, TypeError, OSError, UnicodeDecodeError) as exc:
            return f"Error: {exc}"


def parse_args(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    """Parse the JSON arguments of a tool call."""
    if isinstance(raw, dict):
        return raw
    try:
        args = json.loads(raw or "{}")
    except ValueError as exc:
        raise SandboxError(f"arguments are not valid JSON: {exc}") from exc
    if not isinstance(args, dict):
        raise SandboxError("arguments must be a JSON object")
    return args
