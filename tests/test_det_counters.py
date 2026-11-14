"""Tests for D1 redundancy, D2 engagement, D3 repository identity, D7 volume, and the evidence cap."""
from __future__ import annotations

from dw.common.hashing import prefix_chain
from dw.common.schema import AccountState, Message, RequestEvent
from dw.watchdog.detectors.base import make_view
from dw.watchdog.detectors.common import DCFG, allow_evidence, repo_clusters
from dw.watchdog.detectors.engagement import EngagementDetector, is_correction
from dw.watchdog.detectors.redundancy import RedundancyDetector
from dw.watchdog.detectors.volume import VolumeDetector

USER = Message(role="user", text="fix parse")


def event(rid: str, msgs: list[Message], dup: int = 1, t: float = 0.0, **kw: object) -> RequestEvent:
    chain = prefix_chain("standard_agent", msgs)
    return RequestEvent(request_id=rid, account_id="a", sim_time=t, messages=msgs, prefix_hashes=chain,
                        key=chain[-1], dup_count=dup, **kw)  # type: ignore[arg-type]


def step(path: str) -> list[Message]:
    return [Message(role="tool_call", tool="read", args={"path": path}),
            Message(role="tool_result", tool="read", text="x")]


def test_d1_duplicates_and_branches() -> None:
    det, acct = RedundancyDetector(), AccountState(account_id="a")
    evidence = []
    for i in range(1, 6):                               # best-of-5 of the first state
        evidence += det.observe(make_view(event(f"d{i}", [USER], dup=i, temperature=1.0), "s", i, i == 1), acct)
    for j, path in enumerate(["<REPO>/a.py", "<REPO>/b.py"]):   # two different continuations
        evidence += det.observe(make_view(event(f"c{j}", [USER] + step(path)), "s", 9, False), acct)
    f = det.features(acct)
    assert f["d1_max_dup"] == 5.0 and f["d1_dup_rate"] == 4 / 7 and f["d1_hot_dup_rate"] == 4 / 7
    assert f["d1_branch_frac"] == 1.0 and f["d1_branch_factor"] == 2.0
    assert [e.summary for e in evidence] == [
        "The same context of 1 messages was sent 3 times at temperature 1.0.",
        "The context that ends at message 0 has 2 different continuations in session s."]


def test_d2_followups_corrections_and_return() -> None:
    assert is_correction("no, that's wrong, the tests still fail")
    assert not is_correction("please add a docstring to the parser module")
    det, acct = EngagementDetector(), AccountState(account_id="a")
    s1 = [USER] + step("<REPO>/a.py") + [Message(role="assistant", text="")]
    det.observe(make_view(event("r1", s1, finish_reason="stop"), "s1", 0, True), acct)
    s1b = s1 + [Message(role="user", text="that did not work, the error is still there")]
    det.observe(make_view(event("r2", s1b, t=60.0, finish_reason="stop"), "s1", 1, False), acct)
    s2 = [Message(role="user", text="next")] + step("<REPO>/a.py")
    det.observe(make_view(event("r3", s2, t=2 * 86400.0, finish_reason="stop"), "s2", 0, True), acct)
    f = det.features(acct)
    assert f["d2_followup_rate"] == 0.5 and f["d2_correction_rate"] == 0.5
    assert f["d2_user_turns_per_session"] == 1.5 and f["d2_return_rate"] == 1.0
    assert f["d2_stop_end_frac"] == 1.0


def test_repo_clusters_by_path_jaccard() -> None:
    labels = repo_clusters([{"a.py", "b.py"}, {"a.py", "b.py", "c.py"}, {"x/y.py"}, set(), set()], 0.2)
    assert labels[0] == labels[1] and len(set(labels)) == 4


def test_d7_volume_counters() -> None:
    det, acct = VolumeDetector(), AccountState(account_id="a")
    for i in range(10):
        ev = event(f"r{i}", [USER], t=i * 60.0, user=f"u{i % 3}")
        det.observe(make_view(ev, f"s{i % 4}", 0, True), acct)
    f = det.features(acct)
    assert f["d7_req_per_active_hour"] == 10.0 and f["d7_distinct_users"] == 3.0
    assert f["d7_user_field_frac"] == 1.0 and f["d7_max_concurrent_sessions"] == 4.0
    assert f["d7_active_hours_per_day"] == 1.0


def test_evidence_cap() -> None:
    st: dict = {}
    allowed = [allow_evidence(st, "k") for _ in range(100)]
    first, every = DCFG["evidence_first"], DCFG["evidence_every"]
    assert sum(allowed) == first + 100 // every
