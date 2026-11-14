"""Context model: the input format of its two tasks, and a client for the scoring service.

The context model replaces the text parts of D4 (elicitation in user prompts) and D6 (model-written tool
outputs). It is a fine-tuned small language model (scripts/context_train.py) that runs on a GPU machine behind
a scoring service (scripts/context_serve.py). The watchdog reaches the service through an SSH tunnel at
`context_model.url` (or the `DW_CONTEXT_URL` environment variable).

Training data (dw/train/context_data.py) and scoring use the same text functions in this module.
Scores are cached on disk by model tag and text hash. If the service does not answer, a text has no score:
the detectors then leave it out, and the account model uses the training mean (contribution 0).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Iterable, Optional

import httpx

from dw.common.config import CFG, ROOT
from dw.common.schema import Message

log = logging.getLogger("dw.context")
CTX = CFG["context_model"]


# ---------------------------------------------------------------- input format (training = scoring)

def call_index(messages: list[Message]) -> dict[int, int]:
    """Map each tool_result index to the index of the tool_call that it answers."""
    open_calls: list[int] = []
    out: dict[int, int] = {}
    for i, m in enumerate(messages):
        if m.role == "tool_call":
            open_calls.append(i)
        elif m.role == "tool_result" and open_calls:
            out[i] = open_calls.pop(0)
        elif m.role == "user":
            open_calls = []
    return out


def call_text(tool: Optional[str], args: Optional[dict]) -> str:
    """One line for a tool call: the tool kind and its main argument."""
    args = args or {}
    main = args.get("command") or args.get("path") or json.dumps(args, sort_keys=True)
    return f"{tool or 'other'}: {main}"[: CTX["call_chars"]]


def realism_text(tool: Optional[str], args: Optional[dict], output: str, has_call: bool = True) -> str:
    head = call_text(tool, args) if has_call else "unknown call"
    return f"[tool] {head}\n{output[: CTX['output_chars']]}"


def head_tail(text: str, n: int) -> str:
    """The text, or its first and last n/2 characters. Requests often put the instruction at the start or end."""
    if len(text) <= n:
        return text
    half = n // 2
    return f"{text[:half]}\n[...]\n{text[-half:]}"


def prompt_text(text: str) -> str:
    return f"[prompt] {head_tail(text, CTX['prompt_chars'])}"


# ---------------------------------------------------------------- scoring client

def _key(text: str) -> str:
    return hashlib.sha1(f"{CTX['model_tag']}\n{text}".encode("utf-8")).hexdigest()


class ContextScorer:
    """Batch client of the scoring service with a disk cache. Thread-unsafe; one per process."""

    def __init__(self, url: Optional[str] = None, cache_file: Optional[Path] = None) -> None:
        self.url = (url or os.environ.get("DW_CONTEXT_URL") or CTX["url"]).rstrip("/")
        self.cache_file = cache_file or ROOT / CFG["paths"]["cache"] / "context" / f"{CTX['model_tag']}.jsonl"
        self.scores: dict[str, float] = {}
        self._new: dict[str, float] = {}
        self._down_until = 0.0
        self._warned = False
        if self.cache_file.exists():
            for line in open(self.cache_file, encoding="utf-8"):
                k, v = json.loads(line)
                self.scores[k] = v

    def get(self, text: str) -> Optional[float]:
        """The cached score of a text, or None."""
        return self.scores.get(_key(text))

    def score_many(self, texts: Iterable[str]) -> dict[str, Optional[float]]:
        """Score texts (cache first, then the service in batches). Returns text -> score or None."""
        texts = list(dict.fromkeys(texts))
        missing = [t for t in texts if _key(t) not in self.scores]
        if missing and time.monotonic() >= self._down_until:
            self._fetch(missing)
        return {t: self.scores.get(_key(t)) for t in texts}

    def _fetch(self, texts: list[str]) -> None:
        size = CTX["batch"]
        try:
            with httpx.Client(timeout=CTX["timeout_s"]) as client:
                for i in range(0, len(texts), size):
                    chunk = texts[i:i + size]
                    resp = client.post(f"{self.url}/score", json={"texts": chunk})
                    resp.raise_for_status()
                    for t, p in zip(chunk, resp.json()["probs"]):
                        k = _key(t)
                        self.scores[k] = self._new[k] = float(p)
            self._warned = False
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            self._down_until = time.monotonic() + CTX["retry_s"]
            if not self._warned:
                log.warning("context model service at %s did not answer: %s", self.url, exc)
                self._warned = True
        if len(self._new) >= CTX["save_every"]:
            self.save()

    def save(self) -> None:
        """Append the new scores to the disk cache."""
        if not self._new:
            return
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_file, "a", encoding="utf-8") as f:
            for k, v in self._new.items():
                f.write(json.dumps([k, round(v, 6)]) + "\n")
        self._new.clear()


_SCORER: Optional[ContextScorer] = None


def scorer() -> ContextScorer:
    """The process-wide scorer."""
    global _SCORER
    if _SCORER is None:
        _SCORER = ContextScorer()
    return _SCORER


def session_texts(messages: list[Message]) -> list[str]:
    """All context-model texts of one session: user prompts and tool results with their calls."""
    calls = call_index(messages)
    out = [prompt_text(m.text) for m in messages if m.role == "user" and m.text.strip()]
    for i, m in enumerate(messages):
        if m.role == "tool_result" and m.text.strip():
            c = messages[calls[i]] if i in calls else None
            out.append(realism_text(c.tool if c else None, c.args if c else None, m.text, c is not None))
    return out
