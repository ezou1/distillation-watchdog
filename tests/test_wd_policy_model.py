"""Tests for the policy states and the account model (fit, predict, contributions, thresholds, save and load)."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from dw.common.config import CFG
from dw.watchdog import account_model as am
from dw.watchdog.policy import decide

TH = {"block": 0.9, "review": 0.6}
MIN = CFG["account"]["min_requests"]


def test_policy_states() -> None:
    assert decide(MIN - 1, None, TH) == "insufficient"
    assert decide(MIN, 0.2, TH) == "normal"
    assert decide(MIN, 0.7, TH) == "review"
    assert decide(MIN, 0.95, TH) == "block"
    assert decide(MIN, None, None) == "normal"                    # no model
    assert decide(MIN, 0.2, TH, campaign_state="block") == "block"  # stealth member of a blocked campaign
    assert decide(MIN, 0.95, TH, campaign_state="review") == "block"


def test_policy_disclosed_and_quota() -> None:
    assert decide(100, 0.99, TH, "block", declared_use="benchmark", quota=200) == "disclosed"
    assert decide(201, 0.1, TH, declared_use="benchmark", quota=200) == "review"
    assert decide(201, 0.99, TH, declared_use="benchmark", quota=200) == "block"
    assert decide(5, None, None, declared_use="benchmark", quota=None) == "disclosed"


def _synthetic(n: int, seed: int) -> tuple[list[dict[str, float]], list[int]]:
    rng = np.random.default_rng(seed)
    rows, labels = [], []
    for i in range(n):
        y = int(i % 4 == 0)
        rows.append({"good": float(rng.normal(2.0 * y, 1.0)), "noise": float(rng.normal(0, 1)),
                     "const": 1.0})
        labels.append(y)
    return rows, labels


def test_fit_predict_contributions(tmp_path: Path) -> None:
    rows, labels = _synthetic(400, 1)
    model = am.fit(rows, labels)
    coefs = am.coefficients(model)
    assert coefs[0]["name"] == "good" and coefs[0]["coef"] > 0
    row = rows[0]
    contrib = model.contributions(row)
    assert math.isclose(model.intercept + sum(contrib.values()), model.logit(row), rel_tol=1e-9)
    assert math.isclose(model.predict(row), 1 / (1 + math.exp(-model.logit(row))))
    assert contrib["const"] == 0.0
    assert model.contributions({})["good"] == 0.0               # missing feature = training mean
    val_rows, val_labels = _synthetic(400, 2)
    th = am.set_thresholds(model, val_rows, val_labels)
    scores = model.predict_many(val_rows)
    neg = [s for s, y in zip(scores, val_labels) if y == 0]
    assert sum(s >= th["block"] for s in neg) / len(neg) <= CFG["account"]["block_max_fpr"]
    assert sum(s >= th["review"] for s in neg) / len(neg) <= CFG["account"]["review_max_fpr"]
    assert th["review"] <= th["block"]
    am.save(model, tmp_path / am.ACCOUNT_MODEL_FILE)
    loaded = am.load(tmp_path / am.ACCOUNT_MODEL_FILE)
    assert loaded is not None and loaded.thresholds == th and loaded.predict(row) == model.predict(row)
    assert am.load(tmp_path / "missing.pkl") is None


def test_threshold_at_fpr_is_lowest_valid() -> None:
    scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]
    labels = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1]
    t = am.threshold_at_fpr(scores, labels, 0.1)    # one false positive of ten allowed
    assert 0.9 < t <= 0.95 + 1e-12
    assert am.threshold_at_fpr(scores, labels, 0.0) > 0.95
