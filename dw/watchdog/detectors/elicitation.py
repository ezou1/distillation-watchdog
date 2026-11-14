"""D4 elicitation: the account asks for the training signal (full reasoning, maximum detail, maximum length).

A TF-IDF and logistic regression model scores user text only. Request parameters add four rates.
"""
from __future__ import annotations

from typing import Any

from dw.common.config import CFG
from dw.common.hashing import text_hash
from dw.common.schema import AccountState, DetectorView, Evidence
from dw.watchdog.detectors.base import new_evidence
from dw.watchdog.detectors.common import DCFG, allow_evidence, fresh_messages, ratio, scratch

_CACHE_MAX = 200_000


class ElicitationDetector:
    name = "d4_elicitation"

    def __init__(self, model: Any | None = None) -> None:
        self.model = model
        self.cfg = DCFG["d4"]
        self.max_tokens_min = self.cfg["max_tokens_frac_of_cap"] * CFG["sim"]["max_tokens_cap"]
        self._scores: dict[str, float] = {}

    def text_score(self, text: str) -> float:
        """Return the elicitation probability of one user text (0.0 without a model). Cached."""
        if self.model is None or not text.strip():
            return 0.0
        key = text_hash(text)
        if key not in self._scores:
            if len(self._scores) > _CACHE_MAX:
                self._scores.clear()
            self._scores[key] = float(self.model.predict_proba([text])[0, 1])
        return self._scores[key]

    def request_score(self, view: DetectorView) -> float:
        """Per-request elicitation score: the maximum score of the user messages in the view."""
        return max((self.text_score(m.text) for m in view.messages if m.role == "user"), default=0.0)

    def observe(self, view: DetectorView, acct: AccountState) -> list[Evidence]:
        st = scratch(acct, self.name)
        if not st:
            st.update(n=0, at_max=0, max_effort=0, n_gt1=0, temp_sum=0.0,
                      n_user=0, score_sum=0.0, score_max=0.0, n_high=0, param_ev=False)
        out = self._observe_params(st, view)
        fresh, _ = fresh_messages(acct, view)
        for m in fresh:
            if m.role == "user":
                out += self._observe_text(st, view, m.index, m.text)
        return out

    def _observe_params(self, st: dict[str, Any], view: DetectorView) -> list[Evidence]:
        at_max = view.max_tokens is not None and view.max_tokens >= self.max_tokens_min
        max_effort = view.reasoning_effort in self.cfg["max_effort_values"]
        st["n"] += 1
        st["at_max"] += int(at_max)
        st["max_effort"] += int(max_effort)
        st["n_gt1"] += int(view.n > 1)
        st["temp_sum"] += view.temperature
        if at_max and max_effort and not st["param_ev"]:
            st["param_ev"] = True
            return [new_evidence(self.name, view, weight=0.5, summary=(
                f"The request sets max_tokens to {view.max_tokens} (the cap) and reasoning effort "
                f"to {view.reasoning_effort}."))]
        return []

    def _observe_text(self, st: dict[str, Any], view: DetectorView, index: int, text: str) -> list[Evidence]:
        score = self.text_score(text)
        st["n_user"] += 1
        st["score_sum"] += score
        st["score_max"] = max(st["score_max"], score)
        st["n_high"] += int(score >= self.cfg["elicit_threshold"])
        if score >= self.cfg["evidence_score"] and allow_evidence(st, "elicit"):
            return [new_evidence(self.name, view, message_index=index, summary=(
                f"User message {index} of session {view.session_id} has elicitation score {score:.2f}."))]
        return []

    def features(self, acct: AccountState) -> dict[str, float]:
        st = acct.scratch.get(self.name) or {}
        n, n_user = st.get("n", 0), st.get("n_user", 0)
        return {
            "d4_elicit_mean": ratio(st.get("score_sum", 0.0), n_user),
            "d4_elicit_max": float(st.get("score_max", 0.0)),
            "d4_elicit_frac": ratio(st.get("n_high", 0), n_user),
            "d4_max_tokens_frac": ratio(st.get("at_max", 0), n),
            "d4_max_effort_frac": ratio(st.get("max_effort", 0), n),
            "d4_n_gt1_rate": ratio(st.get("n_gt1", 0), n),
            "d4_mean_temp": ratio(st.get("temp_sum", 0.0), n),
        }

