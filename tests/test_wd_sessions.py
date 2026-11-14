"""Tests for session rebuild from the prefix hash chain and the worker counters."""
from __future__ import annotations

from dw.common.schema import Message
from dw.watchdog.store import EventStore
from dw.watchdog.worker import Worker
from test_wd_stubs import StubDetector, make_event

U1 = Message(role="user", text="Fix parse()")
A1 = Message(role="assistant", text="Let me look.")
C1 = Message(role="tool_call", tool="read", args={"path": "<REPO>/a.py"})
R1 = Message(role="tool_result", tool="read", text="def parse(): pass")
C2 = Message(role="tool_call", tool="shell", args={"command": "ls"})
R2 = Message(role="tool_result", tool="shell", text="a.py")


class Recorder:
    """Detector that records the view of each request."""

    name = "dx_rec"

    def __init__(self) -> None:
        self.views: list = []

    def observe(self, view, acct):
        self.views.append(view)
        return []

    def features(self, acct):
        return {}


def _worker() -> tuple[Worker, Recorder]:
    rec = Recorder()
    return Worker(score=False, detectors=[rec], store=EventStore(None)), rec


def test_continuation_duplicate_branch_and_new_session() -> None:
    w, rec = _worker()
    w.ingest(make_event("r1", "a", [U1], 0))
    w.ingest(make_event("r2", "a", [U1, A1, C1, R1], 10))       # continues r1
    w.ingest(make_event("r3", "a", [U1, A1, C1, R1], 11, dup_count=2))  # duplicate of r2
    w.ingest(make_event("r4", "a", [U1, A1, C2, R2], 12))       # branch from r1
    w.ingest(make_event("r5", "a", [Message(role="user", text="Other task")], 20))  # new session
    w.ingest(make_event("r6", "b", [U1], 21))                   # same key, other account: new session
    sids = [v.session_id for v in rec.views]
    assert sids[:4] == [sids[0]] * 4
    assert sids[4] != sids[0]
    assert [v.new_session for v in rec.views] == [True, False, False, False, True, True]
    assert [v.step for v in rec.views[:4]] == [0, 1, 2, 3]
    acct = w.accounts["a"]
    assert len(acct.sessions) == 2 and acct.n_requests == 5
    assert acct.keys[rec.views[1].key] == sids[0]


def test_longest_prefix_wins() -> None:
    w, rec = _worker()
    w.ingest(make_event("r1", "a", [U1], 0))
    w.ingest(make_event("r2", "a", [Message(role="user", text="Second task")], 1))
    w.ingest(make_event("r3", "a", [U1, A1, C1, R1], 2))
    w.ingest(make_event("r4", "a", [U1, A1, C1, R1, A1, C2, R2], 3))
    assert rec.views[3].session_id == rec.views[0].session_id
    assert rec.views[1].session_id != rec.views[0].session_id


def test_blocked_request_counts_but_is_not_observed() -> None:
    w, rec = _worker()
    w.ingest(make_event("r1", "a", [U1], 0))
    w.ingest(make_event("r2", "a", [U1, A1, C1, R1], 1, status=403, finish_reason=None))
    assert len(rec.views) == 1
    assert w.served == 1 and w.blocked == 1 and w.accounts["a"].n_blocked == 1
    assert w.rate_history()[0]["served"] == 1 and w.rate_history()[0]["blocked"] == 1


def test_evidence_is_indexed_and_request_features() -> None:
    w = Worker(score=False, detectors=[StubDetector()], store=EventStore(None), record_requests=True)
    for i in range(3):
        w.ingest(make_event(f"r{i}", "a", [U1], i, dup_count=i + 1, temperature=1.0, user="u1"))
    ev = w.accounts["a"].evidence
    assert len(ev) == 1 and w.evidence_index[ev[0].id] is ev[0]
    row = w.request_rows[-1][2]
    assert row == {"req_dup_count": 3.0, "req_temperature": 1.0, "req_n": 1.0, "req_max_tokens": 0.0,
                   "req_elicitation": 0.5, "req_user_present": 1.0}
    assert set(w.feature_table()["a"]) >= {"dx_dup_rate", "dx_hot_frac"}
