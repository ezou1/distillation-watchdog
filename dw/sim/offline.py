"""Run a scenario through the lab core and a watchdog worker in one process, without HTTP.

The training and the offline evaluation use this. It calls `Lab.handle()` for each request in time order
and gives the events straight to the worker.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from dw.common.config import CFG
from dw.common.sink import DirectBlocklist, DirectSink
from dw.sim.scenario import Scenario
from dw.sim.sender import Counters


@dataclass
class OfflineResult:
    requests: list[dict[str, Any]] = field(default_factory=list)    # account, persona, status, sim_time
    accounts: dict[str, dict[str, Any]] = field(default_factory=dict)  # persona, role, first_block_time, requests_before_block
    counters: dict[str, dict[str, int]] = field(default_factory=dict)  # per persona, as in the generators


def _make_lab(worker: Any) -> Any:
    from dw.lab.gateway import Lab
    return Lab(sink=DirectSink(worker), blocklist=DirectBlocklist(worker), replay_memo=True)


async def _run(scenario: Scenario, worker: Any) -> OfflineResult:
    lab = _make_lab(worker)
    for reg in scenario.registrations:
        lab.register(reg["account_id"], reg.get("declared_use"), reg.get("quota"))
    rescore_every = CFG["watchdog"]["offline_rescore_every"]
    campaign_every = CFG["watchdog"]["offline_campaign_every"]
    counters = Counters()
    result = OfflineResult(accounts={a: {**label, "first_block_time": None, "requests_before_block": 0}
                                     for a, label in scenario.labels.items()})
    handled = 0
    for r in scenario.requests:
        if counters.skip(r):
            continue
        counters.sent(r)
        status, _ = await lab.handle(r.key, dict(r.body), r.sim_time)
        counters.record(r, status)
        _account_update(result.accounts[r.key], status, r.sim_time)
        result.requests.append({"account": r.key, "persona": r.persona, "status": status, "sim_time": r.sim_time,
                                "final": r.final, "session": r.session_ref})
        handled += 1
        if handled % rescore_every == 0:
            worker.rescore()
        if handled % campaign_every == 0:
            worker.run_campaigns()
    worker.rescore()
    worker.run_campaigns()
    worker.rescore()
    result.counters = counters.snapshot()
    return result


def _account_update(acct: dict[str, Any], status: int, sim_time: float) -> None:
    if acct["first_block_time"] is not None:
        return
    if status == 403:
        acct["first_block_time"] = sim_time
    else:
        acct["requests_before_block"] += 1


def prefetch_context(scenario: Scenario, worker: Any) -> None:
    """Score all context-model texts of the scenario in large batches before the replay.

    The detectors then find each score in the cache, so the replay makes no call per message.
    """
    scorers = {id(s): s for d in getattr(worker, "detectors", []) if (s := getattr(d, "scorer", None)) is not None}
    if not scorers:
        return
    from dw.common.chat import from_openai
    from dw.common.context import session_texts
    longest: dict[str, list] = {}
    for r in scenario.requests:
        msgs = r.body["messages"]
        if len(msgs) > len(longest.get(r.session_ref, ())):
            longest[r.session_ref] = msgs
    texts = [t for msgs in longest.values() for t in session_texts(from_openai(msgs))]
    for s in scorers.values():
        s.score_many(texts)
        s.save()


def run_offline(scenario: Scenario, worker: Any) -> OfflineResult:
    """Replay the scenario through `Lab.handle()` with DirectSink(worker) and DirectBlocklist(worker)."""
    prefetch_context(scenario, worker)
    result = asyncio.run(_run(scenario, worker))
    for d in getattr(worker, "detectors", []):
        if (s := getattr(d, "scorer", None)) is not None:
            s.save()
    return result
