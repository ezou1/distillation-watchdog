"""Tests for the React front end (frontend/): the /app mount on both servers and the endpoints it uses.

The tests do not need a built front end. They put a small stand-in build in a temporary folder.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dw.agent import server as agent_server
from dw.agent.tools import Sandbox
from dw.common.config import CFG
from dw.sim import cli as generator_cli
from dw.watchdog import mock_server, server as watchdog_server
from dw.watchdog.worker import Worker

ROOT = Path(__file__).resolve().parents[1]
API_MD = ROOT / "docs" / "API.md"
API_TS = ROOT / "frontend" / "src" / "api.ts"
MARK = "react-build-stand-in"


@pytest.fixture()
def dist(tmp_path: Path) -> Path:
    """A stand-in for frontend/dist: an index page and one asset."""
    folder = tmp_path / "dist"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text(f"<!doctype html><title>{MARK}</title>")
    (folder / "assets" / "app.js").write_text("export {};")
    return folder


async def no_lab(_body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return 200, {}


def watchdog_client(tmp_path: Path) -> TestClient:
    return TestClient(watchdog_server.create_app(Worker(models_dir=tmp_path, detectors=[])))


def agent_client(tmp_path: Path) -> TestClient:
    agent = agent_server.Agent(Sandbox(tmp_path / "ws"), no_lab)
    return TestClient(agent_server.build_app(agent))


def test_watchdog_serves_the_build_at_app(tmp_path: Path, dist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watchdog_server, "APP_DIST", dist)
    client = watchdog_client(tmp_path)
    for path in ("/app/", "/app"):
        page = client.get(path)
        assert page.status_code == 200 and MARK in page.text, path
    assert client.get("/app/assets/app.js").status_code == 200
    assert client.get("/app/assets/missing.js").status_code == 404
    # The HTML dashboard and the API stay at their paths.
    home = client.get("/")
    assert home.status_code == 200 and MARK not in home.text and "Distillation Watchdog" in home.text
    assert client.get("/api/summary").status_code == 200


def test_watchdog_works_without_a_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watchdog_server, "APP_DIST", tmp_path / "no-build")
    client = watchdog_client(tmp_path)
    assert client.get("/app/").status_code == 404
    assert client.get("/").status_code == 200
    assert client.get("/api/summary").status_code == 200


def test_mock_watchdog_serves_the_build_at_app(dist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mock_server, "APP_DIST", dist)
    client = TestClient(mock_server.create_app(mock_server.MockWatchdog(), run_ticker=False))
    assert MARK in client.get("/app/").text
    assert MARK not in client.get("/").text


def test_agent_serves_the_build_and_keeps_its_origin_check(
        tmp_path: Path, dist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_server, "APP_DIST", dist)
    client = agent_client(tmp_path)
    page = client.get("/app/")
    assert page.status_code == 200 and MARK in page.text
    assert client.get("/").status_code == 200                      # the HTML agent page stays
    assert client.get("/api/session").json()["status"] == "idle"
    # The dashboard is on another port. A POST from that origin is still refused.
    other = {"Origin": f"http://127.0.0.1:{CFG['ports']['watchdog']}"}
    assert client.post("/api/session/new", json={}, headers=other).status_code == 403
    own = {"Origin": f"http://127.0.0.1:{CFG['ports']['agent']}"}
    assert client.post("/api/session/new", json={}, headers=own).json() == {"ok": True}


def test_agent_works_without_a_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_server, "APP_DIST", tmp_path / "no-build")
    client = agent_client(tmp_path)
    assert client.get("/app/").status_code == 404
    assert client.get("/").status_code == 200


def generator_client(role: str) -> TestClient:
    # The lab URL is never called: these tests only read pages and the status.
    gen = generator_cli.Generator(role, "small", 600, "http://127.0.0.1:9")
    return TestClient(generator_cli.make_app(gen))


@pytest.mark.parametrize("role", ["normal", "attack"])
def test_generator_serves_the_build_at_app(role: str, dist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generator_cli, "APP_DIST", dist)
    client = generator_client(role)
    page = client.get("/app/")
    assert page.status_code == 200 and MARK in page.text
    assert client.get("/app/assets/app.js").status_code == 200
    # The HTML panel and the API stay at their paths.
    home = client.get("/")
    assert home.status_code == 200 and MARK not in home.text
    assert client.get("/api/status").json()["role"] == role


def test_generator_works_without_a_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generator_cli, "APP_DIST", tmp_path / "no-build")
    client = generator_client("attack")
    assert client.get("/app/").status_code == 404
    assert client.get("/").status_code == 200


# ---- the React client uses only documented endpoints (docs/REACT_STAGE.md rule 3) -----------------

def _norm(path: str) -> str:
    path = path.split("?")[0]
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    path = re.sub(r"\{[^}]*\}", "{}", path)
    return path.rstrip("/") or "/"


def documented(section: str) -> set[str]:
    text = API_MD.read_text(encoding="utf-8").split(section, 1)[1].split("\n## ", 1)[0]
    found = set()
    for line in text.splitlines():
        if line.startswith("### "):
            found |= {_norm(path) for _method, path in re.findall(r"`(GET|POST|PUT|DELETE)\s+([^`\s]+)", line)}
    return found


def client_endpoints() -> set[str]:
    source = API_TS.read_text(encoding="utf-8")
    return {_norm(path) for path in re.findall(r"""[`"](/(?:api|admin|ingest|blocklist|reports)[^`"\s]*)[`"]""", source)}


def test_react_client_uses_only_documented_endpoints() -> None:
    watchdog = documented("## 4. Watchdog")
    agent = documented("## 3. Agent chat")
    generators = documented("## 2. Traffic generators")
    allowed = watchdog | agent | generators
    used = client_endpoints()
    assert used, "no endpoints found in frontend/src/api.ts"
    assert used <= allowed, f"endpoints not in docs/API.md: {used - allowed}"
    for need in ("/api/summary", "/api/accounts", "/api/accounts/{}", "/api/accounts/{}/casefile", "/api/evidence/{}",
                 "/api/campaigns", "/api/campaigns/{}", "/api/campaigns/{}/casefile", "/api/graph", "/api/stream",
                 "/api/eval", "/api/chat", "/api/approve", "/api/session", "/api/session/new", "/api/files",
                 "/api/status", "/api/start", "/api/stop"):
        assert need in used, need


def test_react_sources_load_nothing_from_other_hosts() -> None:
    """The lab runs on 127.0.0.1 only: fonts and scripts come from the build, not from a CDN."""
    for path in [ROOT / "frontend" / "index.html", *(ROOT / "frontend" / "src").rglob("*.css")]:
        text = path.read_text(encoding="utf-8")
        links = re.findall(r"""(?:src|href)=["']([^"']+)|url\(\s*["']?([^"')]+)|@import\s+["']([^"']+)""", text)
        for url in (u for group in links for u in group if u):
            assert not re.match(r"(https?:)?//", url), f"{path.name} loads {url}"
