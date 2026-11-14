"""D4 elicitation: the account asks for the training signal (full reasoning, maximum detail, maximum length).

The context model (dw/common/context.py) scores user text only. Request parameters add four rates.
"""
from __future__ import annotations

from typing import Any, Optional

from dw.common.config import CFG
from dw.common.context import ContextScorer, prompt_text
from dw.common.schema import AccountState, DetectorView, Evidence
from dw.watchdog.detectors.base import new_evidence
from dw.watchdog.detectors.common import DCFG, allow_evidence, fresh_messages, ratio, scratch
from dw.watchdog.detectors.context_scored import ContextScored


class ElicitationDetector(ContextScored):
    name = "d4_elicitation"

    def __init__(self, scorer: Optional[ContextScorer] = None) -> None:
        self._init_queue(scorer)
        self.cfg = DCFG["d4"]
        self.max_tokens_min = self.cfg["max_tokens_frac_of_cap"] * CFG["sim"]["max_tokens_cap"]

    def request_score(self, view: DetectorView) -> float:
        """Per-request elicitation score: the largest cached score of the user messages (0.0 if not scored yet)."""
        if self.scorer is None:
            return 0.0
        return max((self.scorer.get(prompt_text(m.text)) or 0.0 for m in view.messages if m.role == "user" and m.text.strip()),
                   default=0.0)

    def observe(self, view: DetectorView, acct: AccountState) -> list[Evidence]:
        st = scratch(acct, self.name)
        if not st:
            st.update(n=0, at_max=0, max_effort=0, n_gt1=0, temp_sum=0.0,
                      n_user=0, score_sum=0.0, score_max=0.0, n_high=0, param_ev=False)
        out = self._observe_params(st, view)
        fresh, _ = fresh_messages(acct, view)
        for m in fresh:
            if m.role == "user" and m.text.strip():
                out += self._score_or_queue(st, view, m.index, prompt_text(m.text))
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

    def _add(self, st: dict[str, Any], view: DetectorView, index: int, score: float) -> list[Evidence]:
        st["n_user"] += 1
        st["score_sum"] += score
        st["score_max"] = max(st["score_max"], score)
        st["n_high"] += int(score >= self.cfg["elicit_threshold"])
        if score >= self.cfg["evidence_score"] and allow_evidence(st, "elicit"):
            return [new_evidence(self.name, view, message_index=index, summary=(
                f"User message {index} of session {view.session_id} has elicitation score {score:.2f}."))]
        return []

    def features(self, acct: AccountState) -> dict[str, float]:
        self._flush()
        st = acct.scratch.get(self.name) or {}
        n, n_user = st.get("n", 0), st.get("n_user", 0)
        text = {} if n_user == 0 else {          # no scored text yet (or no service): training mean
            "d4_elicit_mean": ratio(st.get("score_sum", 0.0), n_user),
            "d4_elicit_max": float(st.get("score_max", 0.0)),
            "d4_elicit_frac": ratio(st.get("n_high", 0), n_user),
        }
        return {
            **text,
            "d4_max_tokens_frac": ratio(st.get("at_max", 0), n),
            "d4_max_effort_frac": ratio(st.get("max_effort", 0), n),
            "d4_n_gt1_rate": ratio(st.get("n_gt1", 0), n),
            "d4_mean_temp": ratio(st.get("temp_sum", 0.0), n),
        }

