"""Tests for the rules score (D1 with the D7 exoneration), the policy with three levels, and snapshot rows."""
from __future__ import annotations

from pathlib import Path

from dw.common.config import CFG
from dw.watchdog import account_model as am, rules
from dw.watchdog.policy import decide
from dw.watchdog.store import EventStore
from dw.watchdog.worker import Worker
from test_wd_stubs import StubDetector, load_fixture

THR = {"block": 0.5, "review": 0.3}
MANY_USERS = float(CFG["rules"]["exonerate_min_users"])


def _f(dup: float, users: float = 0.0) -> dict[str, float]:
    return {CFG["rules"]["feature"]: dup, "d7_distinct_users": users}


def test_rules_level_and_exoneration() -> None:
    assert rules.level(_f(0.1), THR, None) == "normal"
    assert rules.level(_f(0.35), THR, None) == "review"
    assert rules.level(_f(0.8), THR, None) == "block"
    assert rules.level(_f(0.8, MANY_USERS), THR, None) == "review"      # an application with end users
    assert rules.level(_f(0.8), THR, "benchmark") == "review"           # a declared use
    assert rules.level(_f(0.8), None, None) == "normal"                 # no thresholds: no rules level


def test_rules_thresholds_round_trip(tmp_path: Path) -> None:
    values, labels = [0.0, 0.05, 0.1, 0.2] * 30 + [0.6, 0.8], [0] * 120 + [1, 1]
    thr = rules.fit_thresholds(values, labels)
    assert 0.2 < thr["block"] <= 0.6 and thr["review"] <= thr["block"]
    rules.save(thr, tmp_path)
    assert rules.load(tmp_path) == thr and rules.load(tmp_path / "missing") is None


def test_policy_takes_the_most_severe_level() -> None:
    model_thr = {"block": 0.9, "review": 0.5}
    assert decide(100, 0.1, model_thr, rules_level="block") == "block"
    assert decide(100, 0.95, model_thr, rules_level="normal") == "block"
    assert decide(100, 0.1, model_thr, rules_level="review") == "review"
    assert decide(100, 0.1, model_thr, campaign_state="block", rules_level="normal") == "block"
    assert decide(10, 0.1, model_thr, rules_level=None) == "insufficient"
    # a missing model feature (no context-model service): the model alone can only flag for review
    assert decide(100, 0.95, model_thr, rules_level="normal", model_complete=False) == "review"
    assert decide(100, 0.95, model_thr, rules_level="block", model_complete=False) == "block"


def test_worker_snapshots_at_request_counts() -> None:
    events, _ = load_fixture()
    worker = Worker(score=False, detectors=[StubDetector()], store=EventStore(None), snapshots=True)
    for ev in events:
        worker.ingest(ev)
    at = set(CFG["account"]["snapshot_requests"])
    assert worker.snapshots and all(n in at for _, n, _ in worker.snapshots)
    for aid, n, feats in worker.snapshots:
        assert worker.accounts[aid].n_requests >= n and feats
    plain = Worker(score=False, detectors=[StubDetector()], store=EventStore(None))
    plain.ingest(events[0])
    assert plain.snapshots == []


def test_weighted_fit_and_model_groups(monkeypatch) -> None:
    monkeypatch.setitem(CFG["account"], "model_groups", ["d2", "d4"])
    rows = [{"d1_dup_rate": float(i % 2), "d2_followup_rate": float((i // 2) % 2) + 0.01 * i} for i in range(80)]
    labels = [i % 2 for i in range(80)]
    names = am.model_names(rows)
    assert "d1_dup_rate" not in names and "d2_followup_rate" in names
    model = am.fit(rows, labels, names=names, weights=[1.0] * 40 + [0.5] * 40)
    assert model.names == ["d2_followup_rate"]
