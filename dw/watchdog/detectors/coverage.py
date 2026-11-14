"""D3 coverage: the spread of an account over repositories and task topics.

The API has no repository field. Sessions that touch similar file paths belong to the same repository.
A distiller covers many repositories and topics. A developer focuses on few.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from dw.common.embed import embed
from dw.common.schema import AccountState, DetectorView, Evidence
from dw.watchdog.detectors.common import (
    DCFG, first_user_text, fresh_messages, message_paths, norm_entropy, ratio, repo_clusters, scratch,
)


def track_session(st: dict[str, Any], acct: AccountState, view: DetectorView) -> tuple[dict[str, Any], bool]:
    """Add the new file paths of the view to its session record. Return (record, is_new_session)."""
    sessions = st.setdefault("sessions", {})
    is_new = view.session_id not in sessions
    rec = sessions.setdefault(view.session_id, {"paths": set(), "start": view.sim_time})
    fresh, _ = fresh_messages(acct, view)
    for m in fresh:
        rec["paths"] |= message_paths(m)
    return rec, is_new


def session_repos(st: dict[str, Any]) -> tuple[list[dict[str, Any]], list[int]]:
    """Return (session records, repository index of each record) for one detector state."""
    recs = list(st.get("sessions", {}).values())
    return recs, repo_clusters([r["paths"] for r in recs], DCFG["d3"]["repo_jaccard"])


class CoverageDetector:
    name = "d3_coverage"

    def __init__(self, topic_model: Any | None = None) -> None:
        self.topic_model = topic_model

    def observe(self, view: DetectorView, acct: AccountState) -> list[Evidence]:
        st = scratch(acct, self.name)
        rec, is_new = track_session(st, acct, view)
        if is_new and self.topic_model is not None:
            text = first_user_text(view)
            rec["topic"] = int(self.topic_model.predict(embed([text]))[0]) if text else None
        return []

    def features(self, acct: AccountState) -> dict[str, float]:
        st = acct.scratch.get(self.name) or {}
        recs, labels = session_repos(st)
        n = len(recs)
        repo_counts = list(Counter(labels).values())
        topics = [r["topic"] for r in recs if r.get("topic") is not None]
        k = DCFG["d3"]["kmeans_k"]
        return {
            "d3_distinct_repos": float(len(repo_counts)),
            "d3_repo_entropy": norm_entropy(repo_counts, n),
            "d3_sessions_per_repo": ratio(n, len(repo_counts)),
            "d3_topic_entropy": norm_entropy(list(Counter(topics).values()), min(k, len(topics))),
        }
