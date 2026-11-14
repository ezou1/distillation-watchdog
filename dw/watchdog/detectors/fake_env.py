"""D6 fake environment: tool outputs that a real machine cannot produce.

(a) A shadow file system replays each session (no execution) and finds contradictions.
(b) A realism classifier gives the probability that a tool output is LLM-written.
"""
from __future__ import annotations

import re
import shlex
from functools import lru_cache
from dataclasses import dataclass, field
from typing import Any

from dw.common.config import CFG
from dw.common.hashing import text_hash
from dw.common.schema import AccountState, DetectorView, Evidence, ViewMessage
from dw.watchdog.detectors.base import new_evidence
from dw.watchdog.detectors.common import DCFG, allow_evidence, fresh_messages, ratio, scratch

_MARKER = CFG["normalize"]["truncation_marker"]
_FAILED = re.compile(r"(?i)\b(error|no such file|not found|does not exist|failed to|cannot|permission denied)\b")
_SEGMENTS = re.compile(r"&&|\|\||;|\n|\|")
_REDIRECT = re.compile(r">>?\s*([^\s&|;>]+)")
_COLLECTED = re.compile(r"\bcollected (\d+) items?\b")
_SUMMARY_LINE = re.compile(r"\b\d+ (?:passed|failed|errors?|skipped)\b")
_COUNTS = re.compile(r"\b(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b")
_READ_ONLY = {"cat", "head", "tail", "ls", "grep", "rg", "find", "wc", "echo", "pwd", "cd", "nl", "diff",
              "stat", "file", "tree", "which", "printf", "true", "pytest", "sort", "uniq", "less", "du",
              "env", "date", "whoami", "test", "type"}
_GIT_READ_ONLY = {"status", "diff", "log", "show", "grep", "blame", "branch", "rev-parse", "ls-files"}
_SED_RANGE = re.compile(r"^\d+(,\d+)?p$")


def _norm(text: str) -> str:
    return " ".join(text.split()) if DCFG["d6"]["ws_normalize"] else text


def _split_marker(text: str) -> tuple[str, bool]:
    """Return (text without the truncation marker, True if the text was truncated)."""
    if text.endswith(_MARKER):
        return text[: -len(_MARKER)], True
    return text, False


def _snippet(text: str) -> str:
    n = DCFG["d6"]["snippet_chars"]
    one = " ".join(text.split())
    return one if len(one) <= n else one[: n - 3] + "..."


@dataclass
class Contradiction:
    """One contradiction found by the shadow file system."""

    message_index: int
    summary: str


@dataclass
class ShadowFS:
    """Replays the tool calls and results of one session. Contents are known only after write or edit."""

    files: dict[str, tuple[str, int]] = field(default_factory=dict)      # path -> (content, message index)
    must: dict[str, tuple[str, str, int]] = field(default_factory=dict)  # path -> (old, new, edit index)
    deleted: dict[str, int] = field(default_factory=dict)                # path -> message index of rm
    pending: list[ViewMessage] = field(default_factory=list)
    n_results: int = 0

    def step(self, msg: ViewMessage) -> list[Contradiction]:
        """Process one message in session order. Return the contradictions that it shows."""
        if msg.role == "tool_call":
            self.pending.append(msg)
            return []
        if msg.role != "tool_result":
            return []
        self.n_results += 1
        call = self._pop_call(msg.tool)
        if call is None:
            return []
        return self._apply(call, msg)

    def _pop_call(self, kind: str | None) -> ViewMessage | None:
        for i, call in enumerate(self.pending):
            if call.tool == kind:
                return self.pending.pop(i)
        return self.pending.pop(0) if self.pending else None

    def _apply(self, call: ViewMessage, res: ViewMessage) -> list[Contradiction]:
        args = call.args or {}
        path = str(args.get("path", "")).rstrip("/")
        failed = _failed(res.text)
        if call.tool == "write" and path and not failed:
            self._set(path, str(args.get("content", "")), call.index)
        elif call.tool == "edit" and path and not failed:
            self._edit(path, str(args.get("old", "")), str(args.get("new", "")), call.index)
        elif call.tool == "read" and path:      # canonical reads drop offset and limit: may be partial
            return self._check_view(path, res, "read", "substring")
        elif call.tool == "shell":
            return self._shell(str(args.get("command", "")), res, call.index)
        return []

    def _set(self, path: str, content: str, index: int) -> None:
        self.files[path] = (content, index)
        self.must.pop(path, None)
        self.deleted.pop(path, None)

    def _edit(self, path: str, old: str, new: str, index: int) -> None:
        known = self.files.get(path)
        self.deleted.pop(path, None)
        if known and old and known[0].count(old) == 1 and not known[0].endswith(_MARKER):
            self._set(path, known[0].replace(old, new, 1), index)
            return
        self.files.pop(path, None)
        if old.strip() and new.strip() and _norm(old) not in _norm(new):
            self.must[path] = (old, new, index)
        else:
            self.must.pop(path, None)

    def _shell(self, command: str, res: ViewMessage, call_index: int) -> list[Contradiction]:
        found = _pytest_check(res)
        segments = [s.strip() for s in _SEGMENTS.split(command) if s.strip()]
        redirects = [t for t in _REDIRECT.findall(command.replace("<REPO>", "REPO")) if t != "/dev/null"]
        view = _view_command(segments[0]) if len(segments) == 1 and not redirects else None
        if view is not None:
            path, how, mode = view
            found += self._check_view(path, res, how, mode)
        if redirects:
            self._forget()
        for seg in segments:
            self._shell_effect(seg, call_index)
        return found

    def _shell_effect(self, segment: str, index: int) -> None:
        """Mark files that `rm` deletes. Forget all contents after any command that can change files."""
        tokens = _tokens(segment)
        if not tokens:
            return
        prog, rest = tokens[0], tokens[1:]
        if prog == "rm":
            for t in rest:
                if t.startswith("<REPO>/"):
                    self.deleted[t.rstrip("/")] = index
                    self.files.pop(t.rstrip("/"), None)
                    self.must.pop(t.rstrip("/"), None)
        elif not _read_only(prog, rest):
            self._forget()

    def _forget(self) -> None:
        self.files.clear()
        self.must.clear()
        self.deleted.clear()

    def _deleted_at(self, path: str) -> int | None:
        for d, idx in self.deleted.items():
            if path == d or path.startswith(d + "/"):
                return idx
        return None

    def _check_view(self, path: str, res: ViewMessage, how: str, mode: str) -> list[Contradiction]:
        """Compare a file view (read, cat, head, sed -n) with the known content of the path."""
        text, truncated = _split_marker(res.text)
        if _failed(text) or not text.strip():
            return []
        d_idx = self._deleted_at(path)
        if d_idx is not None:
            return [Contradiction(res.index, f"Message {d_idx} deleted `{path}` with `rm`. Message {res.index} "
                                             f"`{how}` returned {len(text.splitlines())} lines of it.")]
        if path in self.files:
            content, w_idx = self.files[path]
            if not _agrees(content, text, truncated, mode):
                return [self._mismatch(path, content, w_idx, text, res.index, how)]
        elif path in self.must:
            old, new, e_idx = self.must[path]
            got = _norm(text)
            if _norm(old) in got and _norm(new) not in got:
                return [Contradiction(res.index, f"Message {e_idx} replaced `{_snippet(old)}` with "
                                                 f"`{_snippet(new)}` in `{path}`. Message {res.index} `{how}` "
                                                 f"returned the old text without the new text.")]
        return []

    def _mismatch(self, path: str, content: str, w_idx: int, text: str, r_idx: int, how: str) -> Contradiction:
        got = _norm(text)
        missing = next((ln for ln in content.splitlines() if ln.strip() and _norm(ln) not in got), None)
        if missing is not None:
            return Contradiction(r_idx, f"Message {w_idx} wrote `{_snippet(missing)}` to `{path}`. "
                                        f"Message {r_idx} `{how}` returned a file without this line.")
        return Contradiction(r_idx, f"Message {w_idx} wrote {len(content.splitlines())} lines to `{path}`. "
                                    f"Message {r_idx} `{how}` returned {len(text.splitlines())} different lines.")


def _failed(text: str) -> bool:
    return bool(_FAILED.search(text[:300]))


def _tokens(segment: str) -> list[str]:
    return list(_tokens_cached(segment))


@lru_cache(maxsize=100_000)
def _tokens_cached(segment: str) -> tuple[str, ...]:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    while tokens and (tokens[0] in ("timeout", "sudo", "time") or tokens[0].replace(".", "").isdigit()):
        tokens = tokens[1:]
    return tuple(tokens)


def _read_only(prog: str, rest: list[str]) -> bool:
    if prog in _READ_ONLY:
        return True
    if prog == "sed":
        return not any(t.startswith("-i") or t == "--in-place" for t in rest)
    if prog == "git":
        return bool(rest) and rest[0] in _GIT_READ_ONLY
    if prog in ("python", "python3"):
        return rest[:2] in (["-m", "pytest"], ["-m", "unittest"])
    return False


def _view_command(segment: str) -> tuple[str, str, str] | None:
    """Parse `cat P`, `head [-n N] P`, `tail ...`, or `sed -n 'A,Bp' P`. Return (path, name, mode)."""
    tokens = _tokens(segment)
    if len(tokens) < 2 or not tokens[-1].startswith("<REPO>/"):
        return None
    prog, path, flags = tokens[0], tokens[-1].rstrip("/"), tokens[1:-1]
    if prog == "cat" and not flags:
        return path, "cat", "full"
    if prog == "head" and all(f == "-n" or f.lstrip("-").isdigit() for f in flags):
        return path, "head", "prefix"
    if prog == "tail" and all(f == "-n" or f.lstrip("-").isdigit() for f in flags):
        return path, "tail", "substring"
    if prog == "sed" and len(flags) == 2 and flags[0] == "-n" and _SED_RANGE.match(flags[1]):
        return path, "sed -n", "substring"
    return None


def _agrees(known: str, got: str, got_truncated: bool, mode: str) -> bool:
    """Compare a file view with the known content after whitespace normalization."""
    known, known_truncated = _split_marker(known)
    a, b = _norm(known), _norm(got)
    if mode == "substring":
        return known_truncated or b in a
    if mode == "prefix" or got_truncated:
        return a.startswith(b) or (known_truncated and b.startswith(a))
    return a == b or (known_truncated and b.startswith(a))


def _pytest_check(res: ViewMessage) -> list[Contradiction]:
    """`collected K items` must equal the sum of the counts on the pytest summary line."""
    collected = _COLLECTED.findall(res.text)
    lines = [ln for ln in res.text.splitlines() if _SUMMARY_LINE.search(ln)]
    if len(collected) != 1 or not lines:
        return []
    k = int(collected[0])
    total = sum(int(n) for n, _ in _COUNTS.findall(lines[-1]))
    if total == k:
        return []
    return [Contradiction(res.index, f"Message {res.index} shows pytest `collected {k} items`, "
                                     f"but the summary line counts {total} tests.")]


class FakeEnvDetector:
    name = "d6_environment"

    def __init__(self, realism_model: Any | None = None) -> None:
        self.model = realism_model
        self._scores: dict[str, float] = {}

    def fake_prob(self, text: str) -> float:
        """Return the probability that a tool output is LLM-written. Cached by text hash."""
        key = text_hash(text)
        if key not in self._scores:
            if len(self._scores) > 200_000:
                self._scores.clear()
            self._scores[key] = float(self.model.predict_proba([text])[0, 1])  # type: ignore[union-attr]
        return self._scores[key]

    def observe(self, view: DetectorView, acct: AccountState) -> list[Evidence]:
        st = scratch(acct, self.name)
        if not st:
            st.update(sessions={}, n_contra=0, contra_sessions=set(), prob_sum=0.0, n_scored=0)
        sess = st["sessions"].setdefault(view.session_id, {"fs": ShadowFS(), "upto": 0})
        _, common = fresh_messages(acct, view)
        if common < sess["upto"]:                       # a new branch: replay its prefix silently
            sess["fs"] = ShadowFS()
            for m in view.messages[:common]:
                sess["fs"].step(m)
            sess["upto"] = common
        out: list[Evidence] = []
        for m in view.messages[sess["upto"]:]:
            for c in sess["fs"].step(m):
                out += self._record(st, view, c)
            if m.role == "tool_result" and self.model is not None and m.text.strip():
                st["prob_sum"] += self.fake_prob(m.text)
                st["n_scored"] += 1
        sess["upto"] = max(sess["upto"], len(view.messages))
        return out

    def _record(self, st: dict[str, Any], view: DetectorView, c: Contradiction) -> list[Evidence]:
        st["n_contra"] += 1
        st["contra_sessions"].add(view.session_id)
        if not allow_evidence(st, "contradiction"):
            return []
        return [new_evidence(self.name, view, summary=c.summary, message_index=c.message_index)]

    def features(self, acct: AccountState) -> dict[str, float]:
        st = acct.scratch.get(self.name) or {}
        sessions = st.get("sessions", {})
        n_results = sum(s["fs"].n_results for s in sessions.values())
        return {
            "d6_contradictions_per_100": 100.0 * ratio(st.get("n_contra", 0), n_results),
            "d6_contradiction_session_frac": ratio(len(st.get("contra_sessions", ())), len(sessions)),
            "d6_fake_prob_mean": ratio(st.get("prob_sum", 0.0), st.get("n_scored", 0)),
        }
