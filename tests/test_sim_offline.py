"""Tests for offline mode: a stub lab and worker, and the real Lab when it exists."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dw.common.config import CFG
from dw.common.schema import RequestEvent
from dw.sim import offline
from dw.sim.scenario import Scenario, build_scenario

FIX = Path(__file__).parent / "fixtures" / "canonical_small"


@pytest.fixture(scope="module")
def scenario() -> Scenario:
    return build_scenario("holdout", 301, "small", personas=["solo_dev", "hydra"], data_dir=FIX)


class StubWorker:
    """Blocks one account after it sent `limit` requests."""

    def __init__(self, target: str, limit: int) -> None:
        self.target, self.limit = target, limit
        self.events: list[Any] = []
        self.rescores = 0
        self.campaign_runs = 0

    def ingest(self, ev: Any) -> None:
        self.events.append(ev)

    def blocklist(self) -> dict[str, dict[str, str]]:
        sent = sum(1 for e in self.events if e.account_id == self.target)
        return {self.target: {"reason": "test", "case_id": "c1"}} if sent >= self.limit else {}

    def rescore(self) -> list[dict]:
        self.rescores += 1
        return []

    def run_campaigns(self) -> list[dict]:
        self.campaign_runs += 1
        return []


class StubLab:
    def __init__(self, sink: Any, blocklist: Any) -> None:
        self.sink, self.blocklist = sink, blocklist
        self.registered: list[tuple] = []

    def register(self, account_id: str, declared_use: str | None, quota: int | None) -> None:
        self.registered.append((account_id, declared_use, quota))

    async def handle(self, key: str | None, body: dict, sim_time: float | None) -> tuple[int, dict]:
        if key in self.blocklist.get():
            return 403, {"error": "blocked"}
        body.pop("x_replay")
        self.sink.emit(RequestEvent(request_id="r", account_id=key, sim_time=sim_time))
        return 200, {}


def test_offline_with_stub_lab(scenario: Scenario, monkeypatch: pytest.MonkeyPatch) -> None:
    hydra = next(a for a, lab in scenario.labels.items() if lab["persona"] == "hydra")
    worker = StubWorker(hydra, limit=5)
    monkeypatch.setattr(offline, "_make_lab", lambda w: StubLab(offline.DirectSink(w), offline.DirectBlocklist(w)))
    result = offline.run_offline(scenario, worker)
    acct = result.accounts[hydra]
    assert acct["persona"] == "hydra" and acct["requests_before_block"] == 5
    assert acct["first_block_time"] is not None
    solo = [a for a, v in result.accounts.items() if v["persona"] == "solo_dev"]
    assert all(result.accounts[a]["first_block_time"] is None for a in solo)
    times = [r["sim_time"] for r in result.requests]
    assert times == sorted(times)
    assert worker.rescores == len(result.requests) // CFG["watchdog"]["offline_rescore_every"] + 1
    assert result.counters["hydra"]["blocked"] > 0
    assert all("x_replay" in r.body for r in scenario.requests)   # the lab got a copy of each body


def test_offline_with_real_lab(scenario: Scenario) -> None:
    pytest.importorskip("dw.lab.gateway")
    hydra = next(a for a, lab in scenario.labels.items() if lab["persona"] == "hydra")
    worker = StubWorker(hydra, limit=5)
    result = offline.run_offline(scenario, worker)
    assert result.accounts[hydra]["first_block_time"] is not None
    assert result.counters["solo_dev"]["blocked"] == 0
    for ev in worker.events:
        assert "x_replay" not in ev.model_dump_json()
