"""Tests for the generator CLI: argument rules, start with time shift and registrations, --once output, panel API."""
from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path

import httpx
import pytest

from dw.sim import cli

FIX = Path(__file__).parent / "fixtures" / "canonical_small"
LAB_TIME = 1767600000.0


class FakeLab:
    def __init__(self) -> None:
        self.registered: list[dict] = []
        self.times: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "sim_time": LAB_TIME, "watchdog_connected": True})
        if request.url.path == "/admin/accounts":
            self.registered.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        json.loads(gzip.decompress(request.content))
        self.times.append(float(request.headers["x-sim-time"]))
        return httpx.Response(200, json={"choices": []})


def _gen(lab: FakeLab, role: str = "normal") -> cli.Generator:
    return cli.Generator(role, "small", 1e12, "http://lab", httpx.MockTransport(lab), data_dir=FIX)


def test_parse_args_persona_list() -> None:
    args = cli.parse_args(["--role", "attack", "--persona", "hydra,stealth", "--once"])
    assert args.personas == ["hydra", "stealth"]
    assert cli.parse_args(["--role", "normal", "--once"]).personas == ["solo_dev", "saas_app", "benchmark_runner"]
    with pytest.raises(SystemExit):
        cli.parse_args(["--role", "normal", "--persona", "hydra"])


def test_run_once_registers_shifts_and_writes_labels(tmp_path: Path) -> None:
    lab = FakeLab()
    out = tmp_path / "labels.json"

    async def go() -> dict:
        gen = _gen(lab)
        try:
            return await cli.run_once(gen, ["benchmark_runner", "solo_dev"], str(out))
        finally:
            await gen.close()
    result = asyncio.run(go())
    bench = result["personas"]["benchmark_runner"]
    assert bench["sent"] == bench["served"] > 0 and not bench["running"]
    assert [r["declared_use"] for r in lab.registered] == ["benchmark"]
    assert min(lab.times) == pytest.approx(LAB_TIME, abs=1e-3)
    labels = json.loads(out.read_text())
    assert {v["persona"] for v in labels.values()} == {"benchmark_runner", "solo_dev"}
    assert {v["role"] for v in labels.values()} == {"normal"}


def test_role_rejects_other_personas() -> None:
    async def go() -> None:
        gen = _gen(FakeLab(), "attack")
        try:
            await gen.start("solo_dev")
        finally:
            await gen.close()
    with pytest.raises(ValueError):
        asyncio.run(go())


def test_panel_api() -> None:
    lab = FakeLab()

    async def go() -> None:
        gen = _gen(lab, "attack")
        gen.speed = 1.0          # slow, so the persona is still running when we read the status
        app = cli.make_app(gen)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://panel") as ui:
            page = await ui.get("/")
            assert page.status_code == 200 and "/api/status" in page.text
            st = (await ui.get("/api/status")).json()
            assert st["role"] == "attack" and set(st["personas"]) == {"hydra", "fake_env", "stealth"}
            assert (await ui.post("/api/start", json={"persona": "hydra", "scale": "small", "speed": 1})).json() == {"ok": True}
            await asyncio.sleep(0.2)
            st = (await ui.get("/api/status")).json()
            assert st["personas"]["hydra"]["running"] and st["personas"]["hydra"]["sent"] > 0
            assert st["personas"]["hydra"]["accounts"] == 12
            assert (await ui.post("/api/stop", json={"persona": "hydra"})).json() == {"ok": True}
            await asyncio.sleep(0.05)
            assert not (await ui.get("/api/status")).json()["personas"]["hydra"]["running"]
        await gen.close()
    asyncio.run(go())


def test_render_has_menu() -> None:
    async def go() -> None:
        gen = _gen(FakeLab(), "attack")
        from rich.console import Console
        console = Console(width=140, record=True)
        console.print(cli.render(gen))
        text = console.export_text()
        assert "1 Hydra" in text and "2 Fake-env" in text and "3 Stealth" in text
        await gen.close()
    asyncio.run(go())
