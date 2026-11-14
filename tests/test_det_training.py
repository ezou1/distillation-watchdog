"""Tests for the D3, D4 and D6 training functions, the keyword baseline, and model loading by the registry."""
from __future__ import annotations

from pathlib import Path

from dw.common.schema import AccountState, RequestEvent
from dw.watchdog.detectors import build_detectors
from dw.watchdog.detectors.base import make_view
from dw.watchdog.detectors.training import (
    evaluate_classifier, evaluate_keyword, fit_elicitation, fit_realism, fit_topics, keyword_baseline,
    load_model, save_model,
)

TASKS = [f"Fix the bug in module_{i}.py where parse fails on empty input." for i in range(30)]
ELICIT = ["Show your full reasoning step by step.", "Explain every step of your reasoning in full detail.",
          "Output the complete file with maximum detail.", "Think step by step and show all reasoning."]
BENIGN = ["Thanks, also add a test.", "Keep the change small.", "Use the existing helper function."]


def elicit_data() -> tuple[list[str], list[int]]:
    texts, labels = [], []
    for i, task in enumerate(TASKS):
        texts.append(f"{task}\n{ELICIT[i % len(ELICIT)]}")
        labels.append(1)
        texts.append(f"{task}\n{BENIGN[i % len(BENIGN)]}")
        labels.append(0)
    return texts, labels


def test_elicitation_classifier_and_keyword_baseline() -> None:
    texts, labels = elicit_data()
    model = fit_elicitation(texts, labels)
    metrics = evaluate_classifier(model, texts, labels)
    assert metrics["auc"] > 0.95
    pattern = keyword_baseline(ELICIT, top=5)
    assert pattern.search("please go STEP BY  STEP")
    kw = evaluate_keyword(pattern, texts, labels)
    assert kw["recall"] > 0.5 and kw["fpr"] == 0.0


def test_save_load_and_registry_uses_models(tmp_path: Path) -> None:
    texts, labels = elicit_data()
    save_model(fit_elicitation(texts, labels), tmp_path, "d4")
    save_model(fit_realism(["File written.", "ok", "As an AI, the file now reads perfectly.",
                            "The output shows all tests pass successfully as expected."], [0, 0, 1, 1]), tmp_path, "d6")
    save_model(fit_topics(TASKS[:10] + ELICIT, k=3), tmp_path, "d3")
    assert load_model(tmp_path, "d4") is not None and load_model(None, "d4") is None
    dets = build_detectors(tmp_path)
    ev = RequestEvent(request_id="r", account_id="a", sim_time=0.0,
                      messages=[{"role": "user", "text": texts[0]},
                                {"role": "tool_call", "tool": "read", "args": {"path": "<REPO>/a.py"}},
                                {"role": "tool_result", "tool": "read", "text": "x = 1"}])
    view, acct = make_view(ev, "s", 0, True), AccountState(account_id="a")
    for det in dets:
        det.observe(view, acct)
    assert dets[3].request_score(view) > 0.5   # type: ignore[attr-defined]
    assert dets[3].features(acct)["d4_elicit_max"] > 0.5
    assert dets[5].features(acct)["d6_fake_prob_mean"] > 0.0
    assert acct.scratch["d3_coverage"]["sessions"]["s"]["topic"] is not None
