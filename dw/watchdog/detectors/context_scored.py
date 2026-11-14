"""Shared logic of the detectors that use the context model (D4 text, D6).

A text gets its score at once if the score is in the cache (offline runs prefetch all texts of a scenario).
Else the text waits in a queue, and the queue is scored in one batch when the features are read.
A text that the service cannot score stays in the queue for the next try (at most `_PENDING_MAX` texts).
Evidence from the batch goes to `drain_evidence()`, which the worker calls after it reads the features.
"""
from __future__ import annotations

from typing import Any, Optional

from dw.common.context import ContextScorer
from dw.common.schema import DetectorView, Evidence

_PENDING_MAX = 50_000


class ContextScored:
    """Mixin: `_score_or_queue()` in observe(), `_flush()` at the start of features()."""

    scorer: Optional[ContextScorer]

    def _init_queue(self, scorer: Optional[ContextScorer]) -> None:
        self.scorer = scorer
        self._pending: list[tuple[dict[str, Any], DetectorView, int, str]] = []
        self._late: list[Evidence] = []

    def _add(self, st: dict[str, Any], view: DetectorView, index: int, score: float) -> list[Evidence]:
        """Add one score to the account state. Return evidence items (detector-specific)."""
        raise NotImplementedError

    def _score_or_queue(self, st: dict[str, Any], view: DetectorView, index: int, text: str) -> list[Evidence]:
        if self.scorer is None:
            return []
        score = self.scorer.get(text)
        if score is None:
            self._pending.append((st, view, index, text))
            return []
        return self._add(st, view, index, score)

    def _flush(self) -> None:
        if not self._pending or self.scorer is None:
            return
        scores = self.scorer.score_many(t for *_, t in self._pending)
        keep = []
        for st, view, index, text in self._pending:
            score = scores.get(text)
            if score is None:
                keep.append((st, view, index, text))
            else:
                self._late += self._add(st, view, index, score)
        self._pending = keep[-_PENDING_MAX:]

    def drain_evidence(self) -> list[Evidence]:
        """Evidence items that came from a batch. The worker adds them to their accounts."""
        out, self._late = self._late, []
        return out
