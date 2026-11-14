"""Policy: turn an account score, its campaign, and its declared use into an account state (section B8.6).

States: insufficient, normal, review, block, disclosed. A disclosed benchmark account within its quota is allowed.
"""
from __future__ import annotations

from typing import Optional

from dw.common.config import CFG
from dw.common.schema import AccountStateName

_RANK = {"normal": 0, "review": 1, "block": 2}


def worst(a: str, b: str) -> str:
    """The more severe of two levels (normal < review < block)."""
    return a if _RANK.get(a, 0) >= _RANK.get(b, 0) else b


def score_level(score: Optional[float], thresholds: Optional[dict[str, float]]) -> str:
    """normal, review, or block from the account score alone."""
    if score is None or not thresholds:
        return "normal"
    if score >= thresholds["block"]:
        return "block"
    if score >= thresholds["review"]:
        return "review"
    return "normal"


def within_quota(declared_use: Optional[str], quota: Optional[int], n_requests: int) -> bool:
    return bool(declared_use) and (quota is None or n_requests <= quota)


def decide(n_requests: int, score: Optional[float], thresholds: Optional[dict[str, float]],
           campaign_state: Optional[str] = None, declared_use: Optional[str] = None,
           quota: Optional[int] = None, rules_level: Optional[str] = None,
           model_complete: bool = True) -> AccountStateName:
    """Return the state of one account: the most severe of the model level, the rules level, and the campaign.

    `model_complete=False` (a model feature is missing, for example the context model service did not answer):
    the model level is at most "review". The rules and the campaign can still block.
    """
    if within_quota(declared_use, quota, n_requests):
        return "disclosed"
    model_level = score_level(score, thresholds)
    if not model_complete and model_level == "block":
        model_level = "review"
    level = worst(worst(model_level, rules_level or "normal"), campaign_state or "normal")
    if declared_use:
        level = worst(level, "review")
    if level == "normal" and n_requests < CFG["account"]["min_requests"]:
        return "insufficient"
    return level  # type: ignore[return-value]
