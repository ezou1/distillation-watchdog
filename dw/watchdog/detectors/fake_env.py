"""D6 environment: tool outputs that a model wrote instead of a real machine.

The context model (dw/common/context.py) scores each tool output together with its tool call. The model was
trained on fake outputs and real outputs from the same source (Open-SWE), so it does not learn the source.
The earlier shadow file system was removed: in replay it found no contradictions in fake-environment sessions.
"""
from __future__ import annotations

from typing import Any, Optional

from dw.common.context import CTX, ContextScorer, call_index, realism_text
from dw.common.schema import AccountState, DetectorView, Evidence
from dw.watchdog.detectors.base import new_evidence
from dw.watchdog.detectors.common import allow_evidence, fresh_messages, ratio, scratch
from dw.watchdog.detectors.context_scored import ContextScored

HIGH = 0.5          # a tool output with this realism score or more counts as model-written


class FakeEnvDetector(ContextScored):
    name = "d6_environment"

    def __init__(self, scorer: Optional[ContextScorer] = None) -> None:
        self._init_queue(scorer)

    def observe(self, view: DetectorView, acct: AccountState) -> list[Evidence]:
        st = scratch(acct, self.name)
        if not st:
            st.update(prob_sum=0.0, n_scored=0, n_high=0)
        fresh, _ = fresh_messages(acct, view)
        if not any(m.role == "tool_result" for m in fresh):
            return []
        calls = call_index(view.messages)      # type: ignore[arg-type]  (ViewMessage has role, tool, args)
        out: list[Evidence] = []
        for m in fresh:
            if m.role != "tool_result" or not m.text.strip():
                continue
            c = view.messages[calls[m.index]] if m.index in calls else None
            text = realism_text(c.tool if c else None, c.args if c else None, m.text, c is not None)
            out += self._score_or_queue(st, view, m.index, text)
        return out

    def _add(self, st: dict[str, Any], view: DetectorView, index: int, score: float) -> list[Evidence]:
        st["prob_sum"] += score
        st["n_scored"] += 1
        st["n_high"] += int(score >= HIGH)
        if score >= CTX["evidence_score"] and allow_evidence(st, "fake"):
            return [new_evidence(self.name, view, message_index=index, weight=score, summary=(
                f"Tool output {index} of session {view.session_id} looks model-written (realism score {score:.2f})."))]
        return []

    def features(self, acct: AccountState) -> dict[str, float]:
        self._flush()
        st = acct.scratch.get(self.name) or {}
        n = st.get("n_scored", 0)
        if n == 0:          # nothing scored yet (or no service): leave the features out = training mean
            return {}
        return {"d6_fake_prob_mean": ratio(st.get("prob_sum", 0.0), n), "d6_fake_high_frac": ratio(st.get("n_high", 0), n)}
