"""Tests for the investigator dashboard (static/) and the mock watchdog (dw/watchdog/mock_server.py)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dw.watchdog.mock_server import MockWatchdog, create_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
API_MD = ROOT / "docs" / "API.md"
PERSONA_WORDS = ("solo_dev", "saas_app", "benchmark_runner", "hydra", "fake_env", "stealth", "persona", "swechat", "openswe")


def _norm(path: str) -> str:
    path = path.split("?")[0]
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    path = re.sub(r"\{[^}]*\}", "{}", path)
    return path.rstrip("/") or "/"


def api_md_endpoints() -> set[str]:
    text = API_MD.read_text(encoding="utf-8")
    sec = text.split("## 4. Watchdog", 1)[1].split("\n## ", 1)[0]
    out = set()
    for line in sec.splitlines():
        if line.startswith("### "):
            for _method, path in re.findall(r"`(GET|POST|PUT|DELETE)\s+([^`\s]+)", line):
                out.add(_norm(path))
    return out


def js_endpoints() -> set[str]:
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    found = re.findall(r"""[`"'](/(?:api|admin|ingest|blocklist|reports)[^`"'\s]*)[`"']""", js)
    return {_norm(p) for p in found}


@pytest.fixture(scope="module")
def client():
    app = create_app(MockWatchdog(batch=50), prefill=1.0, run_ticker=False)
    with TestClient(app) as c:
        yield c


def test_js_uses_only_documented_endpoints() -> None:
    documented = api_md_endpoints()
    used = js_endpoints()
    assert "/api/summary" in documented and "/api/stream" in documented
    assert used, "no endpoints found in dashboard.js"
    assert used <= documented, f"endpoints not in docs/API.md section 4: {used - documented}"
    for need in ("/api/summary", "/api/accounts", "/api/accounts/{}", "/api/evidence/{}", "/api/campaigns",
                 "/api/campaigns/{}", "/api/campaigns/{}/casefile", "/api/accounts/{}/casefile", "/api/graph",
                 "/api/stream", "/api/eval"):
        assert need in used, need


def test_vendor_files_and_local_scripts_only() -> None:
    for name in ("chart.umd.min.js", "cytoscape.min.js"):
        p = STATIC / "vendor" / name
        assert p.exists() and p.stat().st_size > 50_000, name
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    srcs = re.findall(r"""<script[^>]*\bsrc=["']([^"']+)""", html)
    assert srcs == ["/static/vendor/chart.umd.min.js", "/static/vendor/cytoscape.min.js", "/static/dashboard.js"]
    links = re.findall(r"""\b(?:src|href)=["']([^"']+)""", html)
    assert not [u for u in links if re.match(r"(https?:)?//", u)], links
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    assert "http://" not in js and "https://" not in js
    css = (STATIC / "dashboard.css").read_text(encoding="utf-8")
    assert "http" not in css and "@import" not in css


def test_static_serving(client) -> None:
    r = client.get("/")
    assert r.status_code == 200 and "Distillation Watchdog" in r.text
    for p in ("/static/dashboard.js", "/static/dashboard.css", "/static/vendor/chart.umd.min.js", "/static/vendor/cytoscape.min.js"):
        assert client.get(p).status_code == 200, p


def test_summary_shape(client) -> None:
    s = client.get("/api/summary").json()
    for k in ("sim_time", "rps", "served", "blocked", "accounts", "campaigns", "rate_history"):
        assert k in s
    assert set(s["accounts"]) == {"insufficient", "normal", "review", "block", "disclosed"}
    assert s["rate_history"] and set(s["rate_history"][0]) >= {"t", "served", "blocked"}


def test_accounts_and_detail_shape(client) -> None:
    rows = client.get("/api/accounts?state=&sort=score&limit=50").json()["accounts"]
    assert len(rows) == 21
    assert [r["score"] for r in rows] == sorted((r["score"] for r in rows), reverse=True)
    assert sum(1 for r in rows if r["you"]) == 1
    states = {r["state"] for r in rows}
    assert {"block", "normal", "disclosed"} <= states
    for r in rows:
        assert set(r) >= {"id", "pseudonym", "you", "score", "state", "requests", "campaign_id", "reasons", "history"}
        assert re.fullmatch(r"acct-[0-9a-f]{6}", r["pseudonym"])
        assert len(r["reasons"]) <= 3
        assert all(len(h) == 2 for h in r["history"])
    blocked = client.get("/api/accounts?state=block").json()["accounts"]
    assert blocked and all(r["state"] == "block" for r in blocked)
    top = rows[0]
    a = client.get(f"/api/accounts/{top['id']}").json()
    assert set(a) >= {"id", "pseudonym", "you", "score", "state", "requests", "campaign_id", "declared_use",
                      "features", "contributions", "evidence", "history"}
    assert all(set(f) >= {"name", "value", "percentile"} for f in a["features"])
    assert all(set(c) >= {"name", "value"} for c in a["contributions"])
    assert a["evidence"]
    for e in a["evidence"]:
        assert set(e) >= {"id", "detector", "account_id", "request_ids", "message_index", "summary", "weight", "sim_time"}
    assert client.get("/api/accounts/acct-nope").status_code == 404


def test_evidence_shape(client) -> None:
    top = client.get("/api/accounts").json()["accounts"][0]
    ev = client.get(f"/api/accounts/{top['id']}").json()["evidence"][0]
    d = client.get(f"/api/evidence/{ev['id']}").json()
    assert set(d["evidence"]) >= {"id", "detector", "summary", "request_ids", "message_index"}
    req = d["request"]
    assert set(req) >= {"request_id", "pseudonym", "sim_time", "temperature", "n", "max_tokens", "tools_schema_id", "messages"}
    assert req["messages"] and all(set(m) >= {"index", "role", "text", "tool", "args"} for m in req["messages"])
    assert d["message"]["index"] == ev["message_index"]
    assert client.get("/api/evidence/ev-nope").status_code == 404


def test_campaigns_graph_and_casefiles(client) -> None:
    cmps = client.get("/api/campaigns").json()["campaigns"]
    assert len(cmps) >= 2
    for c in cmps:
        assert set(c) >= {"id", "size", "score", "state", "mean_member_score", "mean_edge_weight", "top_edge_types"}
    c = client.get(f"/api/campaigns/{cmps[0]['id']}").json()
    assert set(c) >= {"id", "size", "score", "state", "members", "edges", "template", "evidence"}
    assert len(c["members"]) == c["size"]
    assert all(set(e) >= {"source", "target", "weight", "types", "evidence"} for e in c["edges"])
    for ev in c["evidence"]:
        assert client.get(f"/api/evidence/{ev['id']}").status_code == 200
    g = client.get("/api/graph").json()
    ids = {n["id"] for n in g["nodes"]}
    assert all(set(n) >= {"id", "pseudonym", "score", "state", "campaign_id", "you"} for n in g["nodes"])
    assert all(e["source"] in ids and e["target"] in ids and "weight" in e for e in g["edges"])
    for path in (f"/api/campaigns/{cmps[0]['id']}/casefile", f"/api/accounts/{c['members'][0]['id']}/casefile"):
        cf = client.post(path).json()
        assert set(cf) >= {"id", "status", "cached", "text", "cited", "evidence"}
        assert cf["status"] == "ok"
        cites = set(re.findall(r"\[(ev-[\w-]+)\]", cf["text"]))
        assert cites and cites <= {e["id"] for e in cf["evidence"]}
        for ev_id in cites:
            assert client.get(f"/api/evidence/{ev_id}").status_code == 200
        assert client.post(path).json()["cached"] is True
    # An account with no evidence gives the fallback status.
    quiet = [r for r in client.get("/api/accounts").json()["accounts"]
             if not client.get(f"/api/accounts/{r['id']}").json()["evidence"]]
    assert quiet
    cf = client.post(f"/api/accounts/{quiet[0]['id']}/casefile").json()
    assert cf["status"] == "fallback" and cf["text"] == ""


def test_eval_and_images(client) -> None:
    d = client.get("/api/eval").json()
    assert set(d) >= {"metrics", "controls", "compute", "load", "images"}
    for url in d["images"]:
        assert url.startswith("/reports/")
        r = client.get(url)
        assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_blocklist_ingest_reset() -> None:
    app = create_app(MockWatchdog(), prefill=1.0, run_ticker=False)
    with TestClient(app) as c:
        bl = c.get("/blocklist").json()
        assert bl["accounts"] and all("reason" in v for v in bl["accounts"].values())
        assert c.post("/ingest", json={"events": [{}, {}]}).json() == {"accepted": 2, "dropped": 0}
        assert c.post("/admin/reset").json() == {"ok": True}
        assert c.get("/api/accounts").json()["accounts"] == []


def test_no_persona_labels_or_sources_in_responses(client) -> None:
    bodies = [client.get(p).text for p in ("/api/summary", "/api/accounts?limit=100", "/api/graph", "/api/campaigns")]
    for r in client.get("/api/accounts?limit=100").json()["accounts"]:
        a = client.get(f"/api/accounts/{r['id']}").json()
        bodies.append(json.dumps(a))
        for e in a["evidence"][:2]:
            bodies.append(client.get(f"/api/evidence/{e['id']}").text)
    for c in client.get("/api/campaigns").json()["campaigns"]:
        bodies.append(client.get(f"/api/campaigns/{c['id']}").text)
    for b in bodies:
        for w in PERSONA_WORDS:
            assert w not in b, w


def test_stream_and_events() -> None:
    m = MockWatchdog(batch=100)
    m.advance(100)
    seen = set()
    while m.cursor < len(m.events):
        m.advance(100)
        seen |= {k for k, _ in m.take_events()}
    assert {"flag", "block", "campaign"} <= seen or {"block", "campaign"} <= seen
    app = create_app(m, run_ticker=False)
    with TestClient(app) as c:
        with c.stream("GET", "/api/stream") as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            lines = []
            for line in r.iter_lines():
                lines.append(line)
                if line.startswith("data:"):
                    break
    assert lines[0] == "event: summary"
    assert "rate_history" in json.loads(lines[1][5:])
