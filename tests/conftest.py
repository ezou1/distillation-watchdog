"""Test setup: every test uses a fake context-model scorer (no GPU service, no network)."""
from __future__ import annotations

import pytest

from dw.common import context
from fake_scorer import FakeScorer


@pytest.fixture(autouse=True)
def fake_context_scorer(monkeypatch: pytest.MonkeyPatch) -> FakeScorer:
    scorer = FakeScorer()
    monkeypatch.setattr(context, "_SCORER", scorer)
    return scorer
