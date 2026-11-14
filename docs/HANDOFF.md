# Handoff: state of the build on 2026-11-14

This file is for the next Claude Code session. Read it, then `BUILD.md` (the specification) and `BUILD_LOG.md` (decisions, problems, reviewer findings).

## Where the work is

- Build start: 2026-11-14T08:02:05Z (`.build_clock`). At handoff (16:15Z) the elapsed time is past the 6:30 feature freeze. Do not add features. Finish the definition of done (BUILD.md B17).
- Last commit before this file: `43a09fa`. All code is committed. `data/`, `cache/`, `models/`, `.env` are on this machine only (gitignored). Do not commit them.
- Milestones: M0 and M1 are done. M2 (smoke test), M3 (train and evaluate), M4 (reviews and fixes), and M5 (README) are partly done. See "Remaining steps".

## Environment

- Run Claude Code inside WSL: open the Ubuntu terminal, `cd ~/distillation-watchdog`, `claude`. Do not run it from Windows with a `\\wsl.localhost` working directory. That path fails when WSL stops an idle Ubuntu instance, and all tool calls fail (BUILD_LOG P-00).
- WSL has 24 GB of memory, 8 GB of swap, and 12 cores (`C:\Users\eric\.wslconfig`).
- Python: `.venv/bin/python` (made with `uv`). Tests: `OMP_NUM_THREADS=1 .venv/bin/pytest -q -p no:cacheprovider`.
- Run long jobs in an attached session (a foreground command or a background shell task). A job started with `setsid`/`nohup` died when WSL stopped the idle instance (BUILD_LOG P-02).
- Do not run training and the full test suite at the same time. Four scenario workers and a test run caused an out-of-memory kill at 15.8 GB.

## Memory and time of training

- `train.processes` in `config.yaml` is 3. Each scenario worker needs up to 4.5 GB. The parent process needs about 3 GB.
- Expected time with a warm embedding cache (`cache/emb/emb.npz`): about 10 to 12 minutes for `scripts/train.sh`, about 6 minutes for `scripts/eval.sh`.
- The limit "10 CPU minutes" (BUILD.md B9) is not met. A clean run took 922 s wall time and about 68 CPU minutes (BUILD_LOG P-01). Report it. Do not hide it.

## What changed after the last full evaluation

The files in `reports/` come from the run before these changes. They are out of date.

- The simulator request parameters now overlap between personas: retry rate for all personas, stealth sends a second copy for 35% of requests, `reasoning_effort` and the `max_tokens` cap are per-account probabilities, temperature ranges overlap (reviewer findings R1-H1, R1-H2).
- The per-request threshold comes from the validation request rows (`train_all.request_threshold`). `run_eval.py` only measures it on the holdout (R1-M1).
- Security fixes R2-2 to R2-5 and R2-7 (agent chat origin and JSON checks, random approval IDs, NUL-byte paths, live backend only for the agent-chat account, case files send no free text to the LLM). 171 tests passed after these fixes.
- `scripts/eval.sh` now ends with `python -m dw.eval.readme`. This writes the README sections between `<!-- RESULTS:START -->`/`END` and `<!-- CONTROLS:START -->`/`END` from `reports/`. The renderer is new and not yet run on real reports. Fix it if a key is missing.

## Remaining steps (in this order)

1. `scripts/train.sh`. Check `reports/train_metrics.json`: val AUC, thresholds, and the coefficients. With the new simulator, `d1_dup_rate` must no longer separate the classes alone.
2. `scripts/eval.sh`. Check `reports/metrics.json`, `reports/controls.json`, the PNGs, and the README sections.
3. `OMP_NUM_THREADS=1 .venv/bin/pytest -q -p no:cacheprovider`. Fix failures.
4. `scripts/smoke.sh` (M2). It has not passed on the real models yet. It uses `DW_LIVE_FIXTURE` and `DW_LLM_OFFLINE=1`, so it does not call OpenRouter.
5. Optional: `.venv/bin/python -m dw.lab.loadtest` with the watchdog running, to update `reports/load.json`.
6. M4 reviewer 3 (demo walkthrough): a fresh subagent runs `scripts/demo.sh` and follows the demo path in BUILD.md B14 through the HTTP API. Fix or record each finding in BUILD_LOG "Reviewer findings".
7. README: check that every number comes from `reports/` (no typed numbers). Check the ASD-STE100 rules (BUILD.md B15). Add the training-time and format-probe results to "Limits".
8. BUILD_LOG: add the milestone rows M2 to M5 with elapsed times. Add the result of the retrain.
9. Commit. End the message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Known open items (recorded, not fixed)

- Format probe AUC 0.999 (target below 0.70). The cause is task type and session shape (GitHub issue prompts with markdown against chat messages), not harness strings (BUILD_LOG P-03).
- Path strings (`/home/`, `/workspace`) and `Claude Code` in text show the source (R1-M3). A fix needs a data rebuild.
- Few normal accounts in validation and holdout (R1-M4). Validation uses the train templates (R1-L1).
- The agent shell can read `../.env`. Only the approval click prevents this (D-24, R2-6).
- Low-priority code review items R2-8 to R2-13.

## Safety rules that still apply

- No SWE-chat text to any external API. No `:free` OpenRouter routes. No test calls OpenRouter.
- The watchdog never sees `x_replay`. Detectors do not read assistant prose, source, or labels. The holdout is never used for a fit or a threshold.
- The dashboard never shows persona labels or the source.
