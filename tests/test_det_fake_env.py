"""Tests for D6 (realism through the context model) and the shared queue of context-scored detectors."""
from __future__ import annotations

from dw.common.context import realism_text
from dw.common.hashing import prefix_chain
from dw.common.schema import AccountState, Message, RequestEvent
from dw.watchdog.detectors.base import make_view
from dw.watchdog.detectors.fake_env import FakeEnvDetector
from fake_scorer import FakeScorer

SESSION = [
    Message(role="user", text="Fix parse()"),
    Message(role="tool_call", tool="read", args={"path": "<REPO>/a.py"}),
    Message(role="tool_result", tool="read", text="def parse(): pass"),
    Message(role="tool_call", tool="shell", args={"command": "pytest -q"}),
    Message(role="tool_result", tool="shell", text="FAKE 3 passed in 0.01s"),
]


def feed(det: FakeEnvDetector, acct: AccountState, cuts=(3, 5)) -> list[str]:
    summaries = []
    for i, cut in enumerate(cuts):
        ctx = SESSION[:cut]
        chain = prefix_chain("standard_agent", ctx)
        ev = RequestEvent(request_id=f"r{i}", account_id="a", sim_time=float(i), messages=ctx,
                          prefix_hashes=chain, key=chain[-1])
        summaries += [e.summary for e in det.observe(make_view(ev, "s1", i, i == 0), acct)]
    return summaries


def test_cached_scores_give_features_and_evidence_at_once() -> None:
    scorer = FakeScorer()
    det, acct = FakeEnvDetector(scorer), AccountState(account_id="a")
    summaries = feed(det, acct)
    f = det.features(acct)
    assert abs(f["d6_fake_prob_mean"] - 0.5) < 1e-9 and f["d6_fake_high_frac"] == 0.5
    assert len(summaries) == 1 and "realism score 0.95" in summaries[0]
    assert scorer.batches == []                     # everything came from the cache


def test_text_contains_the_tool_call() -> None:
    assert realism_text("shell", {"command": "pytest -q"}, "ok") == "[tool] shell: pytest -q\nok"
    assert realism_text(None, None, "ok", has_call=False).startswith("[tool] unknown call")


def test_queue_scores_in_one_batch_and_drains_evidence() -> None:
    scorer = FakeScorer(cached=False)
    det, acct = FakeEnvDetector(scorer), AccountState(account_id="a")
    assert feed(det, acct) == []                    # nothing scored at observe time
    f = det.features(acct)                          # one batch for both tool outputs
    assert len(scorer.batches) == 1 and len(scorer.batches[0]) == 2
    assert abs(f["d6_fake_prob_mean"] - 0.5) < 1e-9
    late = det.drain_evidence()
    assert len(late) == 1 and late[0].account_id == "a" and det.drain_evidence() == []


def test_service_down_leaves_features_out_and_retries() -> None:
    scorer = FakeScorer(cached=False, up=False)
    det, acct = FakeEnvDetector(scorer), AccountState(account_id="a")
    feed(det, acct)
    assert det.features(acct) == {}                 # no score: the account model uses the training mean
    scorer.up = True
    assert abs(det.features(acct)["d6_fake_prob_mean"] - 0.5) < 1e-9    # the queue is scored on the next try


def test_no_scorer_is_neutral() -> None:
    det, acct = FakeEnvDetector(None), AccountState(account_id="a")
    assert feed(det, acct) == [] and det.features(acct) == {}
