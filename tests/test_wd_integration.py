"""Integration: the fixture scenario through a Worker, an account model fit on its features, rescore, block list.

It uses the real detectors if WS-C1 has built them, else the stub detector.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from dw.watchdog import account_model as am
from dw.watchdog.store import EventStore
from dw.watchdog.worker import Worker
from test_wd_stubs import StubDetector, load_fixture, real_detectors


def _detectors(models: Path, real: bool) -> list:
    if real:
        dets = real_detectors(models)
        if dets is None:
            pytest.skip("build_detectors is not available yet")
        return dets
    return [StubDetector()]


def _labels01(labels: dict, ids: list[str]) -> list[int]:
    return [int(labels[a]["role"] == "attack") for a in ids]


@pytest.mark.parametrize("real", [False, True], ids=["stub", "real"])
def test_fixture_pipeline(tmp_path: Path, real: bool) -> None:
    events, labels = load_fixture()
    probe = Worker(models_dir=tmp_path, score=False, detectors=_detectors(tmp_path, real))
    for ev in events:
        probe.ingest(ev)
    table = probe.feature_table()
    ids = sorted(table)
    rows = [table[a] for a in ids]
    model = am.fit(rows, _labels01(labels, ids))
    am.set_thresholds(model, rows, _labels01(labels, ids))
    am.save(model, tmp_path / am.ACCOUNT_MODEL_FILE)

    worker = Worker(models_dir=tmp_path, store=EventStore(None), detectors=_detectors(tmp_path, real))
    assert worker.score
    events_out = []
    for i, ev in enumerate(events):
        worker.ingest(ev)
        if i % 200 == 199:
            events_out += worker.rescore()
    events_out += worker.rescore()
    events_out += worker.run_campaigns()
    blocked = set(worker.blocklist())
    attacks = {a for a, lab in labels.items() if lab["role"] == "attack" and worker.accounts[a].n_requests >= 30}
    normals = {a for a, lab in labels.items() if lab["role"] == "normal"}
    assert len(blocked & attacks) >= len(attacks) // 2
    assert not blocked & normals
    assert any(e["type"] == "block" for e in events_out)
    for e in events_out:
        assert e["type"] in ("flag", "block", "campaign")
    acct = worker.accounts[next(iter(blocked))]
    assert acct.contributions and acct.score_history and acct.state == "block"
    assert not any(a.dirty for a in worker.accounts.values())


def test_ingest_throughput() -> None:
    events, _ = load_fixture()
    real = real_detectors(Path("models"))
    worker = Worker(score=False, detectors=real if real is not None else [StubDetector()], store=EventStore(None))
    start = time.perf_counter()
    for ev in events:
        worker.ingest(ev)
    rate = len(events) / (time.perf_counter() - start)
    assert rate >= 500, f"{rate:.0f} events/s"
