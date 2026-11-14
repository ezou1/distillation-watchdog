"""Tool schemas and conversion between canonical messages and the OpenAI chat format.

The simulator, the lab gateway, the replay backend, and the agent chat all use these functions.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from dw.common.schema import Message

# Tool name per canonical kind, for each schema ID. Argument names are the same in all schemas.
_SCHEMA_NAMES: dict[str, dict[str, str]] = {
    "standard_agent": {"other": "list_dir", "read": "read_file", "write": "write_file",
                       "edit": "edit_file", "shell": "run_shell"},
    "saas_custom": {"other": "fs_list", "read": "fs_read", "write": "fs_write",
                    "edit": "fs_patch", "shell": "exec"},
    "bench_harness": {"other": "ls", "read": "open_file", "write": "create_file",
                      "edit": "str_replace", "shell": "bash"},
}
_ARGS: dict[str, dict[str, str]] = {
    "other": {"path": "Directory path."},
    "read": {"path": "File path."},
    "write": {"path": "File path.", "content": "Full file content."},
    "edit": {"path": "File path.", "old": "Exact text to replace.", "new": "Replacement text."},
    "shell": {"command": "Shell command."},
}
_DESC = {"other": "List a directory.", "read": "Read a file.", "write": "Write a file.",
         "edit": "Replace text in a file.", "shell": "Run a shell command."}

NAME_TO_KIND: dict[str, str] = {
    name: kind for names in _SCHEMA_NAMES.values() for kind, name in names.items()
}


def tool_defs(schema_id: str) -> list[dict[str, Any]]:
    """Return the OpenAI `tools` list for a schema ID ([] for "none")."""
    names = _SCHEMA_NAMES.get(schema_id)
    if not names:
        return []
    defs = []
    for kind, name in names.items():
        props = {a: {"type": "string", "description": d} for a, d in _ARGS[kind].items()}
        defs.append({"type": "function", "function": {
            "name": name, "description": _DESC[kind],
            "parameters": {"type": "object", "properties": props, "required": list(props)},
        }})
    return defs


def schema_id_of(tools: list[dict[str, Any]] | None) -> str:
    """Identify the schema ID of a request `tools` field. Unknown schemas get a hash ID."""
    if not tools:
        return "none"
    names = sorted(t.get("function", {}).get("name", "") for t in tools)
    for sid, table in _SCHEMA_NAMES.items():
        if names == sorted(table.values()):
            return sid
    return "custom-" + hashlib.sha256(",".join(names).encode()).hexdigest()[:8]


def _canonical_call(kind: str, args: dict[str, Any] | None) -> tuple[str, dict[str, Any]] | None:
    """Map a canonical call to a kind the schemas support. Search becomes a grep shell call."""
    args = dict(args or {})
    if kind == "search":
        pattern = str(args.get("pattern", ""))
        where = str(args.get("path", "."))
        return "shell", {"command": f"grep -rn -- {json.dumps(pattern)} {where}"}
    if kind == "other":
        return ("other", {"path": str(args.get("path", "."))})
    if kind in _ARGS:
        return kind, {k: str(args.get(k, "")) for k in _ARGS[kind]}
    return None


def to_openai(messages: list[Message], schema_id: str) -> list[dict[str, Any]]:
    """Convert canonical messages to OpenAI chat messages for a schema."""
    names = _SCHEMA_NAMES.get(schema_id, _SCHEMA_NAMES["standard_agent"])
    out: list[dict[str, Any]] = []
    call_ids: list[str] = []      # open tool call IDs, answered in order
    counter = 0
    for msg in messages:
        if msg.role == "user":
            out.append({"role": "user", "content": msg.text})
        elif msg.role == "assistant":
            out.append({"role": "assistant", "content": msg.text})
        elif msg.role == "tool_call":
            mapped = _canonical_call(msg.tool or "other", msg.args)
            if mapped is None:
                continue
            kind, args = mapped
            counter += 1
            cid = f"call_{counter}"
            call = {"id": cid, "type": "function",
                    "function": {"name": names[kind], "arguments": json.dumps(args, ensure_ascii=False)}}
            if out and out[-1]["role"] == "assistant":
                out[-1].setdefault("tool_calls", []).append(call)
            else:
                out.append({"role": "assistant", "content": "", "tool_calls": [call]})
            call_ids.append(cid)
        elif msg.role == "tool_result":
            cid = call_ids.pop(0) if call_ids else f"call_orphan_{counter}"
            out.append({"role": "tool", "tool_call_id": cid, "content": msg.text})
    return out


def from_openai(oa_messages: list[dict[str, Any]]) -> list[Message]:
    """Convert OpenAI chat messages to canonical messages. System messages are dropped."""
    out: list[Message] = []
    kinds: dict[str, str] = {}
    for m in oa_messages:
        role = m.get("role")
        content = _text(m.get("content"))
        if role == "user":
            out.append(Message(role="user", text=content))
        elif role == "assistant":
            if content:
                out.append(Message(role="assistant", text=content))
            for call in m.get("tool_calls") or []:
                fn = call.get("function", {})
                kind = NAME_TO_KIND.get(fn.get("name", ""), "other")
                kinds[call.get("id", "")] = kind
                out.append(Message(role="tool_call", tool=kind, args=_parse_args(fn.get("arguments"))))
        elif role == "tool":
            kind = kinds.get(m.get("tool_call_id", ""), "other")
            out.append(Message(role="tool_result", text=content, tool=kind))
    return out


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
        return val if isinstance(val, dict) else {"value": val}
    except (TypeError, ValueError):
        return {"raw": str(raw)}


def split_steps(messages: list[Message]) -> list[tuple[int, list[Message]]]:
    """Split a session into assistant steps.

    Returns (start_index, step_messages). The request for a step is messages[:start_index].
    A step is a run of assistant prose and tool calls without a user turn or tool result inside.
    """
    steps: list[tuple[int, list[Message]]] = []
    current: list[Message] = []
    start = 0
    for i, msg in enumerate(messages):
        if msg.role in ("assistant", "tool_call"):
            if not current:
                start = i
            current.append(msg)
        elif current:
            steps.append((start, current))
            current = []
    if current:
        steps.append((start, current))
    return steps


def step_to_response(step: list[Message], schema_id: str, model: str) -> dict[str, Any]:
    """Build an OpenAI chat.completion response from one recorded assistant step."""
    oa = to_openai(step, schema_id)
    message: dict[str, Any] = {"role": "assistant", "content": ""}
    calls: list[dict[str, Any]] = []
    for m in oa:
        if m.get("content"):
            message["content"] += m["content"]
        calls.extend(m.get("tool_calls", []))
    if calls:
        message["tool_calls"] = calls
    return {
        "id": "chatcmpl-replay", "object": "chat.completion", "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message,
                     "finish_reason": "tool_calls" if calls else "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
