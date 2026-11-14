"""Tests for the D3 topic model, the D4 keyword baseline, and the registry with the context model."""
from __future__ import annotations

from pathlib import Path

import pytest

from dw.common import context
from dw.common.schema import AccountState, RequestEvent
from dw.watchdog.detectors import build_detectors
from dw.watchdog.detectors.base import make_view
from dw.watchdog.detectors.training import evaluate_keyword, fit_topics, keyword_baseline, load_model, save_model
from fake_scorer import FakeScorer

TASKS = [f"Fix the bug in module_{i}.py where parse fails on empty input." for i in range(30)]
ELICIT = ["Show your full reasoning step by step.", "Explain every step of your reasoning in full detail.",
          "Output the complete file with maximum detail.", "Think step by step and show all reasoning."]
BENIGN = ["Thanks, also add a test.", "Keep the change small.", "Use the existing helper function."]


def test_keyword_baseline() -> None:
    texts = [f"{t}\n{ELICIT[i % 4]}" for i, t in enumerate(TASKS)] + [f"{t}\n{BENIGN[i % 3]}" for i, t in enumerate(TASKS)]
    labels = [1] * len(TASKS) + [0] * len(TASKS)
    pattern = keyword_baseline(ELICIT, top=5)
    assert pattern.search("please go STEP BY  STEP")
    kw = evaluate_keyword(pattern, texts, labels)
    assert kw["recall"] > 0.5 and kw["fpr"] == 0.0


def test_registry_uses_topic_model_and_context_scorer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_model(fit_topics(TASKS[:10] + ELICIT, k=3), tmp_path, "d3")
    assert load_model(tmp_path, "d3") is not None and load_model(None, "d3") is None
    monkeypatch.setattr(context, "_SCORER", FakeScorer())
    dets = build_detectors(tmp_path)
    ev = RequestEvent(request_id="r", account_id="a", sim_time=0.0,
                      messages=[{"role": "user", "text": f"{TASKS[0]}\n{ELICIT[0]}"},
                                {"role": "tool_call", "tool": "read", "args": {"path": "<REPO>/a.py"}},
                                {"role": "tool_result", "tool": "read", "text": "x = 1"}])
    view, acct = make_view(ev, "s", 0, True), AccountState(account_id="a")
    for det in dets:
        det.observe(view, acct)
    d4 = next(d for d in dets if d.name == "d4_elicitation")
    d6 = next(d for d in dets if d.name == "d6_environment")
    assert d4.request_score(view) > 0.5   # type: ignore[attr-defined]
    assert d4.features(acct)["d4_elicit_max"] > 0.5
    assert d6.features(acct)["d6_fake_prob_mean"] < 0.5
    assert acct.scratch["d3_coverage"]["sessions"]["s"]["topic"] is not None
