"""Tests for the watchdog HTTP API (docs/API.md section 4) with the fixture scenario and stub detectors."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dw.common.config import CFG
from dw.watchdog import account_model as am
from dw.watchdog.server import create_app, sse
from dw.watchdog.store import EventStore
from dw.watchdog.worker import Worker
from test_wd_stubs import StubDetector, load_fixture

PERSONA_STRINGS = ("solo_dev", "saas_app", "benchmark_runner", "hydra", "fake_env", "stealth",
                   "persona", "swechat", "openswe", "\"source\"")


@pytest.fixture(scope="module")
def fed(tmp_path_factory: pytest.TempPathFactory) -> tuple[TestClient, Worker]:
    """A server whose worker has the fixture scenario, a fitted model, rescoring, and campaigns."""
    events, labels = load_fixture()
    models = tmp_path_factory.mktemp("models")
    probe = Worker(models_dir=models, score=False, detectors=[StubDetector()])
    for ev in events:
        probe.ingest(ev)
    table = probe.feature_table()
    ids = sorted(table)
    model = am.fit([table[a] for a in ids], [int(labels[a]["role"] == "attack") for a in ids])
    am.set_thresholds(model, [table[a] for a in ids], [int(labels[a]["role"] == "attack") for a in ids])
    am.save(model, models / am.ACCOUNT_MODEL_FILE)
    worker = Worker(models_dir=models, store=EventStore(None), detectors=[StubDetector()])
    for ev in events:
        worker.ingest(ev)
    worker.rescore()
    worker.run_campaigns()
    reports = tmp_path_factory.mktemp("reports")
    (reports / "metrics.json").write_text(json.dumps({"auc": 0.9}))
    (reports / "roc_pr.png").write_bytes(b"\x89PNG fake")
    return TestClient(create_app(worker, reports_dir=reports)), worker


def test_summary_and_blocklist(fed) -> None:
    client, worker = fed
    s = client.get("/api/summary").json()
    assert set(s) == {"sim_time", "rps", "served", "blocked", "accounts", "campaigns", "queue_depth", "dropped",
                      "rate_history"}
    assert set(s["accounts"]) == {"insufficient", "normal", "review", "block", "disclosed"}
    assert sum(s["accounts"].values()) == 20 and s["served"] == 1575
    assert set(s["rate_history"][0]) == {"t", "served", "blocked"}
    bl = client.get("/blocklist").json()
    assert bl["version"] >= 1 and bl["accounts"]
    assert all(set(v) == {"reason", "case_id"} for v in bl["accounts"].values())


def test_accounts_never_show_labels(fed) -> None:
    client, _ = fed
    body = client.get("/api/accounts?limit=100").text
    for word in PERSONA_STRINGS:
        assert word not in body
    rows = json.loads(body)["accounts"]
    assert len(rows) == 20
    assert set(rows[0]) == {"id", "pseudonym", "you", "score", "state", "requests", "campaign_id", "reasons",
                            "history"}
    assert rows[0]["pseudonym"].startswith("acct-") and rows[0]["score"] >= rows[-1]["score"]
    blocked = client.get("/api/accounts?state=block").json()["accounts"]
    assert blocked and all(r["state"] == "block" for r in blocked)
    assert len(client.get("/api/accounts?sort=requests&limit=3").json()["accounts"]) == 3
    bench = [r for r in rows if r["id"] == "acct-20f3fe39c0"][0]
    assert bench["state"] == "disclosed"


def test_account_detail_and_evidence(fed) -> None:
    client, worker = fed
    aid = max(worker.accounts.values(), key=lambda a: len(a.evidence)).account_id
    d = client.get(f"/api/accounts/{aid}").json()
    assert {"features", "contributions", "evidence", "history", "declared_use"} <= set(d)
    assert set(d["features"][0]) == {"name", "value", "percentile"}
    assert set(d["contributions"][0]) == {"name", "value"}
    ev_id = d["evidence"][0]["id"]
    e = client.get(f"/api/evidence/{ev_id}").json()
    assert e["evidence"]["id"] == ev_id
    assert e["request"]["request_id"] == e["evidence"]["request_ids"][0]
    assert e["message"]["index"] == e["evidence"]["message_index"]
    assert e["message"]["text"] == e["request"]["messages"][e["message"]["index"]]["text"]
    assert client.get("/api/evidence/ev-nope").status_code == 404
    assert client.get("/api/accounts/nope").status_code == 404


def test_campaigns_graph_and_casefile(fed, monkeypatch) -> None:
    client, worker = fed
    camps = client.get("/api/campaigns").json()["campaigns"]
    graph = client.get("/api/graph").json()
    assert set(graph) == {"nodes", "edges"}
    if graph["nodes"]:
        assert set(graph["nodes"][0]) == {"id", "pseudonym", "score", "state", "campaign_id", "you"}
    if camps:
        cid = camps[0]["id"]
        assert set(camps[0]) == {"id", "size", "score", "state", "mean_member_score", "mean_edge_weight",
                                 "top_edge_types"}
        d = client.get(f"/api/campaigns/{cid}").json()
        assert {"members", "edges", "template", "evidence"} <= set(d)
        assert client.get(f"/api/evidence/{d['evidence'][0]['id']}").status_code == 200
        monkeypatch.setenv("DW_LLM_OFFLINE", "1")
        cf = client.post(f"/api/campaigns/{cid}/casefile").json()
        assert cf["id"] == cid and cf["status"] in ("ok", "fallback") and cf["evidence"]
    aid = next(iter(worker.accounts))
    monkeypatch.setenv("DW_LLM_OFFLINE", "1")
    cf = client.post(f"/api/accounts/{aid}/casefile").json()
    assert set(cf) == {"id", "status", "cached", "text", "cited", "evidence"}


def test_eval_static_and_sse_format(fed) -> None:
    client, _ = fed
    ev = client.get("/api/eval").json()
    assert ev["metrics"] == {"auc": 0.9} and ev["controls"] == {} and ev["images"] == ["/reports/roc_pr.png"]
    assert client.get("/reports/roc_pr.png").status_code == 200
    assert client.get("/reports/missing.png").status_code == 404
    assert client.get("/").status_code == 200
    assert sse("block", {"a": 1}) == 'event: block\ndata: {"a": 1}\n\n'


def test_ingest_queue_worker_and_reset(tmp_path: Path) -> None:
    events, _ = load_fixture()
    store = EventStore(tmp_path / "e.sqlite")
    worker = Worker(models_dir=tmp_path, store=store, detectors=[StubDetector()])
    assert not worker.score                                    # no model file: features only
    with TestClient(create_app(worker, store)) as client:
        payload = {"events": [e.model_dump() for e in events[:300]]}
        assert client.post("/ingest", json=payload).json() == {"accepted": 300, "dropped": 0}
        deadline = time.time() + 10
        while client.get("/api/summary").json()["served"] < 300 and time.time() < deadline:
            time.sleep(0.05)
        s = client.get("/api/summary").json()
        assert s["served"] == 300
        assert set(s["accounts"]) >= {"insufficient", "normal"}
        assert client.post("/admin/reset").json() == {"ok": True}
        assert client.get("/api/summary").json()["served"] == 0
        assert client.get("/blocklist").json()["accounts"] == {}
    assert store.count() == 300


def test_queue_full_counts_drops(tmp_path: Path, monkeypatch) -> None:
    events, _ = load_fixture()
    store = EventStore(tmp_path / "e.sqlite")
    app = create_app(Worker(models_dir=tmp_path, store=store, detectors=[]), store)
    hub = app.state.hub
    monkeypatch.setattr(hub.queue, "_maxsize", 5)
    client = TestClient(app)                                   # no lifespan: the queue is not consumed
    out = client.post("/ingest", json={"events": [e.model_dump() for e in events[:8]]}).json()
    assert out == {"accepted": 5, "dropped": 3}
    assert client.get("/api/summary").json()["dropped"] == 3
    assert store.count() == 3


def test_you_marker(tmp_path: Path) -> None:
    from test_wd_stubs import make_event
    from dw.common.schema import Message
    worker = Worker(models_dir=tmp_path, store=EventStore(None), detectors=[])
    worker.ingest(make_event("r1", CFG["agent"]["account_key"], [Message(role="user", text="hi")]))
    worker.rescore()
    rows = TestClient(create_app(worker)).get("/api/accounts").json()["accounts"]
    assert rows[0]["you"] is True and rows[0]["state"] == "insufficient"
