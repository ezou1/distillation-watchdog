"""D5 templates: first prompts that come from a task generator share masked lines (skeletons).

This module also gives the skeletons and the first-message hashes of an account to the campaign graph,
and a local template inversion for flagged accounts.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from dw.common.hashing import text_hash
from dw.common.schema import AccountState, DetectorView, Evidence
from dw.watchdog.detectors.base import new_evidence
from dw.watchdog.detectors.common import DCFG, allow_evidence, first_user_text, ratio, scratch

NAME = "d5_templates"
_MASKS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\"[^\"\n]*\"|'[^'\n]*'|`[^`\n]*`"), " <STR> "),
    (re.compile(r"(?:<REPO>)?[\w.\-]*/[\w.\-/]+|\b[\w\-]+\.[A-Za-z]{1,4}\b"), " <PATH> "),
    (re.compile(r"\b\d+(?:\.\d+)*\b"), " <NUM> "),
    (re.compile(r"\b(?=[A-Za-z_]*[_\d])\w+\b|\b[a-z]+[A-Z]\w*\b"), " <ID> "),
]
_SLOT = re.compile(r"<(STR|PATH|NUM|ID)>", re.IGNORECASE)


def skeleton(line: str) -> str:
    """Mask quoted strings, paths, numbers, and identifiers in one line. Return lower case tokens."""
    for pattern, repl in _MASKS:
        line = pattern.sub(repl, line)
    return " ".join(line.lower().split())


def skeleton_lines(text: str) -> set[str]:
    """Return the set of skeleton lines of a prompt that have enough tokens."""
    skels = {skeleton(line) for line in text.splitlines()}
    return {s for s in skels if len(s.split()) >= DCFG["d5"]["min_line_tokens"]}


def _frequent(st: dict[str, Any]) -> dict[str, int]:
    """Return the skeleton lines that occur in at least `skeleton_min_frac` of the first prompts."""
    n = st.get("n", 0)
    if n < DCFG["d5"]["min_sessions"]:
        return {}
    min_df = max(2.0, DCFG["d5"]["skeleton_min_frac"] * n)
    return {s: df for s, df in st["df"].items() if df >= min_df}


def template_skeletons(acct: AccountState) -> set[str]:
    """Hashes of the frequent skeleton lines of an account (campaign edge: shared skeletons)."""
    return {text_hash(s) for s in _frequent(acct.scratch.get(NAME) or {})}


def first_message_hashes(acct: AccountState) -> set[str]:
    """Hashes of the first user messages of the account sessions (campaign edge: shared tasks)."""
    return set((acct.scratch.get(NAME) or {}).get("first_hashes", set()))


def invert_templates(acct: AccountState) -> str:
    """Build one {slot} template from the frequent skeleton lines of the account. No LLM call.

    Lines that are frequent keep their masked form. Other lines become one {task} slot.
    We cut the LLM inversion (cut list item 2): prompts can contain SWE-chat text, which must stay local.
    """
    st = acct.scratch.get(NAME) or {}
    frequent = _frequent(st)
    samples = st.get("samples", [])
    if not frequent or not samples:
        return ""
    out: list[str] = []
    for line in samples[0].splitlines():
        skel = skeleton(line)
        if skel in frequent:
            out.append(_SLOT.sub(lambda m: "{" + m.group(1).lower() + "}", skel))
        elif line.strip() and (not out or out[-1] != "{task}"):
            out.append("{task}")
    return "\n".join(out)


class TemplateDetector:
    name = NAME

    def observe(self, view: DetectorView, acct: AccountState) -> list[Evidence]:
        st = scratch(acct, self.name)
        if not st:
            st.update(n=0, df=Counter(), prompt_skels=[], seen=set(), first_hashes=set(), samples=[])
        if view.session_id in st["seen"]:
            return []
        st["seen"].add(view.session_id)
        text = first_user_text(view)
        if not text:
            return []
        skels = skeleton_lines(text)
        st["n"] += 1
        st["df"].update(skels)
        st["prompt_skels"].append(skels)
        st["first_hashes"].add(text_hash(text))
        if len(st["samples"]) < DCFG["d5"]["inversion_prompts"]:
            st["samples"].append(text)
        return self._evidence(st, view, skels)

    def _evidence(self, st: dict[str, Any], view: DetectorView, skels: set[str]) -> list[Evidence]:
        frequent = _frequent(st)
        shared = [frequent[s] for s in skels if s in frequent]
        if not shared or not allow_evidence(st, "skeleton"):
            return []
        df, n = max(shared), st["n"]
        return [new_evidence(self.name, view, weight=0.7, message_index=0, summary=(
            f"The first prompt of session {view.session_id} shares a masked line with "
            f"{df - 1} of {n - 1} earlier first prompts of this account."))]

    def features(self, acct: AccountState) -> dict[str, float]:
        st = acct.scratch.get(self.name) or {}
        n = st.get("n", 0)
        frequent = _frequent(st)
        with_template = sum(1 for skels in st.get("prompt_skels", []) if skels & frequent.keys())
        return {
            "d5_template_mass": ratio(with_template, n),
            "d5_skeletons_per_session": ratio(len(st.get("df", {})), n),
            "d5_top_skeleton_frac": ratio(max(frequent.values(), default=0), n),
        }
