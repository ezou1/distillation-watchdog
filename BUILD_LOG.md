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

- D-11. M1 uses six subagents, not four. We split WS-B into B1 (simulator and generators) and B2 (lab and agent chat). We split WS-C into C1 (detectors) and C2 (worker, account model, campaigns, server). This gives more parallel work.
- D-12. The lab clock controls simulation time: `sim_now = epoch_start + (real_now - lab_start) * speed`. Each generator reads the lab time at start and shifts its scenario to that time. Thus two generators share one time axis.

- D-13. Data: SWE-chat has 11,537 canonical sessions. We dropped sessions with no user turn or no tool call. Open-SWE has 12,000 sessions. The cross-harness slice has 800 rows.
- D-14. Split method: one connected group of users and repos holds 83% of SWE-chat. Pure group components gave the validation split no SWE-chat. We assign users to splits greedily. Each repo and task stays in the split that uses it most. We dropped 1,794 SWE-chat sessions that had a conflict. No group, repo, or task is in two splits.
- D-15. Splits (Open-SWE + SWE-chat): train 7,200 + 6,502; val 2,400 + 1,547; holdout 2,400 + 1,694. The cross-harness slice keeps 149 sessions. Most cross-harness repos also occur in train or val, so we removed them.
- D-16. Final scaffold strip list (after 3 rounds of n-gram mining): `OBSERVATION:\n`, `[Request interrupted by user for tool use]`, `[Request interrupted by user]`, ` (file state is current in your context — no need to Read it back)`, `Review the changes and make sure they are as expected. Edit the file again if necessary.`, `Your command ran successfully and did not produce any output.`, `(Bash completed with no output)`. All Open-SWE wrapper fragments are removed. Write and edit success messages become `OK`. `ls -l` owner, group, size, and date columns become placeholders. `scaleswe` becomes `main`. After round 3, the remaining n-grams are task content and model habits.
- D-17. `other` tool calls (TodoWrite, Task, MCP, WebFetch, submit) are dropped with their results. LS and SWE-agent directory views stay as `other {path}`.
- D-18. Known source signal: the tool mix differs by source. Open-SWE has no `search` or `write` calls and one user turn per session. The benchmark persona (normal, Open-SWE data) and the stealth persona (attack, mixed with SWE-chat) are the hard cases that break the link between source and label. The leakage controls measure it.
- D-19. `write_jsonl` drops any session that still contains a forbidden string (`FORBIDDEN` in `dw/data/normalize.py`). Control 1 found 0 dirty records after the final build.
- D-20. We cut the D5 LLM template inversion (cut list item 2). The watchdog cannot know the data source of a prompt, and stealth accounts carry SWE-chat text. SWE-chat text must not go to an external API. The template now comes from the frequent masked skeleton lines, with `{str}`, `{path}`, `{num}`, `{id}`, and `{task}` slots. No LLM call is necessary.
- D-21. We renamed the D6 detector from `d6_fake_env` to `d6_environment`. A detector name must not look like a persona label in the UI.
- D-22. The lab gateway: 704 requests per second, `handle()` p50 0.30 ms and p95 0.75 ms (5,000 replay requests, concurrency 64, without the watchdog). The load-test client uses 4 processes. One process reached only 275 requests per second because the client used 100% CPU.
- D-23. The lab emits an event for every request, also for 403 responses. Inline flags are `dup`, `dup_hot`, and `cross_dup`. `DW_LIVE_FIXTURE` makes the live backend return a recorded response (for the smoke test).
- D-24. The agent shell runs with `PATH`, `HOME=<workspace>`, and `LANG` only. A command can still read files outside the workspace (for example `../.env`). Only the approval click protects against this. This is a stated limit.
- D-25. The worker does not give requests that the lab blocked to the detectors. A missing feature counts as the training mean (zero contribution).
- D-26. The D2 correction threshold is 0.75, not 0.72. With bge-small, corrections score 0.68–0.90 and other follow-ups 0.56–0.73.
- D-27. The sender spreads 64 in-flight requests over 16 small HTTP clients. One large client pool reached only about 180 requests per second.
- D-28. The label-permutation control reports the mean AUC and a permutation p-value. With unequal classes, the mean AUC of a shuffled model can be far from 0.5 on small sets.
- D-29. LLM cost for the data build: about USD 2.36 on the helper model. No SWE-chat text went to OpenRouter.
- D-30. The first full training run took 79 minutes. The limit is 10 CPU minutes. On this CPU, bge-small embeds 3 texts per second at 2,000 characters, and 34 texts per second at 256 characters. A batch size of 256 with 512-token texts used 8 GB of memory. Changes: the embedder reads the first 200 characters (`detectors.embed_max_chars`) with batch size 64. K-means uses a sample of 5,000 train first prompts.
- D-31. A profile of the worker (27,316 requests in 150 s) showed three costs: the D6 shadow file system (`shlex` on the replay of branches), the D6 realism classifier, and the JSON encoding of each message in the prefix chain. Changes: a cache for the `shlex` tokens, the first 1,000 characters of a tool output for the realism classifier (in the pipeline, so training and inference are the same), and a bounded memo for message hashes (the hash values do not change).

## Milestones

| Milestone | Plan | Actual | Note |
|---|---|---|---|
| M0 Preflight + contracts | 0:30 | 0:25 | Keys work. Both datasets are reachable. Contracts committed. 5 contract tests pass. |
| M1 Parallel build | 3:30 | 0:41 | Six subagents. Each workstream passed its own tests on fixtures (commit 68039bf). The data build continued after this commit. |

## Cuts

## Problems

## Reviewer findings
