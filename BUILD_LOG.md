# Build log

Start time (UTC): 2026-11-14T08:02:05Z (see `.build_clock`).

## Decisions

- D-01. The machine has no `python3-venv` and no sudo access. We installed `uv` in `~/.local/bin` and made `.venv` with `uv`.
- D-02. The repository runs in WSL (Ubuntu 24.04). Commands run through `wsl -d Ubuntu-24.04 -- bash -l`.
- D-03. SWE-chat `sessions.agent` uses the string `Claude Code` (12,417 sessions). One session has `claude-code`. We use only `Claude Code`.
- D-04. Open-SWE-Traces config `v2.0` has the splits `claudecode`, `opencode`, `openhands`, `sweagent`, `minisweagent`. We use `claudecode` for the main data and `sweagent` for the cross-harness control.
- D-05. Model selection: `lab_model` = `qwen/qwen3-coder-next` (HF `Qwen/Qwen3-Coder-Next`). `helper_model` = `qwen/qwen3-coder-30b-a3b-instruct`. Both answered a tool-call test. OpenRouter accepted `provider.data_collection = deny`.
- D-06. SWE-chat sample fixtures are in `tests/fixtures/private/` (in `.gitignore`), because the dataset is gated. Tests that need them skip when the folder is empty.
- D-07. `get_paths_info` on SWE-chat returned HTTP 413 (21,679 files). We used `list_repo_tree` for the file list. We download only `sessions.parquet`, `conversations.parquet`, and `repositories.parquet` (6.4 GB in total).
- D-08. Canonical `search` tool calls become `run_shell` grep commands in the OpenAI format. The three tool schemas have five tools each. The agent chat uses the same five tools.
- D-09. Account IDs are the bearer keys. Keys are random (`acct-<hex>`). They do not contain the persona name. All accounts use the same pseudonym scheme.
- D-10. We added `dw/common/chat.py` (tool schemas and OpenAI conversion) and `dw/common/scrub.py` (one scrub function). Both are shared by several components (DRY).

## Milestones

| Milestone | Plan | Actual | Note |
|---|---|---|---|

## Cuts

## Problems

## Reviewer findings
