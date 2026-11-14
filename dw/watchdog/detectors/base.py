"""The Detector interface and the function that builds the DetectorView of an event.

Detectors see only the DetectorView: no assistant prose, no `x_replay`, no `source`, no label.
"""
from __future__ import annotations

import itertools
from typing import Protocol

from dw.common.schema import AccountState, DetectorView, Evidence, RequestEvent, ViewMessage

_evidence_ids = itertools.count(1)


class Detector(Protocol):
    name: str

    def observe(self, ev: DetectorView, acct: AccountState) -> list[Evidence]:
        """Update per-account state for one request. Return new evidence. Must be cheap."""
        ...

    def features(self, acct: AccountState) -> dict[str, float]:
        """Return account-level features. Names are prefixed with the detector name."""
        ...


def make_view(ev: RequestEvent, session_id: str, step: int, new_session: bool) -> DetectorView:
    """Build the detector view of an event. Assistant prose becomes an empty string."""
    messages = [
        ViewMessage(index=i, role=m.role, text="" if m.role == "assistant" else m.text,
                    tool=m.tool, args=m.args)
        for i, m in enumerate(ev.messages)
    ]
    return DetectorView(
        request_id=ev.request_id, account_id=ev.account_id, sim_time=ev.sim_time,
        tools_schema_id=ev.tools_schema_id, temperature=ev.temperature, n=ev.n,
        max_tokens=ev.max_tokens, reasoning_effort=ev.reasoning_effort, user=ev.user,
        messages=messages, prefix_hashes=ev.prefix_hashes, key=ev.key,
        dup_count=ev.dup_count, global_dup_count=ev.global_dup_count,
        finish_reason=ev.finish_reason, session_id=session_id, step=step, new_session=new_session,
    )


def new_evidence(detector: str, view: DetectorView, summary: str, weight: float = 1.0,
                 message_index: int | None = None, request_ids: list[str] | None = None) -> Evidence:
    """Make an Evidence item with a unique ID."""
    return Evidence(
        id=f"ev-{next(_evidence_ids):07d}", detector=detector, account_id=view.account_id,
        request_ids=request_ids or [view.request_id], message_index=message_index,
        summary=summary, weight=weight, sim_time=view.sim_time,
    )
