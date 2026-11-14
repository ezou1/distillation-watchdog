"""Tests for D6: shadow file system replay on crafted sessions and on real Open-SWE sessions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dw.common.hashing import prefix_chain
from dw.common.schema import AccountState, Message, RequestEvent
from dw.watchdog.detectors.base import make_view
from dw.watchdog.detectors.fake_env import FakeEnvDetector

FIXTURES = Path(__file__).parent / "fixtures"
P = "<REPO>/foo.py"


def call(tool: str, **args: str) -> Message:
    return Message(role="tool_call", tool=tool, args=args)  # type: ignore[arg-type]


def result(tool: str, text: str) -> Message:
    return Message(role="tool_result", tool=tool, text=text)  # type: ignore[arg-type]


def run_session(msgs: list[Message], cuts: list[int] | None = None) -> tuple[list[str], dict[str, float]]:
    """Feed the session as growing requests (one per cut). Return evidence summaries and features."""
    det, acct = FakeEnvDetector(), AccountState(account_id="a")
    summaries: list[str] = []
    for i, cut in enumerate(cuts or [len(msgs)]):
        ctx = msgs[:cut]
        chain = prefix_chain("standard_agent", ctx)
        ev = RequestEvent(request_id=f"r{i}", account_id="a", sim_time=float(i), messages=ctx,
                          prefix_hashes=chain, key=chain[-1])
        summaries += [e.summary for e in det.observe(make_view(ev, "s1", i, i == 0), acct)]
    return summaries, det.features(acct)


def test_consistent_session_has_no_contradiction() -> None:
    msgs = [Message(role="user", text="fix"),
            call("write", path=P, content="x = 1\ny = 2\n"), result("write", "File written."),
            call("read", path=P), result("read", "x = 1\n   y = 2"),
            call("edit", path=P, old="y = 2", new="y = 3"), result("edit", "Edited."),
            call("shell", command=f"cat {P}"), result("shell", "x = 1\ny = 3\n"),
            call("shell", command=f"head -n 1 {P}"), result("shell", "x = 1"),
            call("shell", command="python -m pytest -q"), result("shell", "collected 3 items\n\n== 2 passed, 1 skipped in 0.1s ==")]
    summaries, feats = run_session(msgs, cuts=[3, 5, 9, len(msgs)])
    assert summaries == []
    assert feats["d6_contradictions_per_100"] == 0.0


def test_write_then_read_contradiction() -> None:
    msgs = [Message(role="user", text="fix"),
            call("write", path=P, content="x = 1\n"), result("write", "ok"),
            call("shell", command=f"cat {P}"), result("shell", "x = 2\n")]
    summaries, feats = run_session(msgs, cuts=[3, 5])
    assert summaries == [f"Message 1 wrote `x = 1` to `{P}`. Message 4 `cat` returned a file without this line."]
    assert feats["d6_contradictions_per_100"] == 50.0


def test_deleted_file_comes_back() -> None:
    msgs = [Message(role="user", text="clean up"),
            call("write", path=P, content="x = 1"), result("write", "ok"),
            call("shell", command=f"rm -f {P}"), result("shell", ""),
            call("read", path=P), result("read", "x = 1")]
    summaries, _ = run_session(msgs)
    assert len(summaries) == 1 and "deleted" in summaries[0] and "Message 6" in summaries[0]


def test_failed_read_of_deleted_file_is_consistent() -> None:
    msgs = [Message(role="user", text="clean up"),
            call("shell", command=f"rm {P}"), result("shell", ""),
            call("read", path=P), result("read", "Error: file does not exist.")]
    assert run_session(msgs)[0] == []


def test_wrong_pytest_count() -> None:
    msgs = [Message(role="user", text="test"),
            call("shell", command="pytest"), result("shell", "collected 5 items\n\n=== 3 passed, 1 failed in 0.2s ===")]
    summaries, _ = run_session(msgs)
    assert summaries == ["Message 2 shows pytest `collected 5 items`, but the summary line counts 4 tests."]


def test_edit_not_applied() -> None:
    msgs = [Message(role="user", text="fix"),
            call("edit", path=P, old="return None", new="return []"), result("edit", "The file was updated."),
            call("read", path=P), result("read", "def f():\n    return None\n")]
    assert len(run_session(msgs)[0]) == 1


def test_mutating_shell_command_forgets_contents() -> None:
    msgs = [Message(role="user", text="fix"),
            call("write", path=P, content="x = 1"), result("write", "ok"),
            call("shell", command=f"sed -i 's/1/2/' {P}"), result("shell", ""),
            call("shell", command=f"cat {P}"), result("shell", "x = 2")]
    assert run_session(msgs)[0] == []


def test_branch_replays_without_duplicate_evidence() -> None:
    """A second branch that shares the contradiction prefix reports no new evidence for it."""
    common = [Message(role="user", text="fix"),
              call("write", path=P, content="x = 1"), result("write", "ok"),
              call("read", path=P), result("read", "x = 9")]
    a = common + [call("shell", command="ls"), result("shell", "foo.py")]
    b = common + [call("shell", command="pwd"), result("shell", "<REPO>")]
    det, acct = FakeEnvDetector(), AccountState(account_id="a")
    n = 0
    for i, ctx in enumerate([a, b]):
        chain = prefix_chain("standard_agent", ctx)
        ev = RequestEvent(request_id=f"r{i}", account_id="a", sim_time=0.0, messages=ctx,
                          prefix_hashes=chain, key=chain[-1])
        n += len(det.observe(make_view(ev, "s1", i, i == 0), acct))
    assert n == 1


def _openswe_sessions() -> list[list[Message]]:
    try:
        from dw.data.normalize import openswe_session
    except ImportError:
        pytest.skip("dw.data.normalize is not importable yet (WS-A)")
    out = []
    with open(FIXTURES / "openswe_claudecode_sample.jsonl", encoding="utf-8") as f:
        for line in f:
            s = openswe_session(json.loads(line), "openswe")
            if s is not None:
                out.append(s.messages)
    return out


def test_real_openswe_sessions_have_zero_contradictions() -> None:
    sessions = _openswe_sessions()
    assert sessions
    for msgs in sessions:
        cuts = [i + 1 for i, m in enumerate(msgs) if m.role == "tool_result"]
        summaries, _ = run_session(msgs, cuts=cuts)
        assert summaries == [], summaries


def test_realism_batch_equals_single_scores() -> None:
    """Queued tool outputs are scored in one batch. The account mean must equal one-by-one scoring."""
    class Model:
        calls = 0

        def predict_proba(self, texts):
            import numpy as np
            Model.calls += 1
            p = np.array([min(1.0, len(t) / 100) for t in texts])
            return np.stack([1 - p, p], axis=1)

    det = FakeEnvDetector(Model())
    st = {"prob_sum": 0.0, "n_scored": 0}
    texts = ["a" * 10, "b" * 50, "a" * 10, "c" * 80]
    for t in texts:
        det._queue(st, t)
    det._flush()
    assert Model.calls == 1
    assert st["n_scored"] == 4
    assert abs(st["prob_sum"] - sum(det.fake_prob(t) for t in texts)) < 1e-12
    assert abs(st["prob_sum"] - (0.1 + 0.5 + 0.1 + 0.8)) < 1e-12
