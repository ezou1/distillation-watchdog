"""A stand-in for the context-model scoring service in tests (no GPU, no network)."""
from __future__ import annotations

from typing import Iterable, Optional


class FakeScorer:
    """Scores 0.95 for texts with a marker word, else 0.05. `cached=False` makes get() miss (forces the queue)."""

    def __init__(self, markers: tuple[str, ...] = ("FAKE", "reasoning"), cached: bool = True, up: bool = True) -> None:
        self.markers, self.cached, self.up = markers, cached, up
        self.batches: list[list[str]] = []
        self.seen: set[str] = set()
        self.asked: list[str] = []          # every text that a detector gave to the scorer

    def _score(self, text: str) -> float:
        return 0.95 if any(m in text for m in self.markers) else 0.05

    def get(self, text: str) -> Optional[float]:
        self.asked.append(text)
        return self._score(text) if (self.cached or text in self.seen) and self.up else None

    def score_many(self, texts: Iterable[str]) -> dict[str, Optional[float]]:
        texts = list(dict.fromkeys(texts))
        self.batches.append(texts)
        self.asked += texts
        if not self.up:
            return {t: None for t in texts}
        self.seen.update(texts)
        return {t: self._score(t) for t in texts}

    def save(self) -> None:
        pass
