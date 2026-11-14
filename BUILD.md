# Distillation Watchdog: one-shot build prompt

This file has two parts.

- **Part A** is for the team. Do these steps before you start the build agent.
- **Part B** is the prompt. Paste all of Part B into Claude Code. Do not paste Part A.

---

# PART A. FOR THE TEAM (DO NOT PASTE)

## A1. Keys that you must supply

Put the keys in a file `.env` in the repository root. Do not commit this file.

```
HF_TOKEN=hf_...
OPENROUTER_API_KEY=sk-or-v1-...
```

| Key | What it does | Where to get it |
|---|---|---|
| `HF_TOKEN` | Downloads SWE-chat (gated) and Open-SWE-Traces. | 1. Log in at https://huggingface.co. 2. Go to https://huggingface.co/settings/tokens. 3. Click **Create new token**. Select type **Read**. 4. Go to https://huggingface.co/datasets/SALT-NLP/SWE-chat. Accept the access conditions with the **same account**. Access is not available until you accept. 5. Open https://huggingface.co/datasets/nvidia/Open-SWE-Traces. If it shows a gate, accept it too. |
| `OPENROUTER_API_KEY` | Calls the open-weight model for the coding-agent chat and for the small LLM jobs (fake tool outputs, paraphrases, phrase banks, case files). | 1. Log in at https://openrouter.ai. 2. Go to https://openrouter.ai/settings/credits. Add USD 15 of credit. 3. Go to https://openrouter.ai/settings/keys. Click **Create API Key**. Set a credit limit of USD 15 on the key. The key starts with `sk-or-v1-`. |

Expected OpenRouter cost for the build and tests: less than USD 10. No GPU is necessary. All training runs on the CPU.

Claude Code uses your Claude Max login. That is separate from these keys.

## A2. Before you start

1. Use one machine. Python 3.11 or later. 16 GB RAM or more. 15 GB free disk. Install `tmux` if you can (the demo script uses it to show all terminals at once).
2. Make an empty folder. Put `.env` in it. Start Claude Code in that folder with the strongest model.
3. Give Claude Code permission to run shell commands and edit files in this folder without asking each time. If it must ask, the build stops until you answer.
4. Paste Part B. Let it run. It plans for about 7 hours. Keep the last hour for your own checks and the demo rehearsal.

## A3. Licences and terms (short)

- The lab model is an open-weight Qwen coder model. The build only selects models that OpenRouter lists with a Hugging Face weights repository.
- Bulk attack traffic **never** goes to OpenRouter. It replays recorded responses from the datasets against **our own** gateway on localhost. Only the coding-agent chat and the small helper jobs call OpenRouter. These are normal, low-volume coding requests.
- We do not train any model on the outputs of the hosted model.
- SWE-chat is gated (ODC-BY). Do not put raw data in a public repository. The agent adds `data/` to `.gitignore`.
- Open-SWE-Traces is CC BY 4.0. The README must give attribution.
- We are not lawyers. If a judge asks, say this: "Attack traffic is simulated against our own gateway with public data. No third-party service receives attack traffic."

---

# PART B. BUILD PROMPT (PASTE EVERYTHING BELOW THIS LINE)

You are the lead build agent for a hackathon project. Build the full system from this specification. Work without human help. Do not ask questions. Stop only for the conditions in section B2.5.

## B1. Mission

We simulate a frontier AI lab. The lab serves an open-weight coding model through an OpenAI-compatible API. The lab receives normal traffic and **distillation attacks**. A distillation attack is bulk traffic whose purpose is to harvest model outputs as training data.

Everything runs on **one machine**, as five processes:

| Process | Port | Terminal view | Browser view |
|---|---|---|---|
| **Lab** (model API gateway) | 8000 | live: requests per second, served, blocked, latency | none |
| **Watchdog** (detection + dashboard) | 8010 | live: top accounts, new blocks, campaigns | investigator dashboard (required) |
| **Agent chat** (a real chatbot and coding agent that uses the lab) | 8003 | request log | chat UI with tools (required) |
| **Normal traffic generator** | 8001 | live counters | simple control panel (optional, `--ui`) |
| **Attack generator** | 8002 | live counters | simple control panel (optional, `--ui`) |

All processes bind `127.0.0.1`.

The core idea: one request is weak evidence. Many requests from one account are strong evidence. Links between accounts show a campaign. So the structure is: **weak per-request signals → strong per-account scores → campaigns from an account graph.**

The judging criteria are: analytical depth, a sound and reproducible pipeline, usefulness to an investigator, and visualization. Judges know ML. They will look for shortcut learning. Section B10 (leakage controls) is as important as the detectors.

## B2. Operating rules

### B2.1 Resources

- The build budget for Claude Code is not limited. Use the strongest available model for the lead agent and for all subagents.
- Use subagents in parallel (section B12). Give each subagent the contracts from Phase 0 and the sections of this specification that it needs.
- Run extensive tests. Tests are part of the deliverable.
- The OpenRouter budget is limited (USD 15 key limit). Cache all LLM calls on disk. Do not call OpenRouter in loops of tests. Use recorded fixtures for tests.

### B2.2 Time plan (soft)

1. At the start, write the current UTC time to `.build_clock`.
2. At each milestone in section B12, read `.build_clock` and compare the elapsed time with the plan. Write the result to `BUILD_LOG.md`.
3. The milestone times are guidance, not strict limits. If the work is more than 45 minutes behind the cumulative plan, apply the next cut from section B13. Record the cut.
4. **One firm rule:** at 6 h 30 min elapsed, stop new features. Use the remaining time to make the demo path and the evaluation report work, and to fix tests.

### B2.3 Code style

- **YAGNI.** Do not add features, options, or abstractions that this specification does not need.
- **DRY.** When a process occurs two or more times, make one shared function or class. Examples in this specification: one `Event` schema, one LLM client, one `Detector` interface, one persona simulator, one generator CLI for both traffic roles, one `EventSink` interface for HTTP and offline mode, one core `handle()` function for HTTP and offline evaluation, one terminal status helper for all five processes.
- Readable code: type hints, small functions, clear names, no clever one-liners. Every module starts with a docstring of 1 to 4 sentences.
- All numbers that a person can tune go in `config.yaml`. Do not hard-code thresholds in modules.
- Keep a short `BUILD_LOG.md`: decisions, cuts, problems, results. Write in ASD-STE100 (section B15).

### B2.4 Safety and data rules

- **Replay traffic and generators never execute commands.**
- The agent chat (section B7.5) is the only component that executes tool calls. It does this only in a sandbox folder, and every shell command needs a click of approval from the person in the UI.
- Do not use the client IP address as a feature. All traffic comes from localhost.
- Do not send SWE-chat text to any external API. Only Open-SWE-Traces text, generated text, and the person's own agent-chat text may go to OpenRouter.
- Hash all SWE-chat user IDs with a salted SHA-256. Show only pseudonyms (for example `dev-3f2a`) in the UI.
- Scrub secrets and personal data in normalization: e-mail addresses, API key patterns (`sk-`, `ghp_`, `github_pat_`, `AKIA`, `hf_`, `xox`), and home paths (`/Users/<name>`, `/home/<name>` become `/home/<user>`).
- Add `data/`, `cache/`, `agent_workspace/`, `.env`, and `*.sqlite` to `.gitignore`.
- Do not use OpenRouter `:free` model routes.

### B2.5 When to stop and ask

Stop and print a clear message only for these conditions:
- `.env` does not have `HF_TOKEN` or `OPENROUTER_API_KEY`.
- Hugging Face returns 401 or 403 for SWE-chat. Tell the user to accept the terms at https://huggingface.co/datasets/SALT-NLP/SWE-chat with the account that owns the token.
- OpenRouter returns 401 or 402. Tell the user to check the key or add credit at https://openrouter.ai/settings/credits.

For all other problems, choose the most reasonable solution, record it in `BUILD_LOG.md`, and continue.

## B3. Fixed decisions

| Item | Decision |
|---|---|
| Language | Python 3.11+. Use `uv` if it is installed. Else use `python -m venv` and `pip`. |
| Web | FastAPI + uvicorn. `httpx` (async) for clients. |
| Terminal views | `rich` (`Live` tables). One shared helper `dw/common/term.py`. |
| Process launcher | `scripts/demo.sh` starts all five processes in one `tmux` session (one pane each). If `tmux` is not installed, it starts them in the background with logs in `logs/` and prints the URLs. `scripts/stop.sh` stops them. |
| Lab to watchdog | The lab sends `RequestEvent` batches to the watchdog with `POST /ingest` every 200 ms (fire and forget, bounded buffer). The lab reads the block list from the watchdog with `GET /blocklist` every 1 s. If the watchdog is down, the lab continues to serve and logs a warning. |
| Storage | Watchdog: in-memory state plus an append-only SQLite event log (WAL mode, batch insert every 1 s). Request bodies stored zlib-compressed. |
| Data access | `huggingface_hub`, `datasets` (streaming), `duckdb` or `pyarrow` for parquet. |
| ML | `scikit-learn` (logistic regression, TF-IDF), `numpy`, `networkx` (Louvain: `networkx.community.louvain_communities`). |
| Embeddings | `fastembed` with `BAAI/bge-small-en-v1.5` on CPU. If `fastembed` fails to install, use `sentence-transformers` with `all-MiniLM-L6-v2` and the CPU PyTorch wheel. |
| LLM provider | OpenRouter, OpenAI-compatible: `https://openrouter.ai/api/v1`. |
| Lab model | Select at build time (section B4.3). Open-weight, tool calling, Qwen coder family. |
| Front ends | Static HTML pages with vanilla JS. Vendor Chart.js and Cytoscape.js into `static/vendor/` at build time. Do not load them from a CDN at run time. |
| Charts for the report | matplotlib, PNG files in `reports/`. |
| React | **Do not build.** Write section B16 to `docs/REACT_STAGE.md`. Build it only when a person asks. |

### Bulk replay, not live generation

Bulk traffic does not call the model. Each simulated request carries the recorded response from the dataset in a private field. The gateway returns that response. This costs nothing per request. It also lets one machine send tens of thousands of requests.

The agent chat calls the real model through the lab gateway, which forwards to OpenRouter.

The watchdog must never see the private replay field. Section B7.2 gives the rule and the test.

## B4. Phase 0: preflight and contracts

### B4.1 Environment

1. Write `.build_clock`.
2. Make the repository layout of section B5. Run `git init`. Write `.gitignore`.
3. Install dependencies. Write `requirements.txt` with pinned versions.
4. Load `.env` with `python-dotenv`. Check the two keys (section B2.5).

### B4.2 Data access check

1. Call `huggingface_hub.HfApi().list_repo_files("SALT-NLP/SWE-chat", repo_type="dataset")` with the token. Print the files and their sizes (`get_paths_info`).
2. Call `datasets.get_dataset_config_names("nvidia/Open-SWE-Traces")`. Print the configs and the splits of the newest config. Expected: config `v2.0` with splits that include `claudecode` and `sweagent`. If the names are different, use the newest config that has a Claude Code split and one other harness split. Record this in `BUILD_LOG.md`.
3. Read 20 rows of each source. Save them to `tests/fixtures/` (scrubbed). Subagents use these fixtures before the full data is ready.

### B4.3 Model selection

1. Call `GET https://openrouter.ai/api/v1/models`.
2. A model is **eligible** only if all these are true:
   - `hugging_face_id` is not empty (the weights are open).
   - `supported_parameters` contains `tools`.
   - The ID does not end with `:free`.
3. Select `lab_model`: the first eligible ID from `["qwen/qwen3-coder-next", "qwen/qwen3-coder", "qwen/qwen3-coder-30b-a3b-instruct"]`. If none is eligible, select the eligible Qwen model with "coder" in the ID and the lowest prompt price.
4. Select `helper_model`: the first eligible ID from `["qwen/qwen3-coder-30b-a3b-instruct"]`. Else use `lab_model`.
5. Send one test chat with one tool definition to each model. Check that the response is valid. Save the response as a test fixture.
6. Write both IDs to `config.yaml`.

### B4.4 Contracts (the lead agent writes these before any subagent starts)

1. `dw/common/schema.py`: `Message`, `Session`, `RequestEvent`, `Evidence`, `AccountState`, `DetectorView` (section B6.2, B8.1).
2. `dw/watchdog/detectors/base.py`: the `Detector` protocol.
3. `dw/common/sink.py`: `EventSink` protocol with `HttpSink` (lab to watchdog) and `DirectSink` (offline). `BlocklistSource` protocol with `HttpBlocklist` and `DirectBlocklist`.
4. `docs/API.md`: every HTTP endpoint of all five processes, with request and response JSON examples (section B11.1, B7).
5. `config.yaml` with all keys and default values.
6. `tests/fixtures/`: a small synthetic scenario (about 20 accounts, all six personas) as `RequestEvent` JSON lines. UI and watchdog subagents use it before the real data is ready.

Commit the contracts. Subagents must not change a contract without a note in `BUILD_LOG.md` and an update to every user of that contract.

## B5. Repository layout

```
distill-watch/
  README.md                 # ASD-STE100. Setup, run, demo script, results, limits, attribution.
  BUILD_LOG.md
  config.yaml               # every tunable number
  .env.example
  requirements.txt
  dw/
    common/
      schema.py             # Message, Session, RequestEvent, Evidence, AccountState, DetectorView
      config.py             # load config.yaml once
      llm.py                # one OpenRouter client: retry, concurrency limit, disk cache, cost log
      hashing.py            # canonical serialization and prefix hash chain
      sink.py               # EventSink and BlocklistSource (HTTP and direct)
      term.py               # shared rich terminal status view
    data/
      fetch.py              # download slices of both datasets
      normalize.py          # both sources -> canonical Session; strip scaffold; scrub
      split.py              # group split; manifest with hashes
      synth.py              # LLM jobs: fake tool outputs, stealth paraphrases, phrase banks
    sim/
      personas.py           # persona parameter objects (one class, six configs)
      scheduler.py          # one timing and account simulator for all personas
      scenario.py           # build a scenario: accounts + timed RequestEvents
      sender.py             # send a scenario to the lab over HTTP (gzip, async)
      cli.py                # generator process: --role normal|attack, terminal view, optional --ui
    lab/
      backends.py           # ReplayBackend, LiveBackend (one interface)
      gateway.py            # core handle(); FastAPI routes; block list; inline checks
    watchdog/
      detectors/
        base.py             # Detector interface
        redundancy.py       # D1
        engagement.py       # D2
        coverage.py         # D3
        elicitation.py      # D4
        templates.py        # D5
        fake_env.py         # D6
        volume.py           # D7
      account_model.py      # per-account features -> calibrated score + contributions
      campaigns.py          # account graph + Louvain + campaign score
      policy.py             # score -> allow / review / block
      casefile.py           # LLM case file with evidence citations + validator
      worker.py             # async loop that runs the detectors
      server.py             # watchdog process: /ingest, /blocklist, dashboard API, terminal view
    agent/
      server.py             # agent chat process: agent loop, sandboxed tools, approval queue
      tools.py              # list_dir, read_file, write_file, edit_file, run_shell
    train/
      train_all.py          # trains every learned model; writes models/ and metrics
    eval/
      run_eval.py           # offline evaluation; writes reports/metrics.json and PNGs
      controls.py           # leakage controls (section B10)
  static/
    dashboard.html          # watchdog dashboard
    agent.html              # agent chat UI
    panel.html              # generator control panel (shared by both roles)
    vendor/                 # chart.js, cytoscape.js
  scripts/
    build_data.sh  train.sh  eval.sh  demo.sh  stop.sh  smoke.sh
  tests/
    fixtures/
  docs/
    API.md
    REACT_STAGE.md          # section B16, copied verbatim. Do not build.
```

## B6. Data

### B6.1 Fetch

**SWE-chat** (gated, about 6,000 sessions; tables include `sessions` and `conversations`).

1. Load `sessions`. Keep sessions where `agent` is Claude Code (check the exact string in the data).
2. Load `conversations` for those sessions only. Use projection and filter push-down (duckdb on the parquet, or pyarrow with filters). Do not load all columns.
3. Keep turn types `user_prompt`, `assistant_response`, `tool_use`, `tool_result`. Drop `system_injected`, `assistant_thinking`, `summary`, `system_event`, `file_snapshot`, `progress`, `queue_operation`.
4. Keep `session_id`, `user_id`, `repo_id`, `timestamp`, turn order, role, content, tool name, tool input.

**Open-SWE-Traces** (about 1.14M rows, about 98 GB in total). Do not download all of it.

1. Stream the Claude Code split of the newest config. Read at most `fetch.openswe_rows` rows (default 12,000).
2. Keep `instance_id`, `repo`, `trajectory_id`, `messages`, `resolved`, and from `metadata` the teacher model name and the thinking/reasoning-effort settings.
3. Stream at most `fetch.openswe_xharness_rows` rows (default 800) from one other harness split (prefer `sweagent`). Use these **only** for the cross-harness control (section B10). Do not train on them.
4. Save each source to `data/raw/*.parquet`.

### B6.2 Canonical format

Convert both sources to one format. Every detector reads only this format.

```python
class Message:            # one turn
    role: Literal["user", "assistant", "tool_call", "tool_result"]
    text: str             # user text, assistant prose, or tool output
    tool: str | None      # canonical tool kind: read | write | edit | shell | search | other
    args: dict | None     # canonical args: {"path": ...} or {"command": ...} or {"old":..., "new":...}

class Session:
    session_id: str
    source: Literal["swechat", "openswe", "openswe_xharness"]   # metadata for evaluation only
    group_id: str         # split key: hashed user_id (swechat) or repo (openswe)
    repo_key: str         # hashed repo identity
    task_key: str | None  # instance_id for openswe
    messages: list[Message]
    resolved: int | None
    real_times: list[float] | None   # swechat timestamps; used only to fit timing parameters
```

### B6.3 Strip scaffold (the main trap)

A classifier can learn "this harness means attacker". Prevent this.

1. Drop all system prompts.
2. Drop tool schemas from the data. The persona sets the `tools` field of each request (section B7.1), not the dataset.
3. Remove `<system-reminder>…</system-reminder>` blocks and other harness-injected tags from user and tool text.
4. Remove the harness wrapper text around the task in the first user message of Open-SWE-Traces. Keep the issue text.
5. Normalize paths: `/testbed`, `/workspace/<x>`, the repository root, and temp dirs become `<REPO>`. Home paths become `/home/<user>`.
6. Normalize tool output decoration: remove line-number prefixes (for example `     1→`), ANSI codes, and truncation markers. Then truncate every tool output to `normalize.max_tool_chars` (default 2,000) with **the same** marker for both sources.
7. Truncate every session to the first `normalize.max_steps` assistant steps (default 40).
8. Map tool names to the canonical kinds (`read`, `write`, `edit`, `shell`, `search`, `other`).
9. **Differential n-gram mining.** Find word 3-grams and 5-grams that occur in more than 30% of the sessions of one source and in less than 1% of the other source. Write the top 200 to `reports/scaffold_ngrams.txt`. Read the list. Add every scaffold string (not task content) to the strip list in `normalize.py`. Run again. Do this a maximum of 3 times. Record the final list in `BUILD_LOG.md`.
10. Assistant prose differs by source because different models wrote it (Claude in SWE-chat, Qwen in Open-SWE-Traces). **Detectors must not read assistant prose.** Detectors can read assistant tool calls (kind and args). Section B8.1 enforces this.

### B6.4 Splits

1. Split by `group_id`, not by row: SWE-chat by user, Open-SWE-Traces by repo.
2. Ratios: train 60%, validation 20%, holdout 20%. Fixed seed in `config.yaml`.
3. Write `data/splits/manifest.json` with the group IDs and a SHA-256 of each split file.
4. A test asserts that the splits have no shared groups, no shared repos, and no shared task keys.
5. **The holdout split is for the final evaluation and the demo only.** No model, threshold, or parameter fit may read it. The cross-harness slice is a second holdout.

### B6.5 Fit timing parameters from real data

From SWE-chat train sessions with timestamps, fit and save to `data/timing_fit.json`:
- user think time (gap from an assistant turn to the next user prompt): lognormal parameters.
- tool and model step gaps: lognormal parameters.
- session start hour-of-day distribution.
- sessions per user per active day; days between returns to the same repo.

### B6.6 Synthetic assets (LLM jobs, cached on disk)

Use `dw/common/llm.py` with `helper_model`, at most 16 parallel requests, with a disk cache keyed by the prompt hash. Log tokens and cost to `reports/llm_cost.json`. If the OpenRouter API accepts it, send `"provider": {"data_collection": "deny"}`. If the API rejects it, remove it and log a warning.

1. **Fake tool outputs.** For Open-SWE-Traces trajectories that the fake-env persona uses (train: 300, holdout: 150, from the matching split), select `synth.fake_frac` (default 0.4) of the tool results. Give the LLM only the tool call (command or path) and the two messages before it. Do not give it the real output or any file state. Ask for a plausible raw output. This is what a cheap attacker does who does not run sandboxes. Also keep the real outputs; the realism classifier needs both.
2. **Stealth paraphrases.** For the stealth persona tasks (train: 150, holdout: 150), rewrite the issue text as a short casual request from a developer. Also generate 1 or 2 fake follow-up user turns per session (for example "ok that worked, now also handle the empty case").
3. **Elicitation phrase banks.** Generate two separate banks with different generation prompts and seeds:
   - Bank A (train) and bank B (test). Each has about 200 elicitation instructions across these categories: show full reasoning, explain every step, grade the answer against a rubric, give several alternative solutions, output complete files, maximum detail.
   - Each bank also has about 200 **benign** requests for explanation that real developers write (for example "why does this test fail?", "walk me through this function").
   - Remove items from bank B whose embedding cosine similarity to any item in bank A is more than 0.85.
4. **Attacker templates.** Write 6 task templates with slots (`{repo}`, `{issue}`, `{files}`). Campaigns use these to wrap tasks. Use templates 1 to 4 for train and 5 to 6 for holdout.

## B7. Simulator, lab, agent chat

### B7.1 Personas

Make one `Persona` class with parameters. Make six configurations. All personas use the **same** scheduler (`scheduler.py`). Only the parameters differ.

| Persona | Role | Label | Data | Behavior parameters |
|---|---|---|---|---|
| `solo_dev` | normal | normal | SWE-chat sessions, one account per real user | timing from `timing_fit.json`; tools = standard agent schema; temperature 0 to 0.7; no `user` field |
| `saas_app` | normal | normal (hard negative) | SWE-chat sessions of many users, folded into 3 accounts | very high volume; one fixed custom tool schema; wraps every user prompt in the SaaS product template; sends a stable `user` field per end user |
| `benchmark_runner` | normal | normal (hard negative) | Open-SWE-Traces, 3 accounts, 150 tasks, 1 to 3 attempts | one standard harness; low temperature; no duplicates; account is registered with `declared_use = "benchmark"` and a quota |
| `hydra` | attack | attack | Open-SWE-Traces, 200 accounts, tasks split across accounts | all attempts of one task go to different accounts; best-of-n (the same request sent n = 4 to 8 times at temperature 1.0, only one continues); cron-like synchronized starts with small jitter; elicitation phrases from the bank; max `max_tokens`; maximum reasoning effort; attacker template wraps tasks; tools = standard agent schema |
| `fake_env` | attack | attack | like hydra, 50 accounts | uses the trajectories with LLM-written tool outputs |
| `stealth` | attack | attack | Open-SWE-Traces + SWE-chat sessions mixed in (30%) on the same accounts, 40 accounts | paraphrased tasks; fake follow-ups; human timing sampled near the `timing_fit.json` values; best-of-n of 2 only; no attacker template; fewer elicitation phrases |

Rules:
- Every persona sets the request `tools` field from a small set of schema IDs: `standard_agent` (solo_dev, hydra, fake_env, stealth, and the agent chat), `saas_custom` (saas_app), `bench_harness` (benchmark_runner). Many normal users share `standard_agent`. So a shared schema is weak evidence. This is correct.
- Elicitation phrases go into user turns only. Normal personas also get benign explanation requests from the bank at a rate of 5%.
- For **training** scenarios, sample persona parameters from wide ranges (domain randomization). Use a "light stealth" in training: human timing only, no paraphrase, no mixing. The **full** stealth persona occurs only in the holdout scenario. This tests generalization.
- The seed, the split, and the scale are arguments of `scenario.py`.

### B7.2 Requests

Each assistant step of a session becomes one request:
- `messages` = all canonical messages before that step, as OpenAI chat format.
- Headers: `Authorization: Bearer <account key>`, `X-Sim-Time: <simulation epoch seconds>`.
- Body: `model`, `messages`, `tools`, `temperature`, `n`, `max_tokens`, `reasoning_effort`, optional `user`.
- Private field `x_replay`: the recorded assistant step. The gateway passes it to `ReplayBackend`.

**Rule:** `gateway.handle()` removes `x_replay` before it makes the `RequestEvent` for the watchdog. A test asserts that no `RequestEvent`, no `/ingest` payload, and no stored event contains `x_replay` content.

The simulator clock: the scheduler gives each request a simulation time. The sender sends requests in time order at a speed factor (default 600: one simulated hour takes 6 seconds). Requests without `X-Sim-Time` (the agent chat) get the current simulation time from the lab, so they fit on the same time axis. The watchdog uses this time for all time features. The README must say that a production system uses server time.

`sender.py`: async `httpx`, at most 64 requests in flight, gzip request bodies. It counts sent, served (200), blocked (403), and leaked trajectories (complete trajectories received before the block).

### B7.3 Lab gateway (port 8000)

`gateway.handle(request) -> response` is the core function. The FastAPI route and the offline evaluation both call it. Steps:

1. Find the account from the bearer key. Unknown keys become new accounts (sign-up). `POST /admin/accounts` registers an account with `declared_use` and a quota.
2. If the account or its campaign is on the block list, return HTTP 403 with JSON `{"error": "blocked", "reason": ..., "case_id": ...}`.
3. Inline checks (target p95 < 5 ms): compute the prefix hash chain (section B8.2). Update the duplicate counter for the full-context hash. Record inline flags on the event.
4. Select the backend: `ReplayBackend` if `x_replay` is present, else `LiveBackend` (OpenRouter, `lab_model`, passes `tools` through). Return an OpenAI-format response.
5. Give the `RequestEvent` to the `EventSink`. In live mode this is `HttpSink` (batched `POST /ingest` to the watchdog). In offline mode it is `DirectSink`.

Add a small middleware that decompresses `Content-Encoding: gzip` request bodies.

Terminal view: requests per second, served, blocked, p50/p95 latency of `handle()`, replay versus live count, watchdog connection state.

**Load test:** send 5,000 replay requests at concurrency 64 while the watchdog runs. Report requests per second and p50/p95 latency. Target: 300 requests per second or more, and p95 inline overhead below 5 ms. Write the result to `reports/load.json`.

### B7.4 Traffic generators (ports 8001 and 8002)

`python -m dw.sim.cli --role normal|attack [--persona ...] [--scale small|demo|full] [--speed 600] [--ui]`

- One CLI for both roles. The role selects the allowed personas and the port.
- Without `--ui`: the CLI runs the selected personas and shows a `rich` live view (sent, served, blocked, leaked, per persona). Without `--persona`, it shows a numbered menu in the terminal: for example `1 Solo developers  2 SaaS app  3 Benchmark runner`, or `1 Hydra  2 Fake-env  3 Stealth`. The person types a number to start or stop a persona.
- With `--ui`: the CLI also serves `static/panel.html` on its port. The panel has one button per persona, scale and speed controls, start and stop, and the same counters. Keep the panel simple.
- Generators use the **holdout** scenario only.
- `LAB_URL` defaults to `http://127.0.0.1:8000`.

### B7.5 Agent chat (port 8003)

A real chatbot and coding agent that a person uses in the browser. It is a **customer** of the lab: it calls the lab gateway with its own account key (`acct-human-demo`), so its traffic goes through the watchdog like all other traffic. In the demo, this account must stay "normal".

- **UI** (`static/agent.html`): chat history; a "tools on/off" switch (off = plain chatbot); each tool call and its output in a collapsible block; an approval card for each shell command with **Approve** and **Reject**; a file list of the sandbox; a **New session** button.
- **Agent loop** (`dw/agent/server.py`): send the conversation and the `standard_agent` tool schema to the lab. If the response has tool calls, run them (section below) and send the results back. Stop when the model gives a final answer, or after `agent.max_steps` (default 25) steps per user message. Show "stopped at step limit" if that occurs.
- **Tools** (`dw/agent/tools.py`): `list_dir`, `read_file`, `write_file`, `edit_file`, `run_shell`.
  - All paths resolve inside `agent_workspace/`. Reject any path outside it (resolve symlinks and `..` first).
  - File tools run without approval.
  - `run_shell` waits for **Approve** in the UI. It runs with `cwd=agent_workspace/`, a timeout of 60 s, and an environment that does **not** contain `HF_TOKEN`, `OPENROUTER_API_KEY`, or any other variable from `.env`. Output is truncated to 10,000 characters.
- Non-streaming responses are sufficient.
- Terminal view: one line per request (time, steps, tools used, status).
- Tests use a recorded model response fixture. Only one manual test calls the real model.

## B8. Watchdog (port 8010)

### B8.1 Detector interface

```python
class Detector(Protocol):
    name: str
    def observe(self, ev: RequestEvent, acct: AccountState) -> list[Evidence]: ...   # incremental, cheap
    def features(self, acct: AccountState) -> dict[str, float]: ...                 # account features
```

- Detectors receive a `DetectorView` of the event. The view contains user text, tool calls, tool results, request parameters, the `user` field, the tool schema ID, and the simulation time. **It does not contain assistant prose, `x_replay`, `source`, or any label.** A test asserts this.
- Every `Evidence` has: `id`, `detector`, `account_id`, `request_ids`, `message_index` (if any), `summary` (one sentence with the numbers), `weight`.
- The UI must be able to open the exact request and message for every evidence item.

### B8.2 Prefix hash chain (shared by D1, D2, D7, campaigns)

Serialize each canonical message deterministically. `h_0 = H(tools_schema_id)`. `h_i = H(h_{i-1} || H(message_i))`. The key of a request is `h_n`.

From this one structure, get:
- **Sessions**: a request whose prefix equals the full key of an earlier request from the same account continues that session. The API has no session ID. We rebuild sessions from the hashes.
- **Duplicates**: the same full key sent more than once.
- **Branches**: a prefix with two or more different children.
- **Cross-account task overlap**: the same first-user-message hash on different accounts.

### B8.3 Detectors

Cut order is in section B13. D1, D2, D3, D7, the account model, and campaigns are required.

| ID | Tell | Method | Account features (examples) | Learned? |
|---|---|---|---|---|
| D1 redundancy | Redundant sampling | prefix hash chain | duplicate rate; max duplicates of one state; branch factor; duplicates at temperature ≥ 0.9 | No |
| D2 engagement | Does not use the answer | session statistics from rebuilt sessions | user turns per session; follow-up rate after the final assistant step; correction phrases (embedding similarity to 10 prototype phrases, threshold in config); return rate to the same repo on a different day; fraction of sessions that end at the model stop | No (embeddings only) |
| D3 coverage | Covers instead of focuses | repo and topic distribution per account | distinct repos; normalized repo entropy; sessions per repo; topic entropy over k-means clusters (k = 50, fit on train first-prompts) | No (embeddings) |
| D4 elicitation | Asks for the training signal | TF-IDF (word 1-2 grams + char 3-5 grams) + logistic regression on user text; plus request parameters | mean and max elicitation score; fraction of requests at max `max_tokens`; fraction at max reasoning effort; `n > 1` rate; mean temperature | Yes, small |
| D5 templates | Generated tasks | (a) skeleton lines: mask numbers, paths, identifiers, and quoted strings; count masked lines that occur in ≥ 30% of the account's first prompts. (b) for flagged accounts only, LLM template inversion of 10 prompts into a template with `{slots}` | template mass; distinct skeletons per session | Embeddings + LLM |
| D6 fake_env | Fake environment | (a) shadow file system replay; (b) realism classifier | contradictions per 100 tool results; mean realism score | Partly |
| D7 volume | Industrial scale | counters in simulation time | requests per active hour; concurrent sessions; activity hours per day; `user` field present and number of distinct values | No |

**D6 shadow file system (replay, do not execute).** Walk each rebuilt session in order:
- `write(path, content)` and `edit(path, old, new)` update a dict `path → known content`.
- A later `read(path)` or a shell `cat/head/sed -n` of that path must agree with the known content (compare after whitespace normalization; for an edit, the `new` string must be present).
- `rm path` marks the path deleted. A later successful read of that path is a contradiction.
- A pytest summary must agree with itself: `collected K items` must equal passed + failed + skipped + errors when both lines are present.
- Every contradiction is one evidence item. Example summary: "Step 7 wrote `x = 1` to `<REPO>/foo.py`. Step 9 `cat` returned a file without this line."
- Write unit tests with crafted sessions: one consistent, one with a write-read contradiction, one with a deleted file that comes back, one with a wrong pytest count, and one real Open-SWE-Traces session that must have zero contradictions.

**D6 realism classifier.** Char 3-5 gram TF-IDF + logistic regression. Positives: LLM-written tool outputs (train split). Negatives: real tool outputs from **both** sources (train split). This keeps the source out of the label. Report the false positive rate on real SWE-chat holdout outputs.

Re-execution of commands in a sandbox is **out of scope**. Mention it as future work: the last stage of the funnel, for flagged cases only.

### B8.4 Account model

1. Build training scenarios from the train split. Use 3 seeds with randomized persona parameters. Build one validation scenario from the validation split.
2. Compute all account features with the same worker code that runs live. Use offline mode: call `gateway.handle()` with `DirectSink`, without HTTP.
3. Train logistic regression with standardized features. Report coefficients.
4. For each account, compute the per-feature contribution to the log-odds. The UI shows the top contributions as "why this account".
5. Select thresholds on the **validation** scenario: `block` at the highest recall with a normal-account false positive rate of 1% or less; `review` at a false positive rate of 5% or less.
6. Score only accounts with at least `account.min_requests` requests (default 30). Below this, the state is "insufficient evidence".
7. Also train a **per-request** logistic regression on per-request features (duplicate count so far, temperature, `n`, `max_tokens`, elicitation score, `user` field present). Use it only for the comparison in section B11.3.

### B8.5 Campaigns

Run every 10 seconds of real time, on accounts with a score above `campaign.candidate_threshold` (a funnel: the graph stays small).

Edges between two accounts (sum the weights from `config.yaml`):
- shared first-user-message hashes (the same task on two accounts);
- shared template skeletons (D5);
- synchronized activity: Pearson correlation of per-minute request counts above 0.6;
- shared tool schema, weighted by rarity: `log(N_accounts / N_accounts_with_this_schema)`. The standard schema gets almost zero weight.

Run Louvain. A campaign is a community with 3 or more accounts. Campaign score = mean member score × log(1 + size) plus the mean edge weight. If the campaign score is above the threshold, block **all** members. This catches stealth accounts that are individually below the threshold.

### B8.6 Policy

- `block`: account or campaign above the block threshold, and not a disclosed benchmark account within its quota.
- `review`: above the review threshold, or a disclosed account that exceeds its quota.
- Disclosed benchmark accounts show the flag and the evidence, but the state is "disclosed, allowed within quota".
- The worker writes the block list. The lab reads it from `GET /blocklist`.

### B8.7 Watchdog process

`python -m dw.watchdog.server` runs:
- `POST /ingest` (from the lab) into a bounded `asyncio.Queue`. If the queue is full, write to SQLite and count a drop.
- The worker task: it calls `observe()` on all detectors, updates `AccountState`, and every 2 seconds rescores accounts that changed. It stores a score history per account.
- `GET /blocklist`.
- The dashboard API (section B11.1) and `static/dashboard.html` at `/`.
- Terminal view: ingest rate, queue depth, top 10 accounts by score with state, the last 10 blocks with their main reason, campaigns with size and score.

### B8.8 Case files

`POST /api/campaigns/{id}/casefile` (and the same for one account):
1. Collect the top evidence items, the feature contributions, and the template inversion. Send **only** detector summaries and numbers to the LLM. Do not send raw user text.
2. Ask `helper_model` for a short case file: findings, evidence, confidence, open questions for the investigator, and a recommended action. Every claim must cite evidence IDs in square brackets.
3. **Validator:** reject the case file and retry once if it cites an ID that does not exist or if a paragraph has no citation. If the retry fails, show the evidence list without the LLM text.
4. Cache the case file.

## B9. Learned models

`dw/train/train_all.py` trains everything and writes `models/` and `reports/train_metrics.json`. It must finish in less than 10 CPU minutes.

1. Embeddings for first prompts and correction prototypes. Cache them to `cache/emb/`.
2. D3 topic k-means.
3. D4 elicitation classifier: train on bank A placed into train-split prompts from **both** sources. The label comes from the simulator, not from the source. Test on bank B in holdout prompts. Also build a **keyword baseline**: a regex of the 20 most frequent phrases in bank A. Report both on bank B.
4. D6 realism classifier.
5. Account model and thresholds.
6. Per-request model.

Record wall time and peak memory for each step in `reports/compute.json`.

## B10. Leakage controls (required)

`dw/eval/controls.py` runs these. Write the results to `reports/controls.json` and a table in the README.

1. **Scaffold denylist test.** No canonical record contains any string from the strip list. Also no record contains `<system-reminder>`, `/testbed`, or harness wrapper fragments. This is a pytest test.
2. **Format probe.** A logistic regression on formatting features only (whitespace patterns, line-ending types, Unicode class ratios, markdown markers, JSON markers, path style) of canonical user and tool text predicts the **source**. Report the AUC. Target: below 0.70. If it is higher, go back to section B6.3 step 9. Report the final value honestly.
3. **Assistant-prose probe.** A TF-IDF classifier on assistant prose predicts the source. Expect a high AUC. This shows why detectors do not read assistant prose.
4. **Cross-harness test.** Build a holdout scenario where the hydra persona uses the other-harness slice (normalized) instead of the Claude Code split. The account model must keep its recall. Report recall for both.
5. **Label permutation test.** Retrain the account model with shuffled labels. AUC must be near 0.5.
6. **Feature-family ablation.** Retrain the account model with: (a) only features that come from real data (D1 branches from real attempts, D2, D3, D5, D6), (b) all features including simulated timing and volume (D7). Also a drop-one-detector table. The README must say which signals come from real data and which come from the simulator.

## B11. Dashboard and evaluation

### B11.1 Watchdog dashboard API

The HTML dashboard uses **only** these endpoints. The React stage will use the same endpoints.

| Endpoint | Returns |
|---|---|
| `GET /api/summary` | requests per second, served, blocked, accounts by state, campaigns |
| `GET /api/accounts?state=&sort=score&limit=` | account rows: pseudonym, score, state, requests, top 3 reasons (no persona label) |
| `GET /api/accounts/{id}` | features with population percentiles, contributions, evidence list, score history |
| `GET /api/evidence/{id}` | evidence with the exact request and message (raw canonical text, scrubbed) |
| `GET /api/campaigns`, `GET /api/campaigns/{id}` | members, edges, score, evidence |
| `POST /api/campaigns/{id}/casefile`, `POST /api/accounts/{id}/casefile` | case file |
| `GET /api/graph` | nodes and edges of candidate accounts |
| `GET /api/stream` | Server-Sent Events: one summary tick per second, plus flag, block, and campaign events |
| `GET /api/eval` | `reports/metrics.json` and `reports/controls.json` |
| `POST /admin/reset` | clear live state |

The dashboard must never show the persona label or the source. Investigators do not have labels. The evaluation tab shows labels only in aggregate metrics.

### B11.2 Dashboard page (`static/dashboard.html`)

One page, four tabs. Plain, high-contrast, readable at projector size.

1. **Live.** Request rate (served and blocked) over time. Account table sorted by score with state colors and a small score-history line. A feed of new flags and blocks. The agent-chat account has a small "you" marker.
2. **Account.** Opens from the table. Shows the score, the top feature contributions as a horizontal bar chart, the feature values against population percentiles, and the evidence list. Each evidence item opens the exact message.
3. **Campaigns.** Cytoscape graph of candidate accounts. Node color = state. Edge width = weight. Click a campaign to see members, edge evidence, the template inversion, and the **Generate case file** button.
4. **Evaluation.** The tables and PNGs from `reports/`.

### B11.3 Offline evaluation (`scripts/eval.sh`)

Run the holdout scenario in offline mode through `gateway.handle()` and the worker with `DirectSink`. Write `reports/metrics.json` and PNGs:

- Account-level ROC and PR curves. Recall per attack persona at the selected thresholds. False positives per normal persona (solo, SaaS, benchmark), with counts.
- Campaign effect: stealth recall at account level versus after campaign propagation.
- Time to block: requests and simulated minutes until each attack account is blocked. **Leakage**: the number of complete trajectories each campaign received before the block.
- **Per-request versus per-account** (the key slide): the per-request model false positive rate at its best threshold, multiplied by 10 million requests per day, against the number of falsely blocked normal accounts at account level. Also a plot of the cumulative log-odds of one hydra account and one solo account versus the number of requests.
- Keyword baseline versus the classifier on bank B (D4).
- Fake-env: contradiction rate and realism score for fake-env accounts versus all others.
- Leakage controls (section B10).
- Compute: training time, evaluation time, load-test throughput, LLM cost.

Every number in the README comes from these files. Do not write numbers by hand.

## B12. Build plan, subagents, milestones

The times are elapsed time from `.build_clock`. They are soft (section B2.2).

| Milestone | Target | Who | Done when |
|---|---|---|---|
| M0 Preflight + contracts | 0:30 | lead | Keys work. Both datasets are reachable. Models are selected. Contracts and fixtures are committed (B4.4). |
| M1 Parallel build | 3:30 | 4 subagents in parallel | Each workstream passes its own tests on fixtures. |
| M2 Integration | 4:30 | lead | `scripts/smoke.sh` passes on the real data (see below). |
| M3 Train + evaluate | 5:30 | lead (+1 subagent for controls) | `train.sh` and `eval.sh` write all reports. |
| M4 Independent review + fixes | 6:30 | 3 reviewer subagents, then lead | All review findings are fixed or recorded. Feature freeze. |
| M5 Demo hardening + README | 7:00 | lead | Definition of done (B17). |

### M1 workstreams (run in parallel, each in its own subagent)

- **WS-A Data:** B6 complete (fetch, normalize, scaffold mining, splits, timing fit, synthetic assets).
- **WS-B Lab + generators + agent chat:** B7 complete (personas, scheduler, scenario, sender, CLI with terminal view and `--ui` panel, gateway, backends, sinks, agent chat with sandboxed tools and approval).
- **WS-C Watchdog:** B8 complete on the fixture scenario (detectors, prefix chain, account features, campaigns, policy, case-file validator, watchdog server with terminal view).
- **WS-D Dashboard:** B11.1 and B11.2 against the contract in `docs/API.md`, first with a mock server that serves fixture data, then against the real watchdog.

Each subagent writes unit tests for its own modules and reports what it built, what it cut, and what is open.

### M2 smoke test (`scripts/smoke.sh`)

1. Start all five processes.
2. Run a `small` normal scenario and a `small` hydra scenario.
3. Assert: at least one hydra account is blocked; no solo-dev account is blocked; the lab returns 403 to blocked accounts; `/api/evidence/{id}` opens a message for a block; the agent chat answers one request from a recorded fixture through the lab, and its account is not blocked.
4. Stop all processes. Exit with a non-zero code on any failure.

### M4 independent reviewers (fresh subagents that did not write the code)

1. **ML audit (adversarial).** Look for leakage and shortcut learning: holdout use in any fit, source or label in a `DetectorView`, scaffold strings that remain, simulator parameters that make the task trivial, thresholds fit on holdout. Try to build a trivial classifier that gets a high score from an artifact. Report each finding with a file and line.
2. **Code review.** Check YAGNI, DRY, readability, the safety rules in B2.4 (sandbox path checks, environment without keys, approval for shell), and ASD-STE100 in the README and docstrings.
3. **Demo walkthrough.** Run `scripts/demo.sh`. Follow the demo path in B14 step by step through the HTTP API (and a browser if one is available). Report every step that fails or is confusing.

### Tests

- Unit tests for each module. Required tests named in this specification: split separation, scaffold denylist, `x_replay` exclusion, `DetectorView` exclusion, prefix chain, shadow file system cases, sandbox path escape (`../`, absolute paths, symlinks), shell environment without keys, case-file validator, block list enforcement.
- Integration test: offline small scenario through `handle()` and the worker with `DirectSink`.
- End-to-end: `scripts/smoke.sh`.
- No test calls OpenRouter. Use recorded fixtures.
- Run `pytest -q` at every milestone.

## B13. Cut list (apply in this order)

1. D6 realism classifier (keep the shadow file system).
2. D5 LLM template inversion (keep the skeleton count).
3. `--ui` control panels for the generators (keep the terminal menu).
4. D6 shadow file system and the fake-env persona.
5. Agent chat `run_shell` tool (keep the file tools and the plain chatbot mode).
6. D4 text classifier (keep request-parameter features).
7. Cross-harness control (keep the other controls).

Never cut: holdout separation, the `x_replay` rule, the detector view rule, the scaffold strip and denylist test, the account model, campaigns, evidence links, the per-request versus per-account result, the agent chat (at least plain chat), the dashboard.

## B14. Demo path (write it in the README)

1. Run `scripts/demo.sh`. It opens a `tmux` session with five panes: lab, watchdog, agent chat, normal generator, attack generator.
2. Open the dashboard at `http://127.0.0.1:8010` and the agent chat at `http://127.0.0.1:8003`.
3. In the normal generator pane, start Solo developers, SaaS app, and Benchmark runner. The dashboard stays mostly green. SaaS and Benchmark show some flags but no blocks.
4. In the agent chat, ask the agent to write a small function and its test in the sandbox, then approve the test run. The "you" account stays normal on the dashboard.
5. In the attack generator pane, start **Hydra**. Account scores rise. A campaign forms in the graph. Blocks start. The attack pane shows 403 responses, and the leaked-trajectory count stops.
6. Start **Stealth**. Most accounts stay below the account threshold. The campaign link catches them.
7. Open one evidence item: a duplicate state sent 8 times, or a shadow file-system contradiction.
8. Generate a case file. Show that every claim cites evidence.
9. Evaluation tab: per-request versus per-account, leakage controls, compute cost.

## B15. Documentation rules (ASD-STE100)

Write the README, `BUILD_LOG.md`, `docs/API.md`, docstrings, and UI help text in ASD-STE100 Simplified Technical English:

- Procedural sentences: 20 words maximum. Descriptive sentences: 25 words maximum.
- One instruction per sentence. Use the imperative for instructions ("Run the script.").
- Use the active voice. Use simple verb tenses (present, simple past, simple future).
- Use one word for one meaning. Use the same term for the same thing everywhere (for example, always "account", never "user account" or "client").
- Use articles ("the", "a") where English needs them.
- Put a warning or caution before the step it applies to.
- Paragraphs: 6 sentences maximum.
- Use lists and tables for sequences and comparisons.

The README sections: Purpose. Architecture (one diagram of the five processes). Data and licences (SWE-chat ODC-BY, gated; Open-SWE-Traces CC BY 4.0 with attribution to NVIDIA). Setup. Keys. Run. Demo. Results (from reports). Leakage controls. Which signals are real and which are simulated. Limits. Future work (sandbox re-execution, server time instead of simulation time, React front end, multi-machine deployment).

Limits to state honestly in the README:
- Account structure and timing for attackers are simulated. Normal timing parameters are fitted from real SWE-chat data, and all personas use the same simulator.
- In replay mode, the assistant does not react to fake tool outputs. A real attacker's model would react.
- SWE-chat users are open-source developers who opted in. They do not represent all users.

## B16. React stage (DO NOT BUILD; copy this section to `docs/REACT_STAGE.md`)

> **STOP.** Do not run this stage during the one-shot build. Copy this section verbatim to `docs/REACT_STAGE.md`. Build it only when a person writes "build the React stage".

When a person asks:

1. Make `frontend/` with Vite + React + TypeScript.
2. Dependencies: `@tanstack/react-query` (data), `recharts` (charts), `cytoscape` with `react-cytoscapejs` (graph). No other UI framework.
3. Use **only** the endpoints in `docs/API.md`. Do not change the API. If the React app needs a new field, add it to the API and to the HTML pages at the same time.
4. Make one typed client `src/api.ts` from `docs/API.md`. Make one hook for the SSE stream.
5. Pages: Live, Account, Campaigns, Evaluation (from the watchdog), and Agent chat (from the agent chat process). Same content as the HTML pages.
6. `npm run build` writes to `frontend/dist`. The watchdog serves it at `/app`. Keep the HTML pages at their current paths.
7. Do not remove the HTML pages. The demo must still work if the React build fails.
8. Time box: 90 minutes. If the time box ends, keep the HTML pages for the demo.

## B17. Definition of done

- `scripts/build_data.sh`, `scripts/train.sh`, and `scripts/eval.sh` run from a clean clone with only `.env` added.
- `pytest -q` passes. `scripts/smoke.sh` passes.
- `scripts/demo.sh` starts all five processes on one machine. The demo path in B14 works.
- `reports/` has `metrics.json`, `controls.json`, `compute.json`, `load.json`, `llm_cost.json`, and the PNGs.
- The README and `docs/API.md` are in ASD-STE100. README numbers come from `reports/`.
- `docs/REACT_STAGE.md` exists. The React app is **not** built.
- `BUILD_LOG.md` lists all cuts, decisions, and reviewer findings.

Begin with Phase 0 (section B4) now.
