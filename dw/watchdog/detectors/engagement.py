"""D2 engagement: does the account use the answers? Session statistics from the rebuilt sessions.

A developer replies, corrects the model, and comes back to the same repository. A distiller does not.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np

from dw.common.embed import embed
from dw.common.schema import AccountState, DetectorView, Evidence
from dw.watchdog.detectors.common import DCFG, fresh_messages, ratio, scratch
from dw.watchdog.detectors.coverage import session_repos, track_session


@lru_cache(maxsize=1)
def _prototypes() -> np.ndarray:
    return embed(list(DCFG["d2"]["correction_prototypes"]))


def is_correction(text: str) -> bool:
    """Return True if the text is similar to one of the correction prototype phrases."""
    if not text.strip():
        return False
    sims = _prototypes() @ embed([text])[0]
    return float(sims.max()) >= DCFG["d2"]["correction_threshold"]


class EngagementDetector:
    name = "d2_engagement"

    def __init__(self) -> None:
        self.cfg = DCFG["d2"]

    def observe(self, view: DetectorView, acct: AccountState) -> list[Evidence]:
        st = scratch(acct, self.name)
        rec, _ = track_session(st, acct, view)
        fresh, _ = fresh_messages(acct, view)
        users = [m for m in view.messages if m.role == "user"]
        followups = [m for m in fresh if m.role == "user" and m.index != users[0].index]
        # A user turn after the first one answers a final assistant step (a model stop).
        rec["followed"] = rec.get("followed", False) or bool(followups) or (
            bool(fresh) and rec.get("last_finish") == "stop")
        rec["last_finish"] = view.finish_reason
        rec["had_stop"] = rec.get("had_stop", False) or view.finish_reason == "stop" or rec["followed"]
        rec["user_turns"] = max(rec.get("user_turns", 0), len(users))
        if not rec.get("corrected"):
            rec["corrected"] = any(is_correction(m.text) for m in followups)
        return []

    def features(self, acct: AccountState) -> dict[str, float]:
        st = acct.scratch.get(self.name) or {}
        recs, labels = session_repos(st)
        n = len(recs)
        with_stop = [r for r in recs if r.get("had_stop")]
        return {
            "d2_user_turns_per_session": ratio(sum(r.get("user_turns", 0) for r in recs), n),
            "d2_followup_rate": ratio(sum(1 for r in with_stop if r.get("followed")), len(with_stop)),
            "d2_correction_rate": ratio(sum(1 for r in recs if r.get("corrected")), n),
            "d2_return_rate": self._return_rate(recs, labels),
            "d2_stop_end_frac": ratio(sum(1 for r in recs if r.get("last_finish") == "stop"), n),
        }

    def _return_rate(self, recs: list[dict[str, Any]], labels: list[int]) -> float:
        """Fraction of repositories with sessions that start at least `return_gap_days` apart."""
        starts: dict[int, list[float]] = {}
        for rec, repo in zip(recs, labels):
            starts.setdefault(repo, []).append(rec["start"])
        gap = self.cfg["return_gap_days"] * 86400.0
        return ratio(sum(1 for s in starts.values() if max(s) - min(s) >= gap), len(starts))
