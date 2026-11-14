"""Account model and per-request model: standardized logistic regression with per-feature contributions.

The functions take plain inputs (feature dicts and 0/1 labels), so training, evaluation, and the worker use the same code.
Thresholds come from a validation set: `block` at a normal false positive rate of 1% or less, `review` at 5% or less.
"""
from __future__ import annotations

import math
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression

from dw.common.config import CFG
from dw.common.schema import DetectorView

ACCOUNT_MODEL_FILE = "account_model.pkl"
REQUEST_MODEL_FILE = "request_model.pkl"

FeatureRow = dict[str, float]


@dataclass
class LogitModel:
    """A fitted logistic regression on standardized features."""

    names: list[str]
    mean: np.ndarray
    scale: np.ndarray
    coef: np.ndarray
    intercept: float
    thresholds: dict[str, float] = field(default_factory=dict)

    def _z(self, row: FeatureRow) -> np.ndarray:
        """Standardized vector. A missing feature gets the training mean (contribution 0)."""
        raw = np.array([row.get(n, m) for n, m in zip(self.names, self.mean)], dtype=float)
        return (raw - self.mean) / self.scale

    def contributions(self, row: FeatureRow) -> dict[str, float]:
        """Per-feature contribution to the log-odds: coefficient times standardized value."""
        return {n: float(c) for n, c in zip(self.names, self.coef * self._z(row))}

    def logit(self, row: FeatureRow) -> float:
        return float(self.intercept + np.dot(self.coef, self._z(row)))

    def predict(self, row: FeatureRow) -> float:
        """Calibrated probability of the positive (attack) class."""
        return _sigmoid(self.logit(row))

    def predict_many(self, rows: Sequence[FeatureRow]) -> list[float]:
        return [self.predict(r) for r in rows]


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def feature_names(rows: Sequence[FeatureRow]) -> list[str]:
    """Sorted union of the feature names of all rows."""
    return sorted({name for row in rows for name in row})


def to_matrix(rows: Sequence[FeatureRow], names: list[str]) -> np.ndarray:
    return np.array([[float(r.get(n, 0.0)) for n in names] for r in rows], dtype=float)


def fit(rows: Sequence[FeatureRow], labels: Sequence[int], names: list[str] | None = None,
        C: float | None = None) -> LogitModel:
    """Fit a standardized logistic regression. Labels: 1 = attack, 0 = normal."""
    names = names or feature_names(rows)
    X = to_matrix(rows, names)
    mean = X.mean(axis=0)
    scale = X.std(axis=0)
    scale[scale == 0] = 1.0
    clf = LogisticRegression(C=C or CFG["account"]["logreg_C"], class_weight=CFG["account"]["class_weight"],
                             max_iter=2000)
    clf.fit((X - mean) / scale, np.asarray(labels, dtype=int))
    return LogitModel(names=names, mean=mean, scale=scale, coef=clf.coef_[0].copy(),
                      intercept=float(clf.intercept_[0]))


def coefficients(model: LogitModel) -> list[dict[str, float | str]]:
    """Coefficients on the standardized scale, largest absolute value first."""
    pairs = sorted(zip(model.names, model.coef), key=lambda p: -abs(p[1]))
    return [{"name": n, "coef": round(float(c), 4)} for n, c in pairs]


def threshold_at_fpr(scores: Sequence[float], labels: Sequence[int], max_fpr: float) -> float:
    """Lowest threshold (score >= t means positive) whose normal false positive rate is <= max_fpr.

    The lowest such threshold gives the highest recall.
    """
    neg = sorted((s for s, y in zip(scores, labels) if y == 0), reverse=True)
    if not neg:
        pos = [s for s, y in zip(scores, labels) if y == 1]
        return float(min(pos)) if pos else 0.5
    allowed = int(math.floor(max_fpr * len(neg) + 1e-9))
    if allowed >= len(neg):
        return 0.0
    return float(np.nextafter(neg[allowed], 2.0))


def choose_thresholds(scores: Sequence[float], labels: Sequence[int]) -> dict[str, float]:
    """Block and review thresholds from validation scores (config: account.block_max_fpr, review_max_fpr)."""
    block = threshold_at_fpr(scores, labels, CFG["account"]["block_max_fpr"])
    review = threshold_at_fpr(scores, labels, CFG["account"]["review_max_fpr"])
    return {"block": block, "review": min(review, block)}


def set_thresholds(model: LogitModel, rows: Sequence[FeatureRow], labels: Sequence[int]) -> dict[str, float]:
    """Choose thresholds on a validation set and store them in the model."""
    model.thresholds = choose_thresholds(model.predict_many(rows), labels)
    return model.thresholds


def save(model: LogitModel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(model, f)


def load(path: Path) -> LogitModel | None:
    """Load a model. Return None if the file does not exist."""
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return pickle.load(f)


def request_features(view: DetectorView, elicitation: float) -> FeatureRow:
    """Features of one request for the per-request model (section B8.4 item 7)."""
    return {
        "req_dup_count": float(view.dup_count),
        "req_temperature": float(view.temperature),
        "req_n": float(view.n),
        "req_max_tokens": float(view.max_tokens or 0),
        "req_elicitation": float(elicitation),
        "req_user_present": 1.0 if view.user else 0.0,
    }
