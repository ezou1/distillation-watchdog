"""Shared helpers for the detectors: per-session message tracking, evidence caps, paths, and repo identity.

All detectors use the same tracker. Thus each request is compared with its session only once.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from dw.common.config import CFG
from dw.common.hashing import prefix_chain
from dw.common.schema import AccountState, DetectorView, Message, ViewMessage

DCFG: dict[str, Any] = CFG["detectors"]
_REPO_PATH = re.compile(r"<REPO>/[\w.\-/]+")
_TRACKER = "_tracker"


def scratch(acct: AccountState, name: str) -> dict[str, Any]:
    """Return the private state of one detector for one account."""
    return acct.scratch.setdefault(name, {})


def chain_of(view: DetectorView) -> list[str]:
    """Return the prefix hash chain of the view. Compute it if the event has no valid chain."""
    if len(view.prefix_hashes) == len(view.messages) + 1:
        return view.prefix_hashes
    msgs = [Message(role=m.role, text=m.text, tool=m.tool, args=m.args) for m in view.messages]  # type: ignore[arg-type]
    return prefix_chain(view.tools_schema_id, msgs)


def fresh_messages(acct: AccountState, view: DetectorView) -> tuple[list[ViewMessage], int]:
    """Return (messages not seen before in this session branch, length of the common prefix).

    A common prefix shorter than the stored branch means that the request starts a new branch.
    The result is computed once per request and shared by all detectors.
    """
    state = acct.scratch.setdefault(_TRACKER, {"rid": None, "sessions": {}})
    if state["rid"] == view.request_id:
        return state["fresh"], state["common"]
    chain = chain_of(view)
    stored: list[str] = state["sessions"].get(view.session_id, [])
    common = _common_prefix(stored, chain)
    fresh: list[ViewMessage] = []
    if common < len(chain) - 1:          # the view has messages after the shared prefix
        state["sessions"][view.session_id] = list(chain)
        fresh = view.messages[common:]
    state.update(rid=view.request_id, fresh=fresh, common=common)
    return fresh, common


def _common_prefix(stored: list[str], chain: list[str]) -> int:
    """Return the number of messages that the stored chain and the new chain share."""
    if not stored:
        return 0
    n = min(len(stored), len(chain))
    if stored[n - 1] == chain[n - 1]:
        return n - 1
    lo, hi = 0, n - 1           # chains are prefix-closed: binary search for the last match
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if stored[mid] == chain[mid]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def allow_evidence(state: dict[str, Any], kind: str) -> bool:
    """Return True for the first N evidence items of a kind, then for every Mth item."""
    counts = state.setdefault("_ev_counts", Counter())
    counts[kind] += 1
    c = counts[kind]
    return c <= DCFG["evidence_first"] or c % DCFG["evidence_every"] == 0


def first_user_text(view: DetectorView) -> str:
    """Return the text of the first user message of the view, or an empty string."""
    for m in view.messages:
        if m.role == "user":
            return m.text
    return ""


def message_paths(msg: ViewMessage) -> set[str]:
    """Return the repository-relative paths that one tool call touches."""
    if msg.role != "tool_call" or not msg.args:
        return set()
    found: set[str] = set()
    for value in msg.args.values():
        if isinstance(value, str):
            found.update(p[len("<REPO>/"):].rstrip("/.") for p in _REPO_PATH.findall(value))
    return {p for p in found if p}


def repo_clusters(path_sets: list[set[str]], threshold: float) -> list[int]:
    """Group sessions into repositories by path-set Jaccard similarity (greedy single link).

    Sessions without paths get their own repository. Return one repository index per session.
    """
    labels: list[int] = []
    by_path: dict[str, list[int]] = {}
    n_repos = 0
    for i, paths in enumerate(path_sets):
        best, best_sim = -1, threshold
        for j in {j for p in paths for j in by_path.get(p, [])}:
            sim = len(paths & path_sets[j]) / len(paths | path_sets[j])
            if sim >= best_sim:
                best, best_sim = labels[j], sim
        if best < 0:
            best, n_repos = n_repos, n_repos + 1
        labels.append(best)
        for p in paths:
            by_path.setdefault(p, []).append(i)
    return labels


def norm_entropy(counts: list[int], base_n: int) -> float:
    """Return the Shannon entropy of the counts divided by log(base_n). Return 0.0 if base_n < 2."""
    total = sum(counts)
    if total == 0 or base_n < 2:
        return 0.0
    h = -sum(c / total * math.log(c / total) for c in counts if c)
    return h / math.log(base_n)


def ratio(a: float, b: float) -> float:
    """Return a / b, or 0.0 if b is 0."""
    return a / b if b else 0.0
