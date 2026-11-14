"""Load test of a running lab: replay requests at a fixed concurrency.

It sends `eval.load_requests` gzip replay requests from the fixture scenario and writes the
requests per second and the latencies (client side and lab `handle()`) to `reports/load.json`.
The client runs in `lab.load_client_procs` processes, because one Python client process is slower than the lab.
Run `python -m dw.lab.loadtest` while the lab (and if possible the watchdog) runs.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from itertools import cycle, islice
from typing import Any

import httpx

from dw.common.chat import to_openai, tool_defs
from dw.common.config import CFG, ROOT, path
from dw.common.schema import RequestEvent
from dw.lab.gateway import percentile

FIXTURE = ROOT / "tests" / "fixtures" / "scenario_small.jsonl"
REPLAY_STEP = {"step": [{"role": "tool_call", "text": "", "tool": "shell", "args": {"command": "ls"}}]}


def load_payloads(total: int) -> list[tuple[str, bytes]]:
    """Make (key, gzip body) pairs from the recorded events of the fixture scenario."""
    pairs = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        ev = RequestEvent.model_validate_json(line)
        body = {"model": ev.model, "messages": to_openai(ev.messages, ev.tools_schema_id),
                "tools": tool_defs(ev.tools_schema_id), "temperature": ev.temperature, "n": ev.n,
                "max_tokens": ev.max_tokens, "x_replay": REPLAY_STEP}
        pairs.append((f"acct-load-{ev.account_id}", gzip.compress(json.dumps(body).encode())))
    return list(islice(cycle(pairs), total))


async def send_all(lab_url: str, payloads: list[tuple[str, bytes]], concurrency: int) -> dict[str, Any]:
    """Send the payloads with at most `concurrency` requests in flight. Return latencies and times."""
    latencies: list[float] = []
    statuses: dict[int, int] = {}
    sem = asyncio.Semaphore(concurrency)
    url = lab_url.rstrip("/") + "/v1/chat/completions"
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(timeout=30.0, limits=limits) as client:
        async def send(key: str, data: bytes) -> None:
            headers = {"Authorization": f"Bearer {key}", "Content-Encoding": "gzip",
                       "Content-Type": "application/json"}
            async with sem:
                t0 = time.perf_counter()
                resp = await client.post(url, content=data, headers=headers)
                latencies.append((time.perf_counter() - t0) * 1000.0)
                statuses[resp.status_code] = statuses.get(resp.status_code, 0) + 1

        start = time.time()
        await asyncio.gather(*(send(k, d) for k, d in payloads))
    return {"latencies": latencies, "statuses": statuses, "start": start, "end": time.time()}


def run_part(lab_url: str, total: int, part: int, procs: int, concurrency: int) -> dict[str, Any]:
    payloads = load_payloads(total)[part::procs]
    return asyncio.run(send_all(lab_url, payloads, concurrency))


def run(lab_url: str, total: int, concurrency: int, procs: int) -> dict[str, Any]:
    with ProcessPoolExecutor(procs) as pool:
        parts = list(pool.map(run_part, [lab_url] * procs, [total] * procs, range(procs),
                              [procs] * procs, [max(1, concurrency // procs)] * procs))
    latencies = [x for p in parts for x in p["latencies"]]
    statuses: dict[int, int] = {}
    for p in parts:
        for code, count in p["statuses"].items():
            statuses[code] = statuses.get(code, 0) + count
    elapsed = max(p["end"] for p in parts) - min(p["start"] for p in parts)
    stats = httpx.get(lab_url.rstrip("/") + "/stats").json()
    return {"requests": total, "concurrency": concurrency, "client_procs": procs, "seconds": round(elapsed, 3),
            "rps": round(total / elapsed, 1), "statuses": statuses,
            "client_p50_ms": round(percentile(latencies, 0.50), 2),
            "client_p95_ms": round(percentile(latencies, 0.95), 2),
            "lab_handle_p50_ms": stats["p50_ms"], "lab_handle_p95_ms": stats["p95_ms"],
            "watchdog_connected": stats["watchdog_connected"],
            "target": {"rps_min": 300, "handle_p95_ms_max": 5.0}}


def main() -> None:
    lab_url = os.environ.get("LAB_URL", f"http://127.0.0.1:{CFG['ports']['lab']}")
    ev = CFG["eval"]
    result = run(lab_url, ev["load_requests"], ev["load_concurrency"], CFG["lab"]["load_client_procs"])
    out = path("reports") / "load.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
