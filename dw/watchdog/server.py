"""Watchdog HTTP server (port 8010): ingest queue, worker task, block list, dashboard API, SSE, terminal view.

Run it with `python -m dw.watchdog.server`. Without a trained account model, it computes features only.
The API shows pseudonyms only. It never shows persona labels or the data source.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from rich.console import RenderableType

from dw.common import term
from dw.common.config import CFG, ROOT, path as cfg_path
from dw.common.schema import AccountState, Evidence, RequestEvent
from dw.common.scrub import scrub
from dw.watchdog import casefile
from dw.watchdog.campaigns import Campaign
from dw.watchdog.store import EventStore
from dw.watchdog.worker import Worker, display_name, top_reasons

log = logging.getLogger("dw.watchdog.server")

STATIC = ROOT / "static"
PLACEHOLDER = "<!DOCTYPE html><html><body><h1>Distillation Watchdog</h1><p>The dashboard is not built yet. " \
              "See <a href='/api/summary'>/api/summary</a>.</p></body></html>"
EVAL_FILES = ("metrics", "controls", "compute", "load")


class IngestBody(BaseModel):
    events: list[RequestEvent]


class Hub:
    """Server state around one Worker: the ingest queue, counters, SSE subscribers, and the case-file cache."""

    def __init__(self, worker: Worker, store: Optional[EventStore]) -> None:
        self.worker = worker
        self.store = store
        self.queue: asyncio.Queue[RequestEvent] = asyncio.Queue(maxsize=CFG["watchdog"]["queue_max"])
        self.dropped = 0
        self.processed: deque[tuple[float, int]] = deque()
        self.subscribers: set[asyncio.Queue[tuple[str, dict]]] = set()
        self.casefiles: dict[str, dict[str, Any]] = {}
        self.llm: Optional[casefile.ChatClient] = None

    # ---- ingest and worker ------------------------------------------------------------------

    def enqueue(self, events: list[RequestEvent]) -> tuple[int, int]:
        accepted = dropped = 0
        for ev in events:
            try:
                self.queue.put_nowait(ev)
                accepted += 1
            except asyncio.QueueFull:
                dropped += 1
                if self.store is not None:
                    self.store.append(ev, dropped=True)
        self.dropped += dropped
        return accepted, dropped

    async def consume(self) -> None:
        """Worker task: observe events in chunks and yield to the event loop between chunks."""
        chunk = CFG["watchdog"]["ingest_chunk"]
        while True:
            batch = [await self.queue.get()]
            while len(batch) < chunk and not self.queue.empty():
                batch.append(self.queue.get_nowait())
            for ev in batch:
                self.worker.ingest(ev)
            self.processed.append((time.monotonic(), len(batch)))
            await asyncio.sleep(0)

    def rps(self) -> float:
        window = 5.0
        now = time.monotonic()
        while self.processed and self.processed[0][0] < now - window:
            self.processed.popleft()
        return round(sum(n for _, n in self.processed) / window, 1)

    def rescore(self) -> None:
        self.publish(self.worker.rescore())

    def run_campaigns(self) -> None:
        self.publish(self.worker.run_campaigns())

    def reset(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()
        self.worker.reset()
        self.dropped = 0
        self.processed.clear()
        self.casefiles.clear()

    # ---- SSE ---------------------------------------------------------------------------------

    def publish(self, events: list[dict]) -> None:
        for event in events:
            kind = event["type"]
            data = {k: v for k, v in event.items() if k != "type"}
            self.broadcast(kind, data)

    def broadcast(self, kind: str, data: dict) -> None:
        for q in list(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait((kind, data))

    # ---- views -------------------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        w = self.worker
        return {"sim_time": w.sim_time, "rps": self.rps(), "served": w.served, "blocked": w.blocked,
                "accounts": w.state_counts(), "campaigns": len(w.campaigns),
                "queue_depth": self.queue.qsize(), "dropped": self.dropped, "rate_history": w.rate_history()}


# ---- JSON shapes (docs/API.md section 4) ---------------------------------------------------------

def _you(account_id: str) -> bool:
    return account_id == CFG["agent"]["account_key"]


def _score(acct: AccountState) -> float:
    return round(acct.score, 4) if acct.score is not None else 0.0


def _history(acct: AccountState) -> list[list[float]]:
    return [[t, s] for t, s in acct.score_history]


def account_row(acct: AccountState) -> dict[str, Any]:
    return {"id": acct.account_id, "pseudonym": display_name(acct.account_id), "you": _you(acct.account_id),
            "score": _score(acct), "rules_level": acct.rules_level, "state": acct.state, "requests": acct.n_requests,
            "campaign_id": acct.campaign_id, "reasons": top_reasons(acct, 3), "history": _history(acct)}


def evidence_row(e: Evidence) -> dict[str, Any]:
    return e.model_dump()


def percentile(value: float, population: list[float]) -> float:
    below = sum(1 for v in population if v < value)
    equal = sum(1 for v in population if v == value)
    return round(100.0 * (below + 0.5 * equal) / len(population), 1) if population else 50.0


def account_detail(worker: Worker, acct: AccountState) -> dict[str, Any]:
    population = [a.features for a in worker.accounts.values() if a.features]
    features = [{"name": n, "value": round(v, 4), "percentile": percentile(v, [p.get(n, 0.0) for p in population])}
                for n, v in sorted(acct.features.items())]
    contributions = [{"name": n, "value": round(v, 4)}
                     for n, v in sorted(acct.contributions.items(), key=lambda p: -abs(p[1]))]
    ranked = sorted(acct.evidence, key=lambda e: (-e.weight, -e.sim_time))[:CFG["watchdog"]["evidence_list_max"]]
    row = account_row(acct)
    row.pop("reasons")
    return {**row, "declared_use": acct.declared_use, "features": features, "contributions": contributions,
            "evidence": [evidence_row(e) for e in ranked]}


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    return value


def request_view(ev: RequestEvent) -> dict[str, Any]:
    messages = [{"index": i, "role": m.role, "text": scrub(m.text), "tool": m.tool, "args": _scrub_value(m.args)}
                for i, m in enumerate(ev.messages)]
    return {"request_id": ev.request_id, "account_id": ev.account_id, "pseudonym": display_name(ev.account_id),
            "sim_time": ev.sim_time, "temperature": ev.temperature, "n": ev.n, "max_tokens": ev.max_tokens,
            "tools_schema_id": ev.tools_schema_id, "messages": messages}


def evidence_detail(e: Evidence, ev: Optional[RequestEvent]) -> dict[str, Any]:
    head = {"id": e.id, "detector": e.detector, "summary": e.summary, "request_ids": e.request_ids,
            "message_index": e.message_index}
    if ev is None:
        return {"evidence": head, "request": None, "message": None}
    request = request_view(ev)
    message = None
    if request["messages"]:
        idx = e.message_index if e.message_index is not None and e.message_index < len(ev.messages) \
            else len(ev.messages) - 1
        message = {k: v for k, v in request["messages"][idx].items()}
    return {"evidence": head, "request": request, "message": message}


def campaign_row(c: Campaign) -> dict[str, Any]:
    return {"id": c.id, "size": len(c.members), "score": c.score, "state": c.state,
            "mean_member_score": c.mean_member_score, "mean_edge_weight": c.mean_edge_weight,
            "top_edge_types": c.top_edge_types}


def campaign_detail(worker: Worker, c: Campaign) -> dict[str, Any]:
    members = [{"id": a, "pseudonym": display_name(a), "score": _score(worker.accounts[a]),
                "state": worker.accounts[a].state} for a in c.members]
    return {"id": c.id, "size": len(c.members), "score": c.score, "state": c.state, "members": members,
            "edges": c.edges, "evidence": [evidence_row(e) for e in c.evidence]}


def graph_view(worker: Worker) -> dict[str, Any]:
    nodes = [{"id": a, "pseudonym": display_name(a), "score": _score(worker.accounts[a]),
              "state": worker.accounts[a].state, "campaign_id": worker.accounts[a].campaign_id, "you": _you(a)}
             for a in worker.graph_nodes if a in worker.accounts]
    return {"nodes": nodes, "edges": worker.graph_edges}


def eval_view(reports: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in EVAL_FILES:
        f = reports / f"{name}.json"
        out[name] = json.loads(f.read_text()) if f.exists() else {}
    out["images"] = [f"/reports/{p.name}" for p in sorted(reports.glob("*.png"))]
    return out


def sse(kind: str, data: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(data)}\n\n"


# ---- case files ----------------------------------------------------------------------------------

def _mean_contributions(accts: list[AccountState]) -> dict[str, float]:
    total: dict[str, float] = {}
    for a in accts:
        for n, v in a.contributions.items():
            total[n] = total.get(n, 0.0) + v / len(accts)
    return total


async def _casefile(hub: Hub, key: str, case_id: str, subject: dict, evidence: list[Evidence],
                    contributions: dict[str, float]) -> dict[str, Any]:
    cache_key = key + ":" + ",".join(e.id for e in casefile.top_evidence(evidence))
    if cache_key in hub.casefiles:
        return {**hub.casefiles[cache_key], "cached": True}
    if hub.llm is None:
        from dw.common.llm import LLMClient
        hub.llm = LLMClient()
    result = await casefile.write_casefile(case_id, subject, evidence, contributions, hub.llm)
    if result["status"] == "ok":
        hub.casefiles[cache_key] = result
    return result


async def account_casefile(hub: Hub, acct: AccountState) -> dict[str, Any]:
    subject = {"kind": "account", "pseudonym": display_name(acct.account_id), "score": _score(acct),
               "state": acct.state, "requests": acct.n_requests, "sessions": len(acct.sessions),
               "campaign": acct.campaign_id, "declared_use": acct.declared_use}
    return await _casefile(hub, "acct:" + acct.account_id, acct.account_id, subject, acct.evidence,
                           acct.contributions)


async def campaign_casefile(hub: Hub, c: Campaign) -> dict[str, Any]:
    accts = [hub.worker.accounts[a] for a in c.members]
    subject = {"kind": "campaign", **campaign_row(c),
               "member_states": {s: sum(1 for a in accts if a.state == s) for s in {a.state for a in accts}}}
    evidence = c.evidence + [e for a in accts for e in a.evidence]
    return await _casefile(hub, "cmp:" + c.id, c.id, subject, evidence, _mean_contributions(accts))


# ---- app -----------------------------------------------------------------------------------------

async def _every(seconds: float, fn: Callable[[], None]) -> None:
    while True:
        await asyncio.sleep(seconds)
        try:
            fn()
        except Exception:  # keep the server alive; the error is in the log
            log.exception("periodic task %s failed", getattr(fn, "__name__", fn))


async def _summary_ticks(hub: Hub) -> None:
    while True:
        await asyncio.sleep(CFG["watchdog"]["sse_interval_s"])
        if hub.subscribers:
            hub.broadcast("summary", hub.summary())


def _start_tasks(hub: Hub) -> list[asyncio.Task]:
    wd = CFG["watchdog"]
    jobs = [hub.consume(), _every(wd["rescore_interval_s"], hub.rescore),
            _every(wd["campaign_interval_s"], hub.run_campaigns), _summary_ticks(hub)]
    if hub.store is not None:
        jobs.append(_every(wd["sqlite_flush_s"], hub.store.flush))
    return [asyncio.create_task(j) for j in jobs]


def create_app(worker: Optional[Worker] = None, store: Optional[EventStore] = None,
               reports_dir: Optional[Path] = None) -> FastAPI:
    """Build the FastAPI app. Background tasks start in the lifespan (live server and `with TestClient`)."""
    worker = worker or Worker(store=store)
    hub = Hub(worker, store if store is not None else worker.store)
    reports = reports_dir or cfg_path("reports")

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        tasks = _start_tasks(hub)
        yield
        for t in tasks:
            t.cancel()
        if hub.store is not None:
            hub.store.flush()

    app = FastAPI(title="Distillation Watchdog", lifespan=lifespan)
    app.state.hub = hub

    def get_account(account_id: str) -> AccountState:
        acct = worker.accounts.get(account_id)
        if acct is None:
            raise HTTPException(404, "unknown account")
        return acct

    def get_campaign(campaign_id: str) -> Campaign:
        camp = worker.campaigns.get(campaign_id)
        if camp is None:
            raise HTTPException(404, "unknown campaign")
        return camp

    @app.post("/ingest")
    async def ingest(body: IngestBody) -> dict[str, int]:
        accepted, dropped = hub.enqueue(body.events)
        return {"accepted": accepted, "dropped": dropped}

    @app.get("/blocklist")
    async def blocklist() -> dict[str, Any]:
        return {"version": worker.blocklist_version, "accounts": worker.blocklist()}

    @app.get("/api/summary")
    async def summary() -> dict[str, Any]:
        return hub.summary()

    @app.get("/api/accounts")
    async def accounts(state: Optional[str] = None, sort: str = "score", limit: int = 50) -> dict[str, Any]:
        rows = [a for a in worker.accounts.values() if not state or a.state == state]
        keys: dict[str, Callable[[AccountState], float]] = {
            "score": lambda a: worker.strength(a) if a.score is not None or a.rules_level else -1.0,
            "requests": lambda a: float(a.n_requests), "last_seen": lambda a: a.last_seen}
        rows.sort(key=keys.get(sort, keys["score"]), reverse=True)
        return {"accounts": [account_row(a) for a in rows[:max(limit, 0)]]}

    @app.get("/api/accounts/{account_id}")
    async def account(account_id: str) -> dict[str, Any]:
        return account_detail(worker, get_account(account_id))

    @app.post("/api/accounts/{account_id}/casefile")
    async def account_case(account_id: str) -> dict[str, Any]:
        return await account_casefile(hub, get_account(account_id))

    @app.get("/api/evidence/{evidence_id}")
    async def evidence(evidence_id: str) -> dict[str, Any]:
        item = worker.evidence_index.get(evidence_id)
        if item is None:
            raise HTTPException(404, "unknown evidence")
        ev = hub.store.get(item.request_ids[0]) if hub.store is not None and item.request_ids else None
        return evidence_detail(item, ev)

    @app.get("/api/campaigns")
    async def campaigns() -> dict[str, Any]:
        rows = sorted(worker.campaigns.values(), key=lambda c: -c.score)
        return {"campaigns": [campaign_row(c) for c in rows]}

    @app.get("/api/campaigns/{campaign_id}")
    async def campaign(campaign_id: str) -> dict[str, Any]:
        return campaign_detail(worker, get_campaign(campaign_id))

    @app.post("/api/campaigns/{campaign_id}/casefile")
    async def campaign_case(campaign_id: str) -> dict[str, Any]:
        return await campaign_casefile(hub, get_campaign(campaign_id))

    @app.get("/api/graph")
    async def graph() -> dict[str, Any]:
        return graph_view(worker)

    @app.get("/api/stream")
    async def stream(request: Request) -> StreamingResponse:
        q: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(maxsize=1000)
        hub.subscribers.add(q)

        async def events() -> AsyncIterator[str]:
            try:
                yield sse("summary", hub.summary())
                while not await request.is_disconnected():
                    try:
                        kind, data = await asyncio.wait_for(q.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    yield sse(kind, data)
            finally:
                hub.subscribers.discard(q)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.get("/api/eval")
    async def evaluation() -> dict[str, Any]:
        return eval_view(reports)

    @app.get("/reports/{name}")
    async def report_image(name: str) -> FileResponse:
        f = reports / name
        if "/" in name or not name.endswith(".png") or not f.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(f)

    @app.post("/admin/reset")
    async def reset() -> dict[str, bool]:
        hub.reset()
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> Any:
        page = STATIC / "dashboard.html"
        return FileResponse(page) if page.exists() else HTMLResponse(PLACEHOLDER)

    app.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")
    return app


# ---- terminal view -------------------------------------------------------------------------------

def render(hub: Hub) -> RenderableType:
    w = hub.worker
    s = hub.summary()
    head = term.kv_table("watchdog", [
        ("ingest rate (req/s)", s["rps"]), ("queue depth", s["queue_depth"]), ("dropped", s["dropped"]),
        ("served / blocked", f"{s['served']} / {s['blocked']}"), ("model", "yes" if w.score else "no (features only)"),
        ("accounts", " ".join(f"{k}={v}" for k, v in s["accounts"].items()))])
    top = sorted(w.accounts.values(), key=lambda a: -(a.score or 0.0))[:10]
    accounts = term.rows_table("top accounts", ["account", "score", "state", "requests"],
                               [(display_name(a.account_id), f"{a.score or 0.0:.2f}", a.state, a.n_requests)
                                for a in top])
    blocks = term.rows_table("last blocks", ["account", "reason"],
                             [(b["pseudonym"], b["reason"]) for b in list(w.recent_blocks)[:10]])
    camps = term.rows_table("campaigns", ["id", "size", "score", "state"],
                            [(c.id, len(c.members), f"{c.score:.2f}", c.state)
                             for c in sorted(w.campaigns.values(), key=lambda c: -c.score)])
    return term.panel(f"Distillation Watchdog :{CFG['ports']['watchdog']}", head, accounts, blocks, camps)


async def serve() -> None:
    store = EventStore(cfg_path("sqlite"))
    worker = Worker(store=store)
    if not worker.score:
        log.warning("no account model in %s: the server computes features only", worker.models_dir)
    app = create_app(worker, store)
    config = uvicorn.Config(app, host="127.0.0.1", port=CFG["ports"]["watchdog"], log_level="warning")
    server = uvicorn.Server(config)
    view = asyncio.create_task(term.run_live(lambda: render(app.state.hub), CFG["watchdog"]["term_interval_s"]))
    try:
        await server.serve()
    finally:
        view.cancel()
        store.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from dw.common import embed
    embed.load_cache(cfg_path("cache") / "emb" / "emb.npz")   # vectors from train.sh and eval.sh
    asyncio.run(serve())


if __name__ == "__main__":
    main()
