"""Rules score: duplicate harvesting (D1) against thresholds from validation, with the D7 exoneration.

The rules score is one statistic, `rules.feature` (the share of requests that repeat an earlier context of the
account). Its block and review thresholds come from the validation accounts (account.block_max_fpr and
review_max_fpr). D7 can only lower the result: an account with many distinct `user` values (an application with
end users) or a declared use gets at most "review" from the rules.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from dw.common.config import CFG
from dw.watchdog.account_model import choose_thresholds
from dw.watchdog.policy import score_level

RCFG = CFG["rules"]


def value(features: dict[str, float]) -> float:
    return float(features.get(RCFG["feature"], 0.0))


def exonerated(features: dict[str, float], declared_use: Optional[str]) -> bool:
    """D7: many distinct end users, or a declared use. The rules then give at most "review"."""
    return bool(declared_use) or features.get("d7_distinct_users", 0.0) >= RCFG["exonerate_min_users"]


def level(features: dict[str, float], thresholds: Optional[dict[str, float]], declared_use: Optional[str]) -> str:
    """normal, review, or block from the rules statistic."""
    lvl = score_level(value(features), thresholds)
    return "review" if lvl == "block" and exonerated(features, declared_use) else lvl


def fit_thresholds(values: list[float], labels: list[int]) -> dict[str, float]:
    """Block and review thresholds on the rules statistic (one value per validation account)."""
    return choose_thresholds(values, labels)


def save(thresholds: dict[str, float], models_dir: Path) -> None:
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / RCFG["file"]).write_text(json.dumps({"feature": RCFG["feature"], "thresholds": thresholds}, indent=1))


def load(models_dir: Path) -> Optional[dict[str, float]]:
    """The saved thresholds, or None if the file does not exist."""
    p = models_dir / RCFG["file"]
    return json.loads(p.read_text())["thresholds"] if p.exists() else None
