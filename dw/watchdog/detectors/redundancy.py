"""D1 redundancy: duplicate contexts and branches from the prefix hash chain (section B8.2).

A distiller samples the same state many times (best-of-n) and keeps one continuation.
"""
from __future__ import annotations

from typing import Any

from dw.common.schema import AccountState, DetectorView, Evidence
from dw.watchdog.detectors.base import new_evidence
from dw.watchdog.detectors.common import DCFG, allow_evidence, chain_of, ratio, scratch


class RedundancyDetector:
    name = "d1_redundancy"

    def __init__(self) -> None:
        self.cfg = DCFG["d1"]

    def observe(self, view: DetectorView, acct: AccountState) -> list[Evidence]:
        st = scratch(acct, self.name)
        if not st:
            st.update(n=0, n_dup=0, max_dup=1, n_hot=0, n_global=0, keys=set(), children={})
        st["n"] += 1
        hot = view.temperature >= self.cfg["high_temp"]
        out: list[Evidence] = []
        if view.dup_count > 1:
            st["n_dup"] += 1
            st["n_hot"] += int(hot)
            st["max_dup"] = max(st["max_dup"], view.dup_count)
            if view.dup_count == self.cfg["dup_evidence_min"] and allow_evidence(st, "dup"):
                out.append(self._dup_evidence(view))
        if view.global_dup_count > view.dup_count:
            st["n_global"] += 1
        if view.key not in st["keys"]:
            out += self._add_child(st, view)
        return out

    def _add_child(self, st: dict[str, Any], view: DetectorView) -> list[Evidence]:
        """Link a new key to its nearest known ancestor key. Report a new branch."""
        chain = chain_of(view)
        st["keys"].add(chain[-1])
        for i in range(len(chain) - 2, 0, -1):
            if chain[i] in st["keys"]:
                kids = st["children"].setdefault(chain[i], set())
                kids.add(chain[-1])
                if len(kids) == 2 and allow_evidence(st, "branch"):
                    return [new_evidence(
                        self.name, view, weight=0.5, message_index=i - 1,
                        summary=(f"The context that ends at message {i - 1} has 2 different "
                                 f"continuations in session {view.session_id}."))]
                return []
        return []

    def _dup_evidence(self, view: DetectorView) -> Evidence:
        n_msgs = len(view.messages)
        return new_evidence(
            self.name, view, message_index=n_msgs - 1 if n_msgs else None,
            summary=(f"The same context of {n_msgs} messages was sent {view.dup_count} times "
                     f"at temperature {view.temperature:.1f}."))

    def features(self, acct: AccountState) -> dict[str, float]:
        st = acct.scratch.get(self.name) or {}
        n = st.get("n", 0)
        kids = [len(k) for k in st.get("children", {}).values()]
        return {
            "d1_dup_rate": ratio(st.get("n_dup", 0), n),
            "d1_max_dup": float(st.get("max_dup", 0)),
            "d1_hot_dup_rate": ratio(st.get("n_hot", 0), n),
            "d1_branch_factor": ratio(sum(kids), len(kids)),
            "d1_branch_frac": ratio(sum(1 for k in kids if k >= 2), len(kids)),
            "d1_cross_account_dup_rate": ratio(st.get("n_global", 0), n),
        }
