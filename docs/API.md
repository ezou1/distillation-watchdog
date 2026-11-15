# HTTP API

This document gives every HTTP endpoint of the five processes. All processes bind `127.0.0.1`. All bodies are JSON unless the table says different.

| Process | Port | Module |
|---|---|---|
| Lab (model API gateway) | 8000 | `dw.lab.gateway` |
| Normal traffic generator | 8001 | `dw.sim.cli --role normal` |
| Attack traffic generator | 8002 | `dw.sim.cli --role attack` |
| Agent chat | 8003 | `dw.agent.server` |
| Watchdog | 8010 | `dw.watchdog.server` |

## Shared terms

- **Account**: the identity of a caller. The lab uses the bearer key as the account ID. The UI shows only a pseudonym (`acct-3f2a9c`).
- **Simulation time**: seconds since the Unix epoch on the simulated clock. All time features use it.
- **Canonical message**: `{"role": "user|assistant|tool_call|tool_result", "text": str, "tool": "read|write|edit|shell|search|other"|null, "args": {...}|null}`.
- **Account state**: one of `insufficient`, `normal`, `review`, `block`, `disclosed`.

---

## 1. Lab (port 8000)

### `POST /v1/chat/completions`

OpenAI-compatible chat completion. The lab accepts `Content-Encoding: gzip` request bodies.

Headers:

- `Authorization: Bearer <account key>` (required). An unknown key makes a new account.
- `X-Sim-Time: <float>` (optional). If it is not present, the lab uses its current simulation time.

Request:

```json
{
  "model": "qwen/qwen3-coder-next",
  "messages": [{"role": "user", "content": "Fix the failing test in parser.py"}],
  "tools": [{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
  "temperature": 1.0,
  "n": 1,
  "max_tokens": 32768,
  "reasoning_effort": "high",
  "user": "end-user-17",
  "x_replay": {"step": [{"role": "tool_call", "text": "", "tool": "read", "args": {"path": "<REPO>/parser.py"}}]}
}
```

`x_replay` is private to the simulator. If it is present, the lab returns the recorded step and does not call a model. The lab removes `x_replay` before it makes the event for the watchdog.

Response 200 (OpenAI format):

```json
{
  "id": "chatcmpl-replay", "object": "chat.completion", "created": 1767571200,
  "model": "qwen/qwen3-coder-next",
  "choices": [{"index": 0, "finish_reason": "tool_calls",
    "message": {"role": "assistant", "content": "",
      "tool_calls": [{"id": "call_1", "type": "function",
        "function": {"name": "read_file", "arguments": "{\"path\": \"<REPO>/parser.py\"}"}}]}}],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

Response 403 (account or campaign is on the block list):

```json
{"error": "blocked", "reason": "campaign cmp-0003: shared tasks, synchronized starts", "case_id": "cmp-0003"}
```

### `POST /admin/accounts`

Registers an account with a declared use and a quota.

```json
{"account_id": "acct-k3j9...", "declared_use": "benchmark", "quota": 20000}
```

Response: `{"ok": true}`

### `GET /health`

```json
{"status": "ok", "sim_time": 1767574800.0, "watchdog_connected": true}
```

### `GET /stats`

```json
{"rps": 412.5, "served": 10234, "blocked": 1820, "replay": 12040, "live": 14,
 "p50_ms": 0.9, "p95_ms": 2.7, "watchdog_connected": true,
 "events_dropped": 0, "sim_time": 1767574800.0}
```

`events_dropped` counts the request events that the lab could not send to the watchdog.

---

## 2. Traffic generators (ports 8001 and 8002)

The generators serve these endpoints only with `--ui`. Both roles use the same endpoints.

### `GET /`

Returns `static/panel.html`.

### `GET /api/status`

```json
{"role": "attack", "scale": "demo", "speed": 600, "sim_time": 1767574800.0,
 "personas": {"hydra": {"running": true, "accounts": 60, "sessions_planned": 1480, "sent": 5120, "served": 4300,
                         "blocked": 820, "leaked": 37}}}
```

`leaked` is the number of complete trajectories (sessions that reached the model stop) that the persona received before its accounts were blocked.

`sessions_planned` is the number of trajectories of the persona that can leak: the sessions of its scenario that end at a model stop. The leak rate is `leaked / sessions_planned`. It is `null` until the generator has built the scenario of the scale, which happens at the first start.

### `POST /api/start`

```json
{"persona": "hydra", "scale": "demo", "speed": 600}
```

Response: `{"ok": true}`

### `POST /api/stop`

```json
{"persona": "hydra"}
```

Response: `{"ok": true}`

---

## 3. Agent chat (port 8003)

### `GET /`

Returns `static/agent.html`.

### `POST /api/chat`

Sends one user message. The agent runs until a final answer, a shell approval, or the step limit.

```json
{"message": "Write a function add(a, b) and a test for it.", "tools": true}
```

Response (`status` is `done`, `awaiting_approval`, `step_limit`, or `error`):

```json
{
  "status": "awaiting_approval",
  "steps": 3,
  "messages": [
    {"role": "user", "content": "Write a function add(a, b) and a test for it."},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "name": "write_file", "arguments": {"path": "add.py", "content": "..."}}]},
    {"role": "tool", "tool_call_id": "call_1", "name": "write_file", "content": "Wrote 42 bytes to add.py"}
  ],
  "pending": {"approval_id": "ap-0001", "command": "python -m pytest -q"}
}
```

### `POST /api/approve`

Approves or rejects the pending shell command. The agent continues. The response has the same format as `POST /api/chat`.

```json
{"approval_id": "ap-0001", "approve": true}
```

### `GET /api/session`

Returns the current conversation in the same format as `POST /api/chat` (status `idle` if no turn runs).

### `POST /api/session/new`

Starts a new conversation. The sandbox files stay. Response: `{"ok": true}`

Send the header `Content-Type: application/json` and a JSON body (`{}` is correct). Without it, the server returns 415. This protects against cross-site requests.

### `GET /api/files`

```json
{"files": [{"path": "add.py", "size": 42}, {"path": "test_add.py", "size": 120}]}
```

---

## 4. Watchdog (port 8010)

### `POST /ingest`

The lab sends a batch of `RequestEvent` objects every 200 ms. See `dw/common/schema.py` for all fields.

```json
{"events": [{"request_id": "req-000123", "account_id": "acct-k3j9", "sim_time": 1767574800.0,
  "model": "qwen/qwen3-coder-next", "tools_schema_id": "standard_agent", "temperature": 1.0, "n": 1,
  "max_tokens": 32768, "reasoning_effort": "high", "user": null,
  "messages": [{"role": "user", "text": "Fix the failing test", "tool": null, "args": null}],
  "prefix_hashes": ["9a1c...", "77be..."], "key": "77be...", "dup_count": 3, "global_dup_count": 3,
  "inline_flags": ["dup"], "status": 200, "finish_reason": "tool_calls", "backend": "replay",
  "latency_ms": 0.8, "declared_use": null, "quota": null}]}
```

Response: `{"accepted": 1, "dropped": 0}`

### `GET /blocklist`

```json
{"version": 42, "accounts": {"acct-k3j9": {"reason": "score 0.97: duplicates at high temperature", "case_id": "acct-k3j9"}}}
```

### `GET /api/summary`

```json
{"sim_time": 1767574800.0, "rps": 410.2, "served": 10234, "blocked": 1820,
 "accounts": {"insufficient": 40, "normal": 120, "review": 6, "block": 55, "disclosed": 3},
 "campaigns": 2, "queue_depth": 0, "dropped": 0,
 "rate_history": [{"t": 1767574790.0, "served": 400, "blocked": 12}]}
```

### `GET /api/accounts?state=&sort=score&limit=50`

`state` filters by account state. `sort` is `score` (default), `requests`, or `last_seen`. Rows never contain the persona label or the data source.

`score` is the learned model score (D2, D3, D4, D6). `rules_level` is the level from the rules score (D1 repeated contexts, with the D7 exoneration): `normal`, `review`, `block`, or `null` before 30 requests. The state is the most severe of the model level, the rules level, and the campaign. `sort=score` sorts by the stronger of the model score and the rules level.

```json
{"accounts": [{"id": "acct-k3j9", "pseudonym": "acct-3f2a9c", "you": false, "score": 0.97, "rules_level": "block",
  "state": "block", "requests": 812, "campaign_id": "cmp-0003",
  "reasons": ["d6_fake_prob_mean +2.1", "d4_elicit_mean +0.9"],
  "history": [[1767571800.0, 0.2], [1767572400.0, 0.8]]}]}
```

### `GET /api/accounts/{id}`

```json
{"id": "acct-k3j9", "pseudonym": "acct-3f2a9c", "you": false, "score": 0.97, "state": "block",
 "requests": 812, "campaign_id": "cmp-0003", "declared_use": null,
 "features": [{"name": "d1_dup_rate", "value": 0.71, "percentile": 99.2}],
 "contributions": [{"name": "d1_dup_rate", "value": 2.1}],
 "evidence": [{"id": "ev-0000412", "detector": "d1_redundancy", "account_id": "acct-k3j9",
   "request_ids": ["req-000123"], "message_index": 4,
   "summary": "The same context was sent 8 times at temperature 1.0.", "weight": 1.0, "sim_time": 1767574800.0}],
 "history": [[1767571800.0, 0.2], [1767572400.0, 0.8]]}
```

### `GET /api/evidence/{id}`

Returns the evidence item, the exact request, and the message that the evidence points to. The text is scrubbed canonical text.

```json
{"evidence": {"id": "ev-0000412", "detector": "d1_redundancy", "summary": "...", "request_ids": ["req-000123"], "message_index": 4},
 "request": {"request_id": "req-000123", "account_id": "acct-k3j9", "pseudonym": "acct-3f2a9c", "sim_time": 1767574800.0,
   "temperature": 1.0, "n": 1, "max_tokens": 32768, "tools_schema_id": "standard_agent",
   "messages": [{"index": 0, "role": "user", "text": "...", "tool": null, "args": null}]},
 "message": {"index": 4, "role": "tool_result", "text": "...", "tool": "read", "args": null}}
```

### `GET /api/campaigns`

```json
{"campaigns": [{"id": "cmp-0003", "size": 58, "score": 2.4, "state": "block",
  "mean_member_score": 0.81, "mean_edge_weight": 1.3, "top_edge_types": ["shared_task", "sync"]}]}
```

### `GET /api/campaigns/{id}`

```json
{"id": "cmp-0003", "size": 58, "score": 2.4, "state": "block",
 "members": [{"id": "acct-k3j9", "pseudonym": "acct-3f2a9c", "score": 0.97, "state": "block"}],
 "edges": [{"source": "acct-k3j9", "target": "acct-77qa", "weight": 1.8,
   "types": {"shared_task": 1.0, "sync": 0.8}, "evidence": "4 shared tasks; per-minute correlation 0.83"}],
 "evidence": [{"id": "ev-0000500", "detector": "campaign", "summary": "..."}]}
```

### `POST /api/campaigns/{id}/casefile` and `POST /api/accounts/{id}/casefile`

```json
{"id": "cmp-0003", "status": "ok", "cached": false,
 "text": "## Findings\nThe campaign has 58 accounts that share 41 tasks [ev-0000500].\n...",
 "cited": ["ev-0000500"], "evidence": [{"id": "ev-0000500", "summary": "..."}]}
```

If the validator rejects the LLM text two times, `status` is `fallback` and `text` is empty. The UI then shows only the evidence list. The field `reason` gives the last rejection reason. The watchdog log also shows it.

### `GET /api/graph`

Nodes and edges of the candidate accounts.

```json
{"nodes": [{"id": "acct-k3j9", "pseudonym": "acct-3f2a9c", "score": 0.97, "state": "block", "campaign_id": "cmp-0003", "you": false}],
 "edges": [{"source": "acct-k3j9", "target": "acct-77qa", "weight": 1.8, "types": {"shared_task": 1.0}}]}
```

### `GET /api/stream`

Server-Sent Events. Event types:

- `summary`: the `GET /api/summary` body, one per second.
- `flag`: `{"account_id": "...", "pseudonym": "...", "state": "review", "score": 0.6, "reason": "..."}`.
- `block`: the same fields as `flag`, with `state` = `block`.
- `campaign`: `{"id": "cmp-0003", "size": 58, "score": 2.4, "state": "block"}`.

### `GET /api/eval`

```json
{"metrics": {"...": "reports/metrics.json"}, "controls": {"...": "reports/controls.json"},
 "compute": {"...": "reports/compute.json"}, "load": {"...": "reports/load.json"},
 "images": ["/reports/roc_pr.png", "/reports/per_request_vs_account.png"]}
```

The watchdog serves the PNG files at `/reports/<name>.png`.

### `POST /admin/reset`

Clears the live state (accounts, campaigns, block list, counters). Response: `{"ok": true}`

### `GET /`

Returns `static/dashboard.html`. Static files are at `/static/...`.
