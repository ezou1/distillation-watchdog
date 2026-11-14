# Workstream contracts

This document gives the interfaces between the workstreams. The code contracts are in `dw/common/` and `dw/watchdog/detectors/base.py`. Change a contract only with a note in `BUILD_LOG.md` and an update to every user.

## 1. Data files (owner: WS-A)

| File | Format |
|---|---|
| `data/raw/swechat/*.parquet` | Downloaded tables (already present: `sessions`, `conversations`, `repositories`). |
| `data/raw/openswe.parquet`, `data/raw/openswe_xharness.parquet` | Streamed rows (Claude Code split, `sweagent` split). |
| `data/canonical/{swechat,openswe,openswe_xharness}.jsonl` | One `Session` JSON per line, normalized and scrubbed. |
| `data/splits/{train,val,holdout}.jsonl` | `Session` lines of both sources. The split key is `group_id`. |
| `data/splits/xharness.jsonl` | Normalized cross-harness sessions. A second holdout. Never for training. |
| `data/splits/manifest.json` | `{"seed": int, "groups": {"train": [...], "val": [...], "holdout": [...]}, "files": {"train": {"path": str, "sha256": str}, ...}}` |
| `data/timing_fit.json` | `{"think_time": {"mu": f, "sigma": f}, "step_gap": {"mu": f, "sigma": f}, "start_hour_probs": [24 floats], "sessions_per_active_day": {"mean": f, ...}, "return_gap_days": {"mu": f, "sigma": f}}` (lognormal parameters of seconds or days). |
| `data/synth/fake_tool_outputs.jsonl` | `{"session_id": str, "split": str, "message_index": int, "fake_text": str}`. `message_index` is the index of a `tool_result` in the canonical session. |
| `data/synth/stealth.jsonl` | `{"session_id": str, "split": str, "paraphrase": str, "followups": [str, ...]}` |
| `data/synth/bank_a.json`, `data/synth/bank_b.json` | `{"elicit": [{"text": str, "category": str}], "benign": [{"text": str}]}`. Bank A is for train and validation. Bank B is for holdout. |
| `data/synth/templates.json` | `[{"id": 1, "text": "... {repo} ... {issue} ... {files} ...", "split": "train"}, ...]`. IDs 1-4 are `train` (train and validation scenarios). IDs 5-6 are `holdout`. |

Use the sample fixtures in `tests/fixtures/` until the real files exist.

## 2. Scenario and lab (owner: WS-B)

```python
# dw/sim/scenario.py
@dataclass
class SimRequest:
    sim_time: float
    key: str                  # bearer key = account ID
    body: dict                # OpenAI request body, with private "x_replay": {"step": [Message dicts]}
    persona: str              # label, used only by the sender counters and evaluation
    session_ref: str          # simulator session ID (label side, never sent to the lab)
    final: bool               # True if this step ends the trajectory at the model stop

@dataclass
class Scenario:
    requests: list[SimRequest]               # sorted by sim_time
    labels: dict[str, dict[str, str]]        # account_id -> {"persona": ..., "role": "normal"|"attack"}
    registrations: list[dict]                # bodies for POST /admin/accounts

def build_scenario(split: str, seed: int, scale: str, personas: list[str] | None = None,
                   training: bool = False, xharness: bool = False) -> Scenario: ...
```

- `split` is `train`, `val`, or `holdout`. The function reads only that split file and the matching synthetic assets.
- `training=True` applies domain randomization and the light stealth persona.
- `xharness=True` makes the hydra persona use `data/splits/xharness.jsonl` (leakage control 4).

```python
# dw/lab/gateway.py
class Lab:
    def __init__(self, sink: EventSink, blocklist: BlocklistSource, live: LiveBackend | None = None): ...
    def register(self, account_id: str, declared_use: str | None, quota: int | None) -> None: ...
    async def handle(self, key: str | None, body: dict, sim_time: float | None) -> tuple[int, dict]: ...
```

```python
# dw/sim/offline.py
def run_offline(scenario: Scenario, worker: Worker) -> OfflineResult: ...
```

`run_offline` builds a `Lab` with `DirectSink(worker)` and `DirectBlocklist(worker)`. It calls `lab.handle()` for every request in time order. It calls `worker.rescore()` every `watchdog.offline_rescore_every` events and `worker.run_campaigns()` every `watchdog.offline_campaign_every` events. `OfflineResult` has, per request: account, persona, status, sim_time; and per account: the first block time and the number of requests before the block.

## 3. Watchdog worker (owner: WS-C)

```python
# dw/watchdog/worker.py
class Worker:
    accounts: dict[str, AccountState]
    campaigns: dict[str, Campaign]
    def __init__(self, models_dir: Path | None = None, store: EventStore | None = None,
                 score: bool = True): ...
    def ingest(self, ev: RequestEvent) -> None        # rebuild session, call observe() of all detectors
    def rescore(self) -> list[dict]                   # features, score, policy for dirty accounts; returns flag/block events
    def run_campaigns(self) -> list[dict]             # account graph + Louvain; returns campaign events
    def blocklist(self) -> Blocklist                  # see dw/common/sink.py
    def feature_table(self) -> dict[str, dict[str, float]]   # account_id -> features (training)
```

- With `score=False` (training), the worker computes features only. It needs no trained account model.
- A detector that needs a trained model and finds none returns neutral features (0.0). Tests use this.
- Feature names start with the detector ID, for example `d1_dup_rate`, `d7_req_per_active_hour`.
- `EventStore` is the append-only SQLite log. It stores request bodies zlib-compressed.

## 4. Dashboard (owner: WS-D)

The dashboard uses only the endpoints in `docs/API.md`.

## 5. Running commands

The repository is in WSL. Run every command inside WSL:

```bash
wsl -d Ubuntu-24.04 -- bash -l <<'EOF'
cd ~/distillation-watchdog
.venv/bin/python -m pytest -q
EOF
```

Edit files through `\\wsl.localhost\Ubuntu-24.04\home\eric\distillation-watchdog\...`.
