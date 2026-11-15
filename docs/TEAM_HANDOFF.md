# Handoff for a teammate's Claude Code session

This file is for a Claude Code session on a teammate's machine. That machine has:

1. a clone of this repository (`git clone https://github.com/ezou1/distillation-watchdog.git`);
2. the archive `dw_demo_bundle.tar.gz` (98 MB), which the project owner sends separately.

With these, the session can run the demo and change the code. The session cannot retrain the models; see "What needs the full data".

`docs/HANDOFF.md` is an older handoff between build sessions. It is history: do not follow its steps.

Read next, in this order: `README.md` (architecture, results, limits) and `config.yaml` (all settings, with comments).
`BUILD.md` is the original specification. `BUILD_LOG.md` gives each decision (D-xx) and problem (P-xx) with its reason.

## 1. Setup (about 5 minutes)

The system runs on Linux or WSL (tested on Ubuntu 24.04) with Python 3.12. It needs no GPU.

```bash
cd distillation-watchdog
tar -xzf /path/to/dw_demo_bundle.tar.gz          # run in the repository root: it writes data/, models/, cache/
curl -LsSf https://astral.sh/uv/install.sh | sh  # if uv is not installed
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python -e .
cp .env.example .env                             # then set OPENROUTER_API_KEY (see below)
sudo apt-get install -y tmux                     # optional: demo.sh uses tmux if it is installed
```

The archive contains these files:

| Path | Content |
|---|---|
| `data/splits/holdout.jsonl`, `data/splits/manifest.json` | The holdout split. The demo replays it. It contains real SWE-chat conversations: do not commit it, and do not send it to an external API. |
| `data/synth/` | The attack assets: stealth prompts, fake tool outputs, and elicitation banks A and B. They were written by an LLM and cannot be regenerated identically. |
| `data/timing_fit.json` | The request timing model of the simulated users. |
| `models/rules.json` | The D1 rules thresholds (block and review). |
| `models/account_model.pkl`, `models/request_model.pkl` | The learned model (logistic regression on the D4 features) and its thresholds. |
| `models/d3_kmeans.pkl`, `models/d4_keywords.txt`, `models/context_metrics.json` | The D3 topics, the keyword baseline for the evaluation, and the context-model metrics. |
| `cache/context/qwen3-0.6b-base-v2.jsonl` | Cached context-model scores of every text that the replay sends at the scales small, demo, and full. |
| `cache/emb/emb.npz` | Cached text embeddings (D3). They make the start faster. |

Keys in `.env`:

- `OPENROUTER_API_KEY` is needed only for the agent-chat pane, where a person talks to the lab model. The replayed traffic calls no external API. The calls use the teammate's own key and are billed to it.
- `HF_TOKEN` is not needed. The embedding model (`BAAI/bge-small-en-v1.5`) downloads from a public source on the first start.
- `DW_POD_HOST` and `DW_POD_PORT` are not needed. They are only for the GPU pod (see section 4).

## 2. Check the setup, then run the demo

```bash
OMP_NUM_THREADS=1 .venv/bin/pytest -q -p no:cacheprovider   # unit tests; they use a stand-in for the context model
scripts/smoke.sh                                             # end to end: expect 6 PASS lines
scripts/demo.sh                                              # start the five processes
scripts/stop.sh                                              # stop them
```

`smoke.sh` and `demo.sh` use the same ports (8000–8003 and 8010). Run `scripts/stop.sh` before starting the other one.

The addresses:

| Address | What it is |
|---|---|
| http://127.0.0.1:8010/app/ | Baker Street: the dashboard as an evidence board (needs the front-end build, see section 3) |
| http://127.0.0.1:8010 | The plain-HTML dashboard: Live, Account, Campaigns, Evaluation. It works without the build. |
| http://127.0.0.1:8001 | Normal traffic generator (start and stop personas) |
| http://127.0.0.1:8002 | Attack traffic generator |
| http://127.0.0.1:8003 | Agent chat (needs `OPENROUTER_API_KEY`) |
| http://127.0.0.1:8000 | Lab API (OpenAI-compatible) |

With tmux, `tmux attach -t dw` shows the five panes. Without tmux, the logs are in `logs/<process>.log`.

The demo script is in README "Demo". What to expect at the demo scale:

- Hydra (60 accounts) and fake environment (15) end fully blocked by the rules score, in less than a minute.
- Stealth spreads its traffic over 48 simulated hours, about 5 real minutes at the demo speed (`sim.speed: 600`).
  - If it is stopped early, most stealth accounts show `insufficient` (fewer than 30 requests) or `normal`.
  - Run to the end, the result is about 10 of 20 blocked, 5 in review, and 5 normal. This is a known limit, not a bug.
- In the generator tables, "Blocked" counts requests, not accounts. For account states, look at the dashboard.

## 3. How the system works (short)

```
generators (8001, 8002), agent chat (8003) ──► lab gateway (8000) ──► replay backend or OpenRouter
                                                     │ request events (x_replay removed)  ▲ block list
                                                     ▼                                     │
                                                watchdog (8010)
  detectors ─► rules score (D1, D7 exoneration) ─┐
            └─► learned model (D4 features) ──────┼─► state = most severe ─► block list, dashboard, case files
  campaigns (Louvain over linked accounts) ──────┘
```

- **Rules score** (`dw/watchdog/rules.py`): the D1 duplicate rate against `models/rules.json`. D7 can only lower it: an account with 5 or more distinct `user` values, or a declared use, gets at most "review".
- **Learned model** (`dw/watchdog/account_model.py`): a logistic regression on the D4 features. Which detector groups it uses is set by `account.model_groups` in `config.yaml`. D2, D3, and D6 are computed but not scored, because they failed the validation check (BUILD_LOG D-49).
- **Policy** (`dw/watchdog/policy.py`): the account state is the most severe of the rules level, the model level, and the campaign level. A declared benchmark account within its quota is allowed. Below `account.min_requests` (30), the state is `insufficient`.
- **Context model** (`dw/common/context.py`): it gives a probability for each user prompt (D4) and tool output (D6). Scores come from the disk cache first, then from the scoring service on a GPU machine. Without the service, a text that is not in the cache has no score. Then the learned model can only raise an account to "review", not "block" (D-50). The rules score and the campaigns still block.
- **Worker** (`dw/watchdog/worker.py`): ingests events, updates the detectors, scores the accounts, and records snapshots.
- **Simulation** (`dw/sim/`): the personas (`personas.py`) and the scenario builder (`scenario.py`). The CLI (`cli.py`) replays the holdout split. The offline replay (`offline.py`) runs the watchdog in the same process, without ports.
- **Front end**: `static/dashboard.{html,js,css}` (served by `dw/watchdog/server.py`), `static/panel.html` (generators), `static/agent.html` (agent chat). The API is in `docs/API.md`.
- **React app** (`frontend/`, served at `/app` on each server). `frontend/dist` is not in git: build it after each pull.
  - Build: `cd frontend && npm install && npm run build`. If `npm run build` stops with "tsc: Permission denied", use `node node_modules/typescript/bin/tsc --noEmit && node node_modules/vite/bin/vite.js build`.
  - If `/app/` gives 404, the servers started before the build was there: run `scripts/stop.sh`, then `scripts/demo.sh`.
  - On the watchdog (8010) the app is **Baker Street**: a pinboard with the request-rate chart, five postcards (Recent activity, Accounts list, Suspicious messages, Active metrics, Agent graph), and the Evaluation folder. The code is in `frontend/src/baker/`, and all its styles are in `baker.css` below the class `html.baker`. `docs/BAKER_STREET_PROMPT.md` is the specification; screenshots are in `docs/baker-street/`.
  - On the generators (8001, 8002) and the agent chat (8003) the app keeps the earlier look. They share `App.tsx`, `index.css`, and some components with the dashboard: do not restyle shared code. A change to Baker Street must leave those pages the same.
  - The chart keeps its history in the browser (`localStorage`), because the watchdog keeps only 4 simulated hours. Suspicious messages reads the evidence of at most 20 blocked and 20 suspicious accounts.

## 4. Rules for code changes

These rules hold the evaluation together. Keep them.

- **The watchdog never sees `x_replay`, labels, or the data source.** The gateway removes `x_replay`. Detectors get a `DetectorView` without assistant prose, source, or label. The dashboard never shows persona names or sources.
- **Never use the holdout split to fit a model or a threshold.** Fit on train, select on validation, report on holdout.
- **SWE-chat text never goes to OpenRouter** or to any other external API. The only approved exception is the project owner's GPU pod.
- **Never commit `data/`, `cache/`, `models/`, or `.env`.** They are in `.gitignore`.
- **Do not change the text format of the context model** (`context_model.*_chars`, `head_tail`, `prompt_text`, `realism_text`) or `context_model.model_tag`. The cache keys are the exact texts. After such a change, no text is in the cache, and D4 gives no scores until a GPU service rescores them.
- **Do not rename or remove D4 feature names** (`d4_*` in `dw/watchdog/detectors/elicitation.py`) or the rules feature `d1_dup_rate`. The saved models refer to them by name. A missing feature does not crash: it silently caps the model at "review".
- Changes to the dashboard, the generators, the agent chat, the case files, the campaign display, the policy logic, and the demo flow are safe. They need no retraining.
- Tests: `tests/conftest.py` replaces the context model with `tests/fake_scorer.py` in every test. No test calls OpenRouter or the GPU service. Add a test for each change in behaviour, and keep `pytest` and `scripts/smoke.sh` passing.
- To check a change without the five processes, replay offline. For example, at the small scale:

  ```python
  from pathlib import Path
  from dw.common.config import CFG
  from dw.sim.personas import ROLE_PERSONAS
  from dw.sim.scenario import build_scenario
  from dw.sim.offline import run_offline
  from dw.watchdog.worker import Worker
  from dw.watchdog.store import EventStore

  personas = ROLE_PERSONAS["attack"] + ROLE_PERSONAS["normal"]
  sc = build_scenario("holdout", CFG["account"]["holdout_seed"], "small", list(personas))
  w = Worker(models_dir=Path("models"), score=True, store=EventStore(None))
  run_offline(sc, w)
  # sc.labels[account_id]["persona"] is the label (for evaluation only); w.accounts[account_id].state is the decision
  ```

  The expected result:
  - hydra 12 of 12 block, fake environment 4 of 4 block;
  - stealth 1 block and 3 review;
  - solo developers 7 normal and 1 review;
  - the SaaS app normal, the benchmark runner `disclosed`.

## 5. What needs the full data, a GPU, or the owner

| Task | Needs |
|---|---|
| `scripts/train.sh` (retrain the rules thresholds and the learned model) | `data/splits/train.jsonl` and `val.jsonl` (1.1 GB), which are not in the archive, and the context-model scores of those texts. Ask the project owner. |
| `scripts/eval.sh` (the holdout evaluation and the tables in README "Results") | The validation split and the context-model scores of the full scale (the latter are in the archive). Ask the project owner. |
| Rebuild the data from scratch (`scripts/build_data.sh`) | `HF_TOKEN` with access to the gated `SALT-NLP/SWE-chat`, about 6.4 GB of downloads, and an `OPENROUTER_API_KEY` for the synthetic assets. The new assets would differ from the archived ones. |
| Score new texts with the context model (for example, the agent-chat prompts) | The model weights (`models/context_model/`, 2.3 GB; ask the owner) and a GPU machine. Steps: README "Context model". `scripts/context_serve.py` runs on CUDA only. |
| Change the context model or fix D6 | A GPU (see BUILD_LOG D-49 for the D6 fix: fake outputs for SWE-chat contexts). |

## 6. Known limits (do not report them as new bugs)

- **Stealth recall is about 0.5–0.6**, and early detection is weak. Stealth accounts copy normal use. Their evidence grows only with volume.
- **D6 flags about 20% of real SWE-chat tool outputs**, so it gives evidence but does not score (D-49).
- **The agent-chat account shows `unknown` or `insufficient`** until it has 30 requests. Its new prompts are not in the cache, so D4 does not score them without the GPU service.
- **About 1 in 64 normal accounts is falsely blocked** on the holdout. The owner accepts this: people can appeal.
- **The format probe fails** (AUC 0.999), and the owner decided to ignore it. Other limits are in README "Limits".

## 7. Machine notes

- **WSL:** run Claude Code inside the Linux terminal, not from Windows on a `\\wsl.localhost` path.
- **Stopping processes:** use `scripts/stop.sh` or kill by PID. Never `pkill -f` in the same command as other work: the pattern can match the shell of the command itself and kill it.
- **Memory:** each training worker needs up to 4.5 GB (`train.processes`). The demo and the tests need little memory.
- **Event store:** the watchdog appends every event to `data/watchdog_events.sqlite`.
  - The watchdog does not reload it on start: each restart begins with an empty dashboard.
  - The file only grows (several GB after many demo runs). It is safe to delete while the processes are stopped.
