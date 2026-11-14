"""Checks for scripts/smoke.sh against the five running processes. Exit code 1 on any failure."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

from dw.common.config import CFG

LAB = f"http://127.0.0.1:{CFG['ports']['lab']}"
WD = f"http://127.0.0.1:{CFG['ports']['watchdog']}"
AGENT = f"http://127.0.0.1:{CFG['ports']['agent']}"


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    return ok


def main(labels_paths: list[str]) -> int:
    labels: dict[str, dict[str, str]] = {}
    for p in labels_paths:
        labels.update(json.loads(Path(p).read_text()))
    c = httpx.Client(timeout=60)
    rows = {r["id"]: r for r in c.get(f"{WD}/api/accounts", params={"limit": 5000}).json()["accounts"]}
    state = {a: rows[a]["state"] for a in rows}
    hydra_blocked = [a for a, l in labels.items() if l["persona"] == "hydra" and state.get(a) == "block"]
    solo_blocked = [a for a, l in labels.items() if l["persona"] == "solo_dev" and state.get(a) == "block"]
    results = [
        check("at least one hydra account is blocked", bool(hydra_blocked),
              f"({len(hydra_blocked)} of {sum(l['persona'] == 'hydra' for l in labels.values())})"),
        check("no solo-dev account is blocked", not solo_blocked, f"({len(solo_blocked)} blocked)"),
    ]
    if hydra_blocked:
        acct = hydra_blocked[0]
        resp = c.post(f"{LAB}/v1/chat/completions", headers={"Authorization": f"Bearer {acct}"},
                      json={"model": "x", "messages": [{"role": "user", "content": "hi"}]})
        results.append(check("the lab returns 403 to a blocked account", resp.status_code == 403, str(resp.status_code)))
        detail = c.get(f"{WD}/api/accounts/{acct}").json()
        ev = detail.get("evidence") or []
        opened = c.get(f"{WD}/api/evidence/{ev[0]['id']}").json() if ev else {}
        results.append(check("/api/evidence opens a message for a block",
                             bool(opened.get("request", {}).get("messages")), f"({len(ev)} evidence items)"))
    chat = c.post(f"{AGENT}/api/chat", json={"message": "Say hello.", "tools": False}, timeout=120).json()
    results.append(check("the agent chat answers through the lab", chat.get("status") == "done", chat.get("status", "")))
    you = c.get(f"{WD}/api/accounts/{CFG['agent']['account_key']}")
    you_state = you.json().get("state") if you.status_code == 200 else "unknown"
    results.append(check("the agent-chat account is not blocked", you_state != "block", f"({you_state})"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
