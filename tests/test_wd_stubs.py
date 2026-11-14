"""Shared helpers for the watchdog tests: a small stub detector, event builders, and the fixture scenario."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dw.common.hashing import prefix_chain
from dw.common.schema import AccountState, DetectorView, Evidence, Message, RequestEvent
from dw.watchdog.detectors.base import new_evidence

FIXTURES = Path(__file__).parent / "fixtures"


class StubDetector:
    """Request-parameter features and a duplicate evidence item. Enough to separate the fixture personas."""

    name = "dx_stub"

    def observe(self, view: DetectorView, acct: AccountState) -> list[Evidence]:
        st = acct.scratch.setdefault(self.name, {"n": 0, "dup": 0, "hot": 0, "max_tok": 0, "temp": 0.0, "user": 0})
        st["n"] += 1
        st["dup"] += view.dup_count > 1
        st["hot"] += view.temperature >= 0.9
        st["max_tok"] += (view.max_tokens or 0) >= 32768
        st["temp"] += view.temperature
        st["user"] += bool(view.user)
        if view.dup_count == 3:
            return [new_evidence(self.name, view, f"The same context was sent {view.dup_count} times at "
                                 f"temperature {view.temperature:.1f}.", message_index=len(view.messages) - 1)]
        return []

    def features(self, acct: AccountState) -> dict[str, float]:
        st = acct.scratch.get(self.name)
        if not st:
            return {}
        n = st["n"]
        return {"dx_dup_rate": st["dup"] / n, "dx_hot_frac": st["hot"] / n, "dx_max_tokens_frac": st["max_tok"] / n,
                "dx_mean_temp": st["temp"] / n, "dx_user_frac": st["user"] / n}

    def request_score(self, view: DetectorView) -> float:
        return 0.5


def make_event(rid: str, account: str, messages: list[Message], t: float = 0.0, schema: str = "standard_agent",
               **kw: Any) -> RequestEvent:
    chain = prefix_chain(schema, messages)
    return RequestEvent(request_id=rid, account_id=account, sim_time=t, tools_schema_id=schema,
                        messages=messages, prefix_hashes=chain, key=chain[-1], **kw)


def load_fixture() -> tuple[list[RequestEvent], dict[str, dict[str, str]]]:
    lines = (FIXTURES / "scenario_small.jsonl").read_text().splitlines()
    events = sorted((RequestEvent.model_validate_json(line) for line in lines if line.strip()),
                    key=lambda e: e.sim_time)
    labels = json.loads((FIXTURES / "scenario_small_labels.json").read_text())
    return events, labels


def real_detectors(models_dir: Path) -> list | None:
    """The real detectors if WS-C1 has built them, else None."""
    try:
        from dw.watchdog.detectors import build_detectors  # type: ignore[attr-defined]
    except ImportError:
        return None
    return list(build_detectors(models_dir))
