"""Tests for the detector registry: run all detectors on the fixture scenario, check names, privacy, and speed."""
from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

from dw.common.schema import AccountState, DetectorView, Evidence, Message, RequestEvent
from dw.watchdog.detectors import build_detectors
from dw.watchdog.detectors.base import Detector, make_view

FIXTURES = Path(__file__).parent / "fixtures"
PREFIXES = ["d1_", "d2_", "d3_", "d4_", "d5_", "d6_", "d7_"]


def load_events() -> list[RequestEvent]:
    with open(FIXTURES / "scenario_small.jsonl", encoding="utf-8") as f:
        return [RequestEvent.model_validate_json(line) for line in f]


def load_labels() -> dict[str, dict[str, str]]:
    return json.loads((FIXTURES / "scenario_small_labels.json").read_text())


class SessionIndex:
    """Minimal session rebuild from the prefix chain (the worker owns the real one)."""

    def __init__(self) -> None:
        self.keys: dict[tuple[str, str], str] = {}
        self.steps: dict[str, int] = {}

    def view(self, ev: RequestEvent) -> DetectorView:
        sid = None
        for h in reversed(ev.prefix_hashes[1:]):
            sid = self.keys.get((ev.account_id, h))
            if sid:
                break
        new = sid is None
        sid = sid or f"s-{ev.request_id}"
        self.keys[(ev.account_id, ev.key)] = sid
        self.steps[sid] = self.steps.get(sid, -1) + 1
        return make_view(ev, sid, self.steps[sid], new)


def replay(events: list[RequestEvent], detectors: list[Detector]) -> tuple[dict[str, AccountState], list[Evidence]]:
    """Run all detectors on the events in order. Return account states and all evidence."""
    index, accounts, evidence = SessionIndex(), {}, []
    for ev in events:
        acct = accounts.setdefault(ev.account_id, AccountState(account_id=ev.account_id))
        view = index.view(ev)
        for det in detectors:
            evidence += det.observe(view, acct)
    return accounts, evidence


def all_features(detectors: list[Detector], acct: AccountState) -> dict[str, float]:
    out: dict[str, float] = {}
    for det in detectors:
        out |= det.features(acct)
    return out


def test_registry_names_and_feature_prefixes() -> None:
    dets = build_detectors(None)
    assert [d.name[:3] for d in dets] == [p for p in PREFIXES]
    accounts, evidence = replay(load_events(), dets)
    for acct in accounts.values():
        for det in dets:
            feats = det.features(acct)
            assert 3 <= len(feats) <= 8
            assert all(k.startswith(det.name[:3]) for k in feats)
            assert all(isinstance(v, float) for v in feats.values())
    assert all(e.summary.endswith(".") and e.request_ids for e in evidence)


def test_neutral_models_give_zero_model_features() -> None:
    dets = build_detectors(None)
    accounts, _ = replay(load_events(), dets)
    for acct in accounts.values():
        feats = all_features(dets, acct)
        assert feats["d3_topic_entropy"] == 0.0
        assert feats["d4_elicit_max"] == 0.0
        assert feats["d6_fake_prob_mean"] == 0.0


def test_attack_accounts_have_redundancy_and_fake_env_signal() -> None:
    dets = build_detectors(None)
    accounts, _ = replay(load_events(), dets)
    labels = load_labels()
    feats = {a: all_features(dets, s) for a, s in accounts.items()}
    by = lambda persona, k: [feats[a][k] for a, l in labels.items() if l["persona"] == persona]  # noqa: E731
    assert min(by("hydra", "d1_dup_rate")) > max(by("solo_dev", "d1_dup_rate"))
    assert min(by("fake_env", "d6_contradictions_per_100")) > 0
    assert max(by("solo_dev", "d6_contradictions_per_100")) == 0
    assert max(by("benchmark_runner", "d6_contradictions_per_100")) == 0
    assert min(by("solo_dev", "d2_followup_rate")) > max(by("hydra", "d2_followup_rate"))
    assert by("saas_app", "d7_distinct_users")[0] == 7.0


def test_detectors_do_not_read_assistant_prose() -> None:
    """The same event with and without a marker in assistant prose gives the same evidence and features."""
    marker = "SECRET-ASSISTANT-PROSE-7f3a"
    base = [Message(role="user", text="Fix parse() in <REPO>/a.py"),
            Message(role="assistant", text="PLACEHOLDER"),
            Message(role="tool_call", tool="write", args={"path": "<REPO>/a.py", "content": "x = 1"}),
            Message(role="tool_result", tool="write", text="ok"),
            Message(role="tool_call", tool="read", args={"path": "<REPO>/a.py"}),
            Message(role="tool_result", tool="read", text="x = 2")]

    def run(prose: str) -> tuple[list[str], dict[str, float]]:
        msgs = [m.model_copy(update={"text": prose}) if m.role == "assistant" else m for m in base]
        ev = RequestEvent(request_id="r1", account_id="a", sim_time=100.0, messages=msgs,
                          temperature=1.0, max_tokens=32768, reasoning_effort="high")
        dets, acct = build_detectors(None), AccountState(account_id="a")
        view = make_view(ev, "s1", 0, True)
        assert marker not in repr(view)
        summaries = [e.summary for d in dets for e in d.observe(view, acct)]
        return summaries, all_features(dets, acct)

    (summaries, feats), other = run(marker), run("Something else entirely.")
    assert (summaries, feats) == other
    assert any("x = 1" in s for s in summaries)       # the D6 contradiction is still found
    assert not any(marker in s for s in summaries)


def test_detector_view_has_no_private_fields() -> None:
    names = {f.name for f in dataclasses.fields(DetectorView)}
    assert not names & {"x_replay", "source", "label", "persona", "role_label", "session_ref"}
    for det in build_detectors(None):
        assert "observe" in dir(det) and "features" in dir(det)


def test_throughput_at_least_1000_events_per_second() -> None:
    events = load_events()
    replay(events, build_detectors(None))             # warm the embedding cache
    dets = build_detectors(None)
    index = SessionIndex()
    views = [(ev.account_id, index.view(ev)) for ev in events]
    accounts: dict[str, AccountState] = {}
    t0 = time.perf_counter()
    for aid, view in views:
        acct = accounts.setdefault(aid, AccountState(account_id=aid))
        for det in dets:
            det.observe(view, acct)
    rate = len(views) / (time.perf_counter() - t0)
    print(f"detector throughput: {rate:.0f} events/s")
    assert rate >= 1000
