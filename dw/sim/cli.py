"""Traffic generator CLI for both roles (normal and attack). It replays the holdout scenario against the lab.

The terminal shows live counters and a numbered menu to start and stop personas. With --ui it also serves
static/panel.html and a small JSON API. With --once it sends the selected personas, prints JSON, and exits.

Start resumes a stopped persona after its last sent request. It never sends a request twice: a replay on the
same accounts would repeat their contexts, and D1 would block them for the repeat, not for the persona.

The account outcomes (blocked, review, not flagged) come from the watchdog API. The generator matches them to
its own labels. This is the evaluation view: the watchdog and its dashboard never see the labels.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from rich.console import RenderableType
from rich.text import Text

from dw.common import term
from dw.common.config import CFG, ROOT
from dw.common.react_app import DEFAULT_DIST, mount_react_app
from dw.sim.personas import ROLE_PERSONAS, TITLES
from dw.sim.scenario import Scenario, build_scenario
from dw.sim.sender import Counters, Sender, make_clients

PORTS = {"normal": CFG["ports"]["normal_gen"], "attack": CFG["ports"]["attack_gen"]}
APP_DIST = DEFAULT_DIST  # the React build, served at /app


class Generator:
    """Starts and stops personas of one role. The terminal menu and the panel API both use it."""

    def __init__(self, role: str, scale: str, speed: float, lab_url: str,
                 transport: Optional[httpx.AsyncBaseTransport] = None, data_dir: Optional[Path] = None,
                 watchdog_url: Optional[str] = None,
                 watchdog_transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.role, self.scale, self.speed = role, scale, speed
        self.personas = ROLE_PERSONAS[role]
        self.counters = Counters()
        self.clients = make_clients(lab_url, transport)
        self.client = self.clients[0]          # for /health and /admin calls
        self.sender = Sender(self.clients, self.counters)
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.started: dict[str, str] = {}               # persona -> scale it runs on
        self._scenarios: dict[str, Scenario] = {}
        self._data_dir = data_dir
        self.last_error = ""
        wd_url = watchdog_url or os.environ.get("WATCHDOG_URL", f"http://127.0.0.1:{CFG['ports']['watchdog']}")
        self.watchdog = httpx.AsyncClient(base_url=wd_url, transport=watchdog_transport, timeout=5.0)
        self.outcomes: dict[str, dict[str, int]] = {}    # persona -> {"block", "review", "clear"} account counts

    async def scenario(self, scale: str) -> Scenario:
        """The holdout scenario of this role, built once per scale (in a thread, so the server stays live)."""
        if scale not in self._scenarios:
            self._scenarios[scale] = await asyncio.to_thread(
                build_scenario, "holdout", CFG["account"]["holdout_seed"], scale, self.personas,
                data_dir=self._data_dir)
        return self._scenarios[scale]

    async def lab_sim_time(self) -> Optional[float]:
        resp = await self.client.get("/health")
        resp.raise_for_status()
        return resp.json().get("sim_time")

    def running(self, persona: str) -> bool:
        task = self.tasks.get(persona)
        return task is not None and not task.done()

    async def start(self, persona: str, scale: Optional[str] = None, speed: Optional[float] = None) -> None:
        """Register the persona accounts, then replay its requests from the lab's current simulation time."""
        if persona not in self.personas:
            raise ValueError(f"persona {persona} is not in role {self.role}")
        if self.running(persona):
            return
        self.scale, self.speed = scale or self.scale, speed or self.speed
        sc = await self.scenario(self.scale)
        requests = [r for r in sc.requests if r.persona == persona and not self.counters.was_sent(r)]
        for reg in sc.registrations:
            if sc.labels[reg["account_id"]]["persona"] == persona:
                (await self.client.post("/admin/accounts", json=reg)).raise_for_status()
        lab_time = await self.lab_sim_time()
        offset = lab_time - requests[0].sim_time if (requests and lab_time is not None) else 0.0
        self.started[persona] = self.scale
        self.tasks[persona] = asyncio.create_task(self.sender.run(requests, self.speed, offset))

    def stop(self, persona: str) -> None:
        task = self.tasks.get(persona)
        if task:
            task.cancel()

    def state(self, persona: str) -> str:
        """idle (never started), running, stopped (can resume), or finished (all requests sent)."""
        task = self.tasks.get(persona)
        if task is None:
            return "idle"
        if not task.done():
            return "running"
        return "stopped" if task.cancelled() else "finished"

    async def poll_outcomes(self) -> None:
        """Count the accounts of each started persona by watchdog state."""
        labels = self.labels()
        if not labels:
            return
        try:
            resp = await self.watchdog.get("/api/accounts", params={"limit": 1_000_000})
            resp.raise_for_status()
            states = {a["id"]: a["state"] for a in resp.json()["accounts"]}
        except (httpx.HTTPError, ValueError, KeyError):
            return                                     # keep the last counts if the watchdog does not answer
        out: dict[str, dict[str, int]] = {}
        for account, lab in labels.items():
            c = out.setdefault(lab["persona"], {"block": 0, "review": 0, "clear": 0})
            st = states.get(account)
            c[st if st in ("block", "review") else "clear"] += 1
        self.outcomes = out

    async def poll_outcomes_forever(self, every_s: float = 2.0) -> None:
        while True:
            await self.poll_outcomes()
            await asyncio.sleep(every_s)

    async def toggle(self, persona: str) -> None:
        """Menu action. A failure shows in the view instead of stopping the generator."""
        try:
            if self.running(persona):
                self.stop(persona)
            else:
                await self.start(persona)
            self.last_error = ""
        except (OSError, ValueError, httpx.HTTPError) as exc:
            self.last_error = f"{persona}: {exc}"

    def labels(self) -> dict[str, dict[str, str]]:
        """Labels of the accounts of all started personas."""
        out: dict[str, dict[str, str]] = {}
        for persona, scale in self.started.items():
            sc = self._scenarios[scale]
            out.update({a: lab for a, lab in sc.labels.items() if lab["persona"] == persona})
        return out

    def _accounts(self, persona: str) -> int:
        sc = self._scenarios.get(self.started.get(persona, self.scale))
        if sc is None:
            return int(CFG["sim"]["scales"][self.scale][persona])
        return sum(1 for lab in sc.labels.values() if lab["persona"] == persona)

    def status(self) -> dict[str, Any]:
        snap = self.counters.snapshot()
        zero = {"sent": 0, "served": 0, "blocked": 0, "leaked": 0, "errors": 0}
        return {"role": self.role, "scale": self.scale, "speed": self.speed, "sim_time": self.sender.sim_time,
                "error": self.last_error,
                "personas": {p: {"running": self.running(p), "state": self.state(p), "accounts": self._accounts(p),
                                 **snap.get(p, zero), "outcome": self.outcomes.get(p)}
                             for p in self.personas}}

    async def close(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        for client in self.clients:
            await client.aclose()
        await self.watchdog.aclose()


# ---------------------------------------------------------------- terminal view

def render(gen: Generator) -> RenderableType:
    st = gen.status()
    menu = "  ".join(f"{i} {TITLES[p]}" for i, p in enumerate(gen.personas, 1))
    def outcome(c: dict[str, Any], key: str) -> Any:
        return c["outcome"][key] if c["outcome"] else "-"
    rows = [(TITLES[p], c["state"], c["accounts"], outcome(c, "block"), outcome(c, "review"), outcome(c, "clear"),
             c["sent"], c["served"], c["blocked"], c["leaked"], c["errors"]) for p, c in st["personas"].items()]
    when = datetime.fromtimestamp(st["sim_time"], timezone.utc).strftime("%Y-%m-%d %H:%M") if st["sim_time"] else "-"
    table = term.rows_table(f"sim time {when} UTC  scale {gen.scale}  speed {gen.speed:g}",
                            ["persona", "state", "accounts", "acct blocked", "acct review", "acct not flagged",
                             "req sent", "req served", "req refused", "leaked", "errors"], rows)
    return term.panel(f"{gen.role} traffic generator", table,
                      Text(f"Type a number and Enter to start or stop: {menu}   q = quit"),
                      Text(st["error"], style="red"))


def _read_menu(gen: Generator, loop: asyncio.AbstractEventLoop, quit_event: asyncio.Event) -> None:
    """Read menu choices from stdin in a thread. The event loop does the work."""
    for line in sys.stdin:
        choice = line.strip()
        if choice == "q":
            break
        if choice.isdigit() and 1 <= int(choice) <= len(gen.personas):
            asyncio.run_coroutine_threadsafe(gen.toggle(gen.personas[int(choice) - 1]), loop)
    loop.call_soon_threadsafe(quit_event.set)


# ---------------------------------------------------------------- panel server

def make_app(gen: Generator) -> Any:
    from fastapi import Body, FastAPI
    from fastapi.responses import FileResponse, JSONResponse

    app = FastAPI(title=f"{gen.role} generator")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(ROOT / "static" / "panel.html")

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return gen.status()

    @app.post("/api/start")
    async def start(body: dict[str, Any] = Body(...)) -> Any:
        try:
            await gen.start(body["persona"], body.get("scale"), body.get("speed"))
        except (OSError, ValueError, httpx.HTTPError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return {"ok": True}

    @app.post("/api/stop")
    def stop(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        gen.stop(body["persona"])
        return {"ok": True}

    mount_react_app(app, APP_DIST)
    return app


async def _serve(gen: Generator) -> None:
    import uvicorn
    config = uvicorn.Config(make_app(gen), host="127.0.0.1", port=PORTS[gen.role], log_level="warning")
    await uvicorn.Server(config).serve()


# ---------------------------------------------------------------- main

async def run_once(gen: Generator, personas: list[str], labels_out: Optional[str]) -> dict[str, Any]:
    """Send the whole scenario of the given personas and return the counters."""
    for p in personas:
        await gen.start(p)
    await asyncio.gather(*gen.tasks.values())
    if labels_out:
        Path(labels_out).write_text(json.dumps(gen.labels(), indent=1), encoding="utf-8")
    return {"role": gen.role, "personas": {p: gen.status()["personas"][p] for p in personas}}


async def run_interactive(gen: Generator, personas: list[str], ui: bool) -> None:
    for p in personas:
        await gen.start(p)
    quit_event = asyncio.Event()
    if sys.stdin.isatty():
        threading.Thread(target=_read_menu, args=(gen, asyncio.get_running_loop(), quit_event), daemon=True).start()
    jobs = [asyncio.create_task(term.run_live(lambda: render(gen))),
            asyncio.create_task(gen.poll_outcomes_forever())]
    if ui:
        jobs.append(asyncio.create_task(_serve(gen)))
    try:
        await quit_event.wait()
    finally:
        for job in jobs:
            job.cancel()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m dw.sim.cli", description="Traffic generator (holdout scenario).")
    ap.add_argument("--role", choices=list(ROLE_PERSONAS), required=True)
    ap.add_argument("--persona", default="", help="comma-separated persona names to start at once")
    ap.add_argument("--scale", choices=list(CFG["sim"]["scales"]), default="demo")
    ap.add_argument("--speed", type=float, default=float(CFG["sim"]["speed"]))
    ap.add_argument("--ui", action="store_true", help="serve the control panel on the role's port")
    ap.add_argument("--once", action="store_true", help="send the selected personas, print JSON, exit")
    ap.add_argument("--labels-out", default=None, help="with --once: write account labels to this file")
    args = ap.parse_args(argv)
    args.personas = [p.strip() for p in args.persona.split(",") if p.strip()]
    bad = [p for p in args.personas if p not in ROLE_PERSONAS[args.role]]
    if bad:
        ap.error(f"personas {bad} are not in role {args.role}: {ROLE_PERSONAS[args.role]}")
    if args.once and not args.personas:
        args.personas = list(ROLE_PERSONAS[args.role])
    return args


async def amain(args: argparse.Namespace) -> None:
    lab_url = os.environ.get("LAB_URL", "http://127.0.0.1:8000")
    gen = Generator(args.role, args.scale, args.speed, lab_url)
    try:
        if args.once:
            print(json.dumps(await run_once(gen, args.personas, args.labels_out)), flush=True)
        else:
            await run_interactive(gen, args.personas, args.ui)
    finally:
        await gen.close()


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    try:
        asyncio.run(amain(args))
    except httpx.ConnectError:
        raise SystemExit("The lab does not answer. Start the lab first, or set LAB_URL.")
    except FileNotFoundError as exc:
        raise SystemExit(f"A data file is missing: {exc.filename}. Run the data pipeline first.")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
