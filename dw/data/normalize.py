"""Convert raw SWE-chat and Open-SWE-Traces rows to canonical sessions and remove harness scaffold.

The module removes system prompts, injected tags, the Open-SWE task wrapper, and tool output decoration.
It normalizes paths, maps tool names to canonical kinds, truncates, and scrubs all text.
It also mines n-grams that occur often in one source and seldom in the other (section B6.3 step 9).
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from dw.common.chat import split_steps
from dw.common.config import CFG, path, salt
from dw.common.hashing import salted_id
from dw.common.schema import Message, Session
from dw.common.scrub import scrub

NCFG = CFG["normalize"]
REPO = "<REPO>"
ID_CHARS = 16

# Final strip list after the n-gram mining rounds (reports/scaffold_ngrams.txt).
# Each item is harness text, not task content. They are removed from user and tool text.
STRIP_STRINGS: list[str] = [
    "OBSERVATION:\n",
    "[Request interrupted by user for tool use]",
    "[Request interrupted by user]",
    " (file state is current in your context — no need to Read it back)",
    "Review the changes and make sure they are as expected. Edit the file again if necessary.",
    "Your command ran successfully and did not produce any output.",
    "(Bash completed with no output)",
]
# Success messages of write and edit tools differ by harness and version. They become one text.
EDIT_OK = "OK"
_EDIT_OK_RE = re.compile(
    r"^(?:The file \S+ has been (?:updated|edited|created)|File created successfully at:|"
    r"Successfully (?:wrote|edited|created|updated)|Applied \d+ edits? to)", re.I)
# Open-SWE task wrapper fragments. They must never appear in canonical records.
WRAPPER_FRAGMENTS: list[str] = [
    "We are addressing the following issue in our repository",
    "--- BEGIN ISSUE ---",
    "--- END ISSUE ---",
    "all your operations must be confined to this directory",
    "<test_and_patch_integrity>",
]
# Strings that must never appear in a canonical record (leakage control 1).
FORBIDDEN: list[str] = [*STRIP_STRINGS, *WRAPPER_FRAGMENTS, "<system-reminder>", "/testbed"]
_ISSUE_RE = re.compile(r"--- BEGIN ISSUE ---\s*(.*?)\s*--- END ISSUE ---", re.S)
# Tags whose whole block is harness text.
_DROP_BLOCK_TAGS = ("system-reminder", "command-message", "local-command-stdout",
                    "local-command-stderr", "local-command-caveat", "task-notification",
                    "user-prompt-submit-hook", "usage", "duration_ms", "tool_uses", "total_tokens",
                    "ide_opened_file", "ide_selection", "NOTE")
# Tags that wrap real content: keep the content, remove the tags.
_UNWRAP_TAGS = ("command-name", "command-args", "tool_use_error", "persisted-output", "bash-input", "bash-stdout",
                "bash-stderr", "response clipped")
_DROP_BLOCK_RE = re.compile(
    r"<(" + "|".join(re.escape(t) for t in _DROP_BLOCK_TAGS) + r")>.*?</\1>", re.S)
# After block removal, stray tags of both lists are removed too.
_UNWRAP_RE = re.compile(r"</?(" + "|".join(re.escape(t) for t in _UNWRAP_TAGS + _DROP_BLOCK_TAGS) + r")>")
# Built-in Claude Code slash commands. A user turn that is only such a command is dropped.
_BUILTIN_CMD_RE = re.compile(
    r"^/(clear|context|exit|model|compact|cost|help|resume|login|logout|status|config|mcp|doctor|"
    r"memory|permissions|agents|ide|hooks|plugin|usage|rewind|export|add-dir|init|statusline|"
    r"vim|theme|terminal-setup|release-notes|bashes|todos|output-style)\b")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_LINE_NO_RE = re.compile(r"^ *\d+(?:→|\t)", re.M)
_TRUNC_RES = [
    re.compile(r"\[\.\.\.\s*\d*\s*(?:more\s+)?(?:lines?|characters?|chars?)?\s*truncated\s*\.\.\.\]", re.I),
    re.compile(r"\.\.\.\s*\[\d+ (?:lines?|characters?) truncated\]\s*\.\.\.", re.I),
    re.compile(r"\[(?:Output|output) truncated[^\]]*\]"),
    re.compile(r"\(truncated\)"),
]
_PATH_RES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:/private)?/tmp/claude-\d+/[^\s/'\"`]+"), REPO),
    (re.compile(r"/var/folders/[^\s/]+/[^\s/]+/T"), REPO),
    (re.compile(r"(?<![\w.>-])/workspace/[^\s/'\"`]+"), REPO),
    (re.compile(r"/testbed"), REPO),
    (re.compile(r"-(?:Users|home)-[A-Za-z0-9_.]+-"), "-home-<user>-"),
]
_NOT_ROOTS = {"", "/home", "/home/<user>", "/root", "/tmp", "/private", "/private/tmp", "/Users"}
_SOURCE_DIRS = {"src", "lib", "tests", "test", "pkg", "cmd", "internal", "source", "packages",
                "components", "scripts", "docs"}
_SWEAGENT_VIEW_RE = re.compile(r"^Here's the result of running `cat -n` on [^\n]*:\n")
_SWEAGENT_DIR_RE = re.compile(r"^Here's the files and directories up to \d+ levels? deep in [^\n]*:\n")
# Sandbox artifacts: `ls -l` owner, group and date columns, and the Open-SWE branch and git user names.
_LS_LONG_RE = re.compile(
    r"^([dlcbps-][rwxsStT-]{9})[@+.]?\s+\d+\s+\S+\s+\S+\s+[\d,]+\s+"
    r"(?:[A-Z][a-z]{2}\s+\d{1,2}\s+(?:\d{1,2}:\d{2}|\d{4}))", re.M)
_SANDBOX_RES: list[tuple[re.Pattern[str], str]] = [
    (_LS_LONG_RE, r"\1 <n> <owner> <group> <size> <date>"),
    (re.compile(r"\bscaleswe-engine\b"), "<git-user>"),
    (re.compile(r"\bscaleswe\b"), "main"),
]
# Harness decoration of shell results (Claude Code).
_SHELL_DECOR_RES = [re.compile(r"^Exit code \d+\n?"), re.compile(r"\n?Shell cwd was reset to \S+")]

# ---------------------------------------------------------------- text cleaning


def strip_tags(text: str) -> str:
    """Remove harness-injected tag blocks and unwrap tags around real content."""
    text = _DROP_BLOCK_RE.sub("", text)
    return _UNWRAP_RE.sub("", text)


def normalize_paths(text: str, root: str | None = None) -> str:
    """Replace sandbox, workspace, temp and repository-root paths with <REPO>."""
    if root and len(root) > 1:
        text = text.replace(root.rstrip("/"), REPO)
    for pattern, repl in _PATH_RES:
        text = pattern.sub(repl, text)
    return text


def remove_line_numbers(text: str) -> str:
    """Remove `cat -n` style line-number prefixes when most lines have them."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        return text
    hits = sum(1 for ln in lines if _LINE_NO_RE.match(ln))
    if hits < 0.5 * len(lines):
        return text
    return _LINE_NO_RE.sub("", text)


def remove_truncation_markers(text: str) -> str:
    for pattern in _TRUNC_RES:
        text = pattern.sub("", text)
    return text


def truncate(text: str, max_chars: int | None = None) -> str:
    """Cut text to max_tool_chars and add the shared truncation marker."""
    limit = max_chars or NCFG["max_tool_chars"]
    return text if len(text) <= limit else text[:limit] + NCFG["truncation_marker"]


def clean_text(text: str, root: str | None = None) -> str:
    """Common cleaning for every text: scrub, paths, tags, ANSI codes, sandbox artifacts, the strip list."""
    text = scrub(text or "")
    text = normalize_paths(text, root)
    text = strip_tags(text)
    text = _ANSI_RE.sub("", text)
    for pattern, repl in _SANDBOX_RES:
        text = pattern.sub(repl, text)
    for item in STRIP_STRINGS + WRAPPER_FRAGMENTS:
        text = text.replace(item, "")
    return text.strip()


def clean_tool_output(text: str, root: str | None = None, kind: str | None = None) -> str:
    """Clean one tool output: common cleaning, decoration removal, then truncation.

    A success message of a write or edit tool becomes EDIT_OK.
    """
    text = clean_text(text, root)
    if kind in ("write", "edit") and _EDIT_OK_RE.match(text):
        return EDIT_OK
    text = _SWEAGENT_DIR_RE.sub("", _SWEAGENT_VIEW_RE.sub("", text))
    for pattern in _SHELL_DECOR_RES:
        text = pattern.sub("", text)
    text = remove_truncation_markers(remove_line_numbers(text))
    return truncate(text.strip())


def clean_args(args: dict[str, Any], root: str | None) -> dict[str, Any]:
    return {k: clean_text(str(v), root) if isinstance(v, str) else v for k, v in args.items()}


def extract_issue(text: str) -> str:
    """Return the issue text inside the Open-SWE wrapper. Return the text if there is no wrapper."""
    match = _ISSUE_RE.search(text or "")
    return match.group(1) if match else (text or "")

# ---------------------------------------------------------------- tool mapping


def _get(d: dict[str, Any], *keys: str) -> str:
    for k in keys:
        if d.get(k) is not None:
            return str(d[k])
    return ""


def map_claude_tool(name: str, inp: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Map a Claude Code tool call to (kind, canonical args). Return None to drop the call."""
    if name == "Read":
        return "read", {"path": _get(inp, "file_path", "path")}
    if name == "Write":
        return "write", {"path": _get(inp, "file_path", "path"), "content": _get(inp, "content")}
    if name == "Edit":
        return "edit", {"path": _get(inp, "file_path"), "old": _get(inp, "old_string"),
                        "new": _get(inp, "new_string")}
    if name == "MultiEdit":
        edits = inp.get("edits") or []
        return "edit", {"path": _get(inp, "file_path"),
                        "old": "\n".join(str(e.get("old_string", "")) for e in edits),
                        "new": "\n".join(str(e.get("new_string", "")) for e in edits)}
    if name == "NotebookEdit":
        return "edit", {"path": _get(inp, "notebook_path"), "old": "", "new": _get(inp, "new_source")}
    if name == "Bash":
        return "shell", {"command": _get(inp, "command")}
    if name in ("Grep", "Glob"):
        return "search", {"pattern": _get(inp, "pattern"), "path": _get(inp, "path") or "."}
    if name == "LS":
        return "other", {"path": _get(inp, "path")}
    return None


def map_sweagent_tool(name: str, inp: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Map a SWE-agent tool call to (kind, canonical args). Return None to drop the call."""
    if name == "bash":
        return "shell", {"command": _get(inp, "command")}
    if name != "str_replace_editor":
        return None
    cmd, p = inp.get("command"), _get(inp, "path")
    if cmd == "view":
        return ("read", {"path": p}) if "." in p.rsplit("/", 1)[-1] else ("other", {"path": p})
    if cmd == "create":
        return "write", {"path": p, "content": _get(inp, "file_text")}
    if cmd == "str_replace":
        return "edit", {"path": p, "old": _get(inp, "old_str"), "new": _get(inp, "new_str")}
    if cmd == "insert":
        return "edit", {"path": p, "old": "", "new": _get(inp, "new_str")}
    return None


def map_tool(name: str, inp: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Map a tool call from either harness."""
    if name in ("bash", "str_replace_editor", "submit"):
        return map_sweagent_tool(name, inp)
    return map_claude_tool(name, inp)


def _parse_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return val if isinstance(val, dict) else {}

# ---------------------------------------------------------------- session finishing


def finish_messages(msgs: list[Message], times: list[float] | None = None
                    ) -> tuple[list[Message], list[float] | None]:
    """Drop empty prose, empty user turns, and turns before the first user turn.

    Then keep the first max_steps assistant steps.
    """
    keep = [i for i, m in enumerate(msgs)
            if not (m.role in ("assistant", "user") and not m.text.strip())]
    first_user = next((j for j, i in enumerate(keep) if msgs[i].role == "user"), len(keep))
    keep = keep[first_user:]
    msgs = [msgs[i] for i in keep]
    times = [times[i] for i in keep] if times is not None else None
    steps = split_steps(msgs)
    if len(steps) > NCFG["max_steps"]:
        cut = steps[NCFG["max_steps"]][0]
        msgs = msgs[:cut]
        times = times[:cut] if times is not None else None
    return msgs, times


def _valid(msgs: list[Message]) -> bool:
    return bool(msgs) and msgs[0].role == "user" and any(m.role == "tool_call" for m in msgs)

# ---------------------------------------------------------------- Open-SWE


def openswe_session(row: dict[str, Any], source: str) -> Session | None:
    """Convert one Open-SWE-Traces row to a canonical session."""
    raw = row["messages"]
    oa = json.loads(raw) if isinstance(raw, str) else raw
    msgs: list[Message] = []
    pending: list[str | None] = []      # kinds of open tool calls, answered in order
    first_user = True
    for m in oa:
        role, content = m.get("role"), m.get("content") or ""
        if role == "user":
            text = extract_issue(content) if first_user else content
            first_user = False
            msgs.append(Message(role="user", text=clean_text(text)))
        elif role == "assistant":
            msgs.append(Message(role="assistant", text=clean_text(content)))
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                mapped = map_tool(fn.get("name", ""), _parse_json(fn.get("arguments")))
                pending.append(mapped[0] if mapped else None)
                if mapped:
                    msgs.append(Message(role="tool_call", tool=mapped[0], args=clean_args(mapped[1], None)))
        elif role == "tool":
            kind = pending.pop(0) if pending else None
            if kind:
                msgs.append(Message(role="tool_result", tool=kind, text=clean_tool_output(content, None, kind)))
    msgs, _ = finish_messages(msgs)
    if not _valid(msgs):
        return None
    repo = str(row.get("repo") or "")
    rkey = salted_id("repo:" + repo, salt())[:ID_CHARS]
    return Session(session_id=str(row.get("trajectory_id")), source=source, group_id=rkey,  # type: ignore[arg-type]
                   repo_key=rkey, task_key=row.get("instance_id"), messages=msgs,
                   resolved=row.get("resolved"))

# ---------------------------------------------------------------- SWE-chat


def _abs_paths(turns: list[dict[str, Any]]) -> list[str]:
    out = []
    for t in turns:
        if t["turn_type"] != "tool_use":
            continue
        inp = _parse_json(t.get("tool_input_json"))
        p = scrub(_get(inp, "file_path", "notebook_path", "path"))
        if p.startswith("/"):
            out.append(p)
    return out


def repo_root(paths: list[str]) -> str | None:
    """Return the deepest directory that holds at least normalize.repo_root_min_cover of the paths.

    Common source folders (src, lib, tests, ...) move the result up one level.
    Home folders and the file system root are not accepted as a repository root.
    """
    if not paths:
        return None
    counts: Counter[str] = Counter()
    for p in paths:
        parts = p.split("/")[1:-1]
        for i in range(1, len(parts) + 1):
            counts["/" + "/".join(parts[:i])] += 1
    good = [d for d, c in counts.items() if c >= NCFG["repo_root_min_cover"] * len(paths)
            and d not in _NOT_ROOTS]
    if not good:
        return None
    root = max(good, key=len)
    while root.rsplit("/", 1)[-1] in _SOURCE_DIRS and root.rsplit("/", 1)[0] not in _NOT_ROOTS:
        root = root.rsplit("/", 1)[0]
    return root


def _epoch(ts: Any) -> float:
    return ts.timestamp() if hasattr(ts, "timestamp") else float(ts)


def swechat_session(sid: str, turns: list[dict[str, Any]]) -> Session | None:
    """Convert the ordered SWE-chat turns of one session to a canonical session."""
    root = repo_root(_abs_paths(turns))
    kinds: dict[str, str | None] = {}
    msgs: list[Message] = []
    times: list[float] = []

    def add(msg: Message, ts: Any) -> None:
        msgs.append(msg)
        times.append(_epoch(ts) if ts is not None else (times[-1] if times else 0.0))

    for t in turns:
        tt, content = t["turn_type"], t.get("content") or ""
        if tt == "user_prompt":
            text = clean_text(content, root)
            add(Message(role="user", text="" if _BUILTIN_CMD_RE.match(text) else text), t["timestamp"])
        elif tt == "assistant_response":
            add(Message(role="assistant", text=clean_text(content, root)), t["timestamp"])
        elif tt == "tool_use":
            mapped = map_claude_tool(t.get("tool_name") or "", _parse_json(t.get("tool_input_json")))
            kinds[t.get("tool_call_id") or ""] = mapped[0] if mapped else None
            if mapped:
                add(Message(role="tool_call", tool=mapped[0], args=clean_args(mapped[1], root)), t["timestamp"])
        elif tt == "tool_result":
            kind = kinds.get(t.get("tool_call_id") or "")
            if kind:
                add(Message(role="tool_result", tool=kind, text=clean_tool_output(content, root, kind)),
                    t["timestamp"])
    msgs, real = finish_messages(msgs, times)
    if not _valid(msgs):
        return None
    first = turns[0]
    s = salt()
    return Session(session_id="sc-" + salted_id("session:" + sid, s)[:ID_CHARS], source="swechat",
                   group_id=salted_id(str(first.get("user_id")), s)[:ID_CHARS],
                   repo_key=salted_id("repo:" + str(first.get("repo_id")), s)[:ID_CHARS],
                   task_key=None, messages=msgs, resolved=None, real_times=real)

# ---------------------------------------------------------------- n-gram mining


def session_words(s: Session) -> list[str]:
    """Words of the user text, tool text, and tool arguments of a session (no assistant prose)."""
    parts: list[str] = []
    for m in s.messages:
        if m.role in ("user", "tool_result"):
            parts.append(m.text)
        elif m.role == "tool_call" and m.args:
            parts.extend(str(v) for v in m.args.values())
    return " ".join(parts).split()


def _grams(s: Session, sizes: tuple[int, ...] = (3, 5)) -> set[str]:
    words = session_words(s)
    return {" ".join(words[i:i + n]) for n in sizes for i in range(len(words) - n + 1)}


def _doc_freq(sessions: list[Session], only: set[str] | None = None) -> Counter[str]:
    df: Counter[str] = Counter()
    for s in sessions:
        grams = _grams(s)
        df.update(grams & only if only is not None else grams)
    return df


def _candidates(sessions: list[Session]) -> set[str]:
    """N-grams that are frequent in a small first sample (pass 1 keeps the memory use low)."""
    sample = sessions[: NCFG["ngram_pass1"]]
    floor = 0.8 * NCFG["ngram_source_min_frac"] * len(sample)
    return {g for g, c in _doc_freq(sample).items() if c >= floor}


def mine_ngrams(a: list[Session], b: list[Session]) -> list[tuple[str, str, float, float]]:
    """Return (source, ngram, frac_in_source, frac_in_other) for differential n-grams of both sources."""
    if not a or not b:
        return []
    cand = _candidates(a) | _candidates(b)
    da, db = _doc_freq(a, cand), _doc_freq(b, cand)
    hi, lo = NCFG["ngram_source_min_frac"], NCFG["ngram_other_max_frac"]
    out: list[tuple[str, str, float, float]] = []
    for name, mine, other, n_mine, n_other in (("A", da, db, len(a), len(b)), ("B", db, da, len(b), len(a))):
        for gram, c in mine.items():
            fa, fb = c / n_mine, other.get(gram, 0) / n_other
            if fa > hi and fb < lo:
                out.append((name, gram, fa, fb))
    out.sort(key=lambda r: -r[2])
    return out[: NCFG["ngram_top"]]


def write_ngram_report(rows: list[tuple[str, str, float, float]], names: tuple[str, str],
                       out: Path, round_no: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Differential n-grams, round {round_no}. A={names[0]} B={names[1]}.",
             "# source\tfrac_in_source\tfrac_in_other\tngram"]
    lines += [f"{names[0] if s == 'A' else names[1]}\t{fa:.3f}\t{fb:.3f}\t{g}" for s, g, fa, fb in rows]
    lines.append("# Final strip list (dw.data.normalize.STRIP_STRINGS):")
    lines += [f"#   {json.dumps(x)}" for x in STRIP_STRINGS]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

# ---------------------------------------------------------------- driver


def is_clean(line: str) -> bool:
    """True if a serialized session contains no forbidden scaffold string (leakage control 1)."""
    return not any(bad in line or json.dumps(bad)[1:-1] in line for bad in FORBIDDEN)


def write_jsonl(sessions: Iterable[Session], out: Path) -> int:
    """Write sessions as JSON lines. Drop a session that still holds a forbidden string."""
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(out, "w", encoding="utf-8") as f:
        for s in sessions:
            line = s.model_dump_json()
            if not is_clean(line):
                continue
            f.write(line + "\n")
            n += 1
    return n


def read_jsonl(src: Path) -> Iterator[Session]:
    with open(src, encoding="utf-8") as f:
        for line in f:
            yield Session.model_validate_json(line)


def stride_sample(src: Path, n: int) -> list[Session]:
    """Read about n sessions spread evenly over a JSONL file."""
    with open(src, encoding="utf-8") as f:
        total = sum(1 for _ in f)
    step = max(1, total // n)
    with open(src, encoding="utf-8") as f:
        return [Session.model_validate_json(line) for i, line in enumerate(f) if i % step == 0][:n]


def _openswe_iter(src: Path, source: str) -> Iterator[Session]:
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(src)
    for batch in pf.iter_batches(batch_size=200):
        for row in batch.to_pylist():
            s = openswe_session(row, source)
            if s:
                yield s


def _swechat_iter(max_sessions: int | None) -> Iterator[Session]:
    from dw.data.fetch import swechat_turns
    for sid, turns in swechat_turns(max_sessions):
        s = swechat_session(sid, turns)
        if s:
            yield s


def build(which: list[str]) -> None:
    """Write data/canonical/<source>.jsonl for the selected sources."""
    canon = path("canonical")
    jobs: dict[str, Callable[[], Iterator[Session]]] = {
        "openswe": lambda: _openswe_iter(path("raw") / "openswe.parquet", "openswe"),
        "openswe_xharness": lambda: _openswe_iter(path("raw") / "openswe_xharness.parquet", "openswe_xharness"),
        "swechat": lambda: _swechat_iter(NCFG.get("swechat_max_sessions")),
    }
    for name in which:
        n = write_jsonl(jobs[name](), canon / f"{name}.jsonl")
        print(f"{name}: {n} sessions", flush=True)


def mine(round_no: int, sample: int | None = None) -> None:
    """Mine SWE-chat against Open-SWE and Open-SWE against the cross-harness slice."""
    canon = path("canonical")
    sample = sample or NCFG["ngram_sample"]
    load = {n: stride_sample(canon / f"{n}.jsonl", sample) for n in ("swechat", "openswe", "openswe_xharness")}
    rows = mine_ngrams(load["swechat"], load["openswe"])
    xrows = mine_ngrams(load["openswe"], load["openswe_xharness"])
    out = path("reports") / "scaffold_ngrams.txt"
    write_ngram_report(rows, ("swechat", "openswe"), out, round_no)
    with open(out, "a", encoding="utf-8") as f:
        f.write("# Cross-harness: A=openswe B=openswe_xharness\n")
        for s, g, fa, fb in xrows:
            f.write(f"{'openswe' if s == 'A' else 'openswe_xharness'}\t{fa:.3f}\t{fb:.3f}\t{g}\n")
    print(f"round {round_no}: {len(rows)} + {len(xrows)} n-grams -> {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--only", nargs="*", default=["openswe", "openswe_xharness", "swechat"])
    m = sub.add_parser("mine")
    m.add_argument("--round", type=int, default=1)
    args = parser.parse_args()
    if args.cmd == "build":
        build(args.only)
    else:
        mine(args.round)


if __name__ == "__main__":
    main()
