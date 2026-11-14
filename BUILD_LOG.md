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
- D-32. Retry rate for each account. After the R1-H1 fix, `d1_dup_rate` still separated the classes alone (single-feature AUC 1.0 on the train and validation tables). The cause: one constant retry rate per persona (normal 0.03 to 0.06, stealth 0.35). Each account now draws its retry rate from a range in `config.yaml`. Normal and stealth ranges overlap. After the retrain, the best single feature has AUC 0.94 (train) and 0.97 (validation). `d1_dup_rate` has AUC 0.94 on validation.
- D-33. A retry copy of one step arrived after the next step of the same session. This is not possible, because the next step needs the response. The copy spread is now at most half of the gap to the next step.
- D-34. The README renderer writes whole numbers without decimals and large values with one decimal.
- D-35. Training speed. A profile of one training scenario showed two large costs: the D6 realism classifier (one scikit-learn call for each tool output) and the lab event (the full message history was converted and hashed again for each request). Changes: D6 scores the queued tool outputs in one batch before the features are read. In the offline replay only, the lab converts and hashes each message object one time (a memo by object identity; the live lab does not use it). `train.processes` is 2: three processes shared cores and used more CPU time. The account features are the same as before (maximum difference 0.0 on scenario train/101). Training: 224.5 s wall time, 411.7 s CPU time. These values use the embedding cache of an earlier run (`cache/emb/emb.npz`), and then the limit of 10 CPU minutes is met. A first run with an empty cache took 681.6 s wall time and 1994.4 s CPU time (the bge-small embeddings: 484 s in D3, most of the rest in the scenario workers). For a first run from a clean clone, the limit is not met. Evaluation: 893.1 requests per second.
- D-36. Campaign links for stealth accounts (examined, not built). We tested a file-footprint link (tool-call path sets of sessions) on a validation scenario with the full stealth persona. In this data, each stealth task has one trajectory, and no other account uses the same task. Thus stealth accounts share no task, prompt, timing, or file set with other attack accounts. The largest path overlaps were with benchmark-runner accounts (normal) in the same repositories. A repository-level link would learn the data source (Open-SWE repositories against SWE-chat repositories). We did not add a link. The validation stealth assets made for this test changed the validation scenario, so we removed them again (the cost stays in `reports/llm_cost.json`).
- D-37. Cross-harness diagnosis (control 4). The recall drop comes almost fully from `d3_repo_entropy` and `d3_topic_entropy`. The SWE-agent slice has 149 sessions for the hydra accounts: median 2 sessions per account, and 48% of the accounts have one session. The main holdout has median 5 sessions and no account with one session. With one session, the entropy features are zero. The requests per session are similar (median 38 and 40). Thus the control measures the size of the slice more than the harness. A fair control gives the same number of sessions per account (fewer hydra accounts in the cross-harness scenario). Not changed.
- D-38. Source protections (format probe, R1-M2). (a) `train.full_stealth`: the train and validation scenarios use the full stealth persona (paraphrased prompts, follow-up turns, SWE-chat sessions). Validation stealth assets: 100 sessions (from the LLM cache, no new cost). (b) Scale `train`: 8 benchmark runners (normal accounts on Open-SWE data) instead of 3. (c) The holdout has 10 benchmark runners instead of 3, so control 7 has Open-SWE normal accounts. (d) Control 7 (source reliance): with the same label, does the account score follow the data source? We chose these changes before we saw holdout results. We did not tune them on the holdout.
- D-39. Cross-harness control with the same sessions per hydra account (diagnosis D-37). Option A: the other harness with 27 hydra accounts (the main holdout has 200). Option B: the main data with the hydra task pool cut to the size of the other-harness slice (82 accounts on each side). Result: A 0.52 (main) against 0.7778 (other harness); B 0.3537 against 0.4878. With the same sessions per account, the recall on the other harness is not lower. Option B has more accounts and is the more reliable comparison.
- D-40. Falsely blocked normal accounts: accepted as a limit. A real deployment uses a human review and an appeal process.
- D-41. Smoke test clock. The lab clock used `sim.speed` (600), but the smoke test sent traffic at speed 3000. Simulation time was compressed five times, so the time features (concurrency, rate) were wrong. The lab now reads `DW_SIM_SPEED`, and `scripts/smoke.sh` sets it to its speed. The smoke test also waits until the watchdog queue is empty before the checks.
- D-42. Robust account model. Live traffic over HTTP arrives slightly out of order, so hydra accounts showed some branches (`d1_branch_factor` about 1.2). In all training scenarios this feature was constant (1.0, std 0.001). The standardized value was about 180, and the score of blocked hydra accounts went to 0. The smoke test released all blocks. Changes: `fit()` leaves out features that are constant in training (`account.min_rel_std`), and standardized values are clipped to +-5 (`account.z_clip`) in training and scoring. The offline evaluation did not show this problem, because the offline replay has no reordering.
- D-43. D5 removed (user decision). In this data, the template-line count measured the prompt length of the source (GitHub issue texts), not templates. The campaign graph also lost its template-line edge and the campaign `template` field. This deviates from B13, which keeps the skeleton count.
- D-44. Rules score (user decision). D1 duplicates are a rules score: the share of requests that repeat an earlier context of the account (`rules.feature`). Block and review thresholds come from the largest value of each validation account (`models/rules.json`). D7 is only an exoneration: many distinct `user` values or a declared use cap the rules level at "review". The account state is the most severe of the rules level, the learned-model level, and the campaign state. Rules-flagged accounts are campaign candidates.
- D-45. Snapshot training. The learned model decided on partial account histories but was trained on full histories, so D3 and D7 worked backwards (attackers blocked after one or two sessions showed one repository). The worker now records feature snapshots at 30, 60, 120, 250, 500, and 1,000 requests and at the end. The model is fitted on all snapshots, weighted so each account counts once. Thresholds use the largest score of each validation account.
- D-46. Context model (user decision; replaces the TF-IDF classifiers of D4 and D6). A fine-tuned Qwen3-0.6B-Base sequence classifier with two tasks (`[prompt]` elicitation, `[tool]` realism), trained on a RunPod A100 (`scripts/context_train.py`). Served by `scripts/context_serve.py` on the pod's localhost, reached through an SSH tunnel (`scripts/context_tunnel.sh`, key `~/.ssh/dw_runpod`). Data fixes before training: realism negatives are real Open-SWE outputs only (the fakes exist only for Open-SWE contexts; the old classifier used both sources and learned the source); the tool call is part of the input; SAAS-wrapped prompts appear in both labels. A first data version cut long prompts to their start, which removed elicitation phrases at the end; the input now keeps the start and the end of long prompts (`head_tail`). Training and scoring use the same text functions (`dw/common/context.py`). Scores are cached on disk by model tag. Offline runs prefetch all texts of a scenario. Without the service, D4 and D6 leave their text features out (training mean).
- D-47. Data rule exception (user decision): SWE-chat text goes to the rented GPU machine for the context model. It never goes to OpenRouter.
- D-48. D6 shadow file system removed. In replay it found no contradictions in fake-environment sessions and most contradictions in normal SaaS sessions.
- D-49. Validation check of the learned-model groups (validation split only; the holdout was not used). With the context model, D2 and D3 added no recall and raised the source AUC of normal accounts (Open-SWE against SWE-chat; 0.5 is ideal): all groups 0.86, without D2 and D3 0.71, D4 only 0.41. D6 alone had source AUC 0.99 and a negative coefficient: the context model gives real SWE-chat outputs almost the realism score of the fakes (median account score: solo developer 0.24, SaaS 0.25, fake environment 0.36, hydra and benchmark 0.06), because its training data has fakes only for Open-SWE contexts. The account model used this as "low realism score = Open-SWE = attack". Decision: the learned model uses D4 only. D6 runs and gives evidence, but does not score. A fix needs fake outputs for SWE-chat contexts (generated on the GPU machine).
- D-50. Fallback without the context model. With the service down, the D4 text features were missing, the model used the training mean, and the request settings alone blocked two solo developers in the smoke test. Now a missing model feature caps the model level at "review" (`policy.decide(model_complete=False)`). The rules score and the campaigns still block. The smoke test passes with and without the service.

## Milestones

| Milestone | Plan | Actual | Note |
|---|---|---|---|
| M0 Preflight + contracts | 0:30 | 0:25 | Keys work. Both datasets are reachable. Contracts committed. 5 contract tests pass. |
| M1 Parallel build | 3:30 | 0:41 | Six subagents. Each workstream passed its own tests on fixtures (commit 68039bf). The data build continued after this commit. |
| M2 Integration | 4:30 | 8:43 | `scripts/smoke.sh` passed on the real models (6 of 6 checks). The first session stopped because of WSL memory and idle-stop problems (P-02). The build continued in a new session inside WSL. |
| M3 Train + evaluate | 5:30 | 8:41 | Retrain after D-32. `train.sh` and `eval.sh` wrote all reports. The README renderer wrote the Results and Leakage controls sections. |
| M4 Independent review + fixes | 6:30 | 8:57 | Reviewers 1, 2, and 3 are complete. All findings are fixed or recorded. |
| M5 Demo hardening + README | 7:00 | 8:58 | README Limits has the training time, the format probe, the cross-harness result, and the stealth campaign result. 171 tests pass. |

## Cuts

- Cut list item 2: D5 LLM template inversion (D-20). The skeleton count stays. The campaign field `template` is `null`.

## Retrain result (after D-32)

- Training: 432 s wall time, 1686 s CPU time. The limit of 10 CPU minutes is not met (P-01). After D-35: 224.5 s wall time, 411.7 s CPU time, with the same models (embedding cache present). With an empty cache: 681.6 s wall time, 1994.4 s CPU time.
- Validation account AUC 0.9997. Block threshold 0.555, review threshold 0.089 (from validation).
- Holdout: account AUC 0.957. Per-request AUC 0.909. Falsely blocked normal accounts: 2 of 57. Attack accounts blocked: 0.892.
- Stealth recall: 0.205 at account level, 0.205 after campaigns. The campaigns do not catch more stealth accounts.
- Controls: format probe AUC 0.999 (fail), cross-harness hydra recall 0.690 against 0.561 (fail), label permutation mean AUC 0.462.

## Result after phase 1 and the context model (D-43 to D-50)

Holdout, one run after all choices were made on validation.

- Learned model (D4 only): validation AUC 0.986, holdout AUC 0.934. Rules statistic (D1) holdout AUC 0.963. Rules thresholds: block 0.233, review 0.216.
- Blocked per persona (rules / model / account level / final): hydra 200/113/200/200 of 200; fake environment 47/22/47/47 of 47; stealth 7/16/23/23 of 39.
- Falsely blocked normal accounts: 1 of 64 (was 5 of 64). Attack accounts blocked: 0.944 (was 0.965). Stealth recall 0.590 (was 0.769).
- D4 context model on bank B (phrases not seen in training): precision 0.9897, recall 0.8993, false positive rate 0.0093 (the TF-IDF classifier had 0.851, 0.696, 0.122). Keyword baseline: 0.5486, 0.2707, 0.2227.
- D6 context model on the holdout realism set: AUC 0.9426, recall 0.7771, false positive rate 0.049 (Open-SWE). It flags 0.1983 of real SWE-chat outputs (the TF-IDF classifier flagged 0.046). D6 does not score (D-49).
- Control 4: 1.0 against 1.0; options A and B also equal. Control 7: normal accounts AUC 0.5315, stealth Spearman 0.0756 (p = 0.6474). Label permutation mean AUC 0.5692.
- Ablation (holdout AUC): rules statistic only 0.9633, current learned groups 0.9276, strict real data 0.4885, simulator only 0.9727.
- Smoke test: 6 of 6 with the context model, and 6 of 6 without it.
- Training: 123.7 s wall time, 225.4 s CPU time on this machine (the context-model scores come from the GPU machine and the disk cache).

## Problems

- P-00. Claude Code ran from Windows with the working directory `\\wsl.localhost\...`. When the Ubuntu instance was stopped, Windows could not reach this path, and tool calls failed. Run Claude Code inside WSL.

- P-01. Training time. The limit is 10 CPU minutes. A clean training run (empty embedding cache) took 922 s of wall time and about 68 CPU minutes on this machine (12 cores, WSL). The scenario replays through the full worker and the bge-small embeddings use most of the time. A second run with the embedding cache takes less time. `reports/compute.json` has the values of the last run. We did not meet this limit. D-35 meets the limit only when the embedding cache exists.
- P-02. Two training runs did not finish. Run A (4 parallel scenario processes and a test run): the Linux out-of-memory killer stopped one worker (4.0 GB). The WSL machine had 15.8 GB. Run B (2 processes, started with `setsid`): WSL stopped the idle Ubuntu instance when no `wsl.exe` session was attached, and this stopped the job. The kernel log shows no memory problem for run B. Changes: WSL now has 24 GB of memory and 8 GB of swap (`.wslconfig`). `train.processes` is 3. Run long jobs in an attached session.
- P-03. Format probe (control 2): AUC 0.999 on the holdout. The target is below 0.70. The largest features are markdown headings and inline code in user text, the number of user turns, and the number of tool results. Open-SWE prompts are GitHub issue texts with markdown. SWE-chat prompts are chat messages. This is a difference in task type and session shape, not a harness string. More scaffold stripping cannot remove it. The README reports the value.

## Reviewer findings

Reviewer 1: ML audit (adversarial). Reviewer 2: code review. Reviewer 3: demo walkthrough.

| ID | Reviewer | Severity | Finding | Status |
|---|---|---|---|---|
| R1-H1 | ML | High | `d1_dup_rate` alone separated the classes (AUC 1.0). No normal persona sent a duplicate. Stealth always sent 2 copies. | Fixed. All personas have a retry rate. Stealth sends a second copy for 35% of requests only. |
| R1-H2 | ML | High | `max_tokens`, `reasoning_effort`, and temperature were constants for hydra and fake-env. | Fixed. Each account draws these values. The distributions overlap between normal and attack personas. |
| R1-M1 | ML | Medium | The per-request threshold was selected on the holdout. | Fixed. `train_all.py` selects it on the validation request rows. The holdout only measures it. |
| R1-M2 | ML | Medium | Attack accounts have exactly 1 user turn per session (Open-SWE data has one prompt). | Fixed by D-38. The train and validation scenarios use the full stealth persona with follow-up turns. |
| R1-M3 | ML | Medium | Path strings (`/home/`, `/workspace`) and `Claude Code` still show the source. | Recorded. A data rebuild was not possible in the time box. The format probe measures this (control 2 fails). |
| R1-M4 | ML | Medium | Few normal accounts in validation (39) and holdout (57). The false positive rate has steps of about 2%. | Recorded as a limit. |
| R1-L1 | ML | Low | Validation uses the train templates. | Recorded. Validation thresholds are optimistic. |
| R1-L2 | ML | Low | D7 timing features are fully simulated. | Recorded. The README table shows the origin of each signal. |
| R2-1 | Code | High | `.gitignore` pattern `data/` also ignored `dw/data/`. The data pipeline was not committed. | Fixed. The patterns are anchored (`/data/`). `dw/data/` is committed. |
| R2-2 | Code | High | Cross-site request forgery on the agent chat: a foreign page could approve a shell command. Approval IDs were sequential. | Fixed. A POST with a foreign `Origin` gets 403. A POST that is not JSON gets 415. Approval IDs are random (`secrets.token_urlsafe`). |
| R2-3 | Code | Medium | A path with a NUL byte broke the agent session. | Fixed. The sandbox refuses the path. A tool error always adds a tool result to the history. |
| R2-4 | Code | Medium | Case files sent evidence summaries (they can contain SWE-chat text) to the helper model. | Fixed. The LLM prompt has only evidence IDs, detector names, weights, counts, and feature values. The full summaries are added locally. |
| R2-5 | Code | Medium | The lab sent any request without `x_replay` to the live backend, for any account. | Fixed. Only the agent-chat account can use the live backend. Other accounts get 400. |
| R2-6 | Code | Medium | The agent shell can read `../.env`. | Recorded as a limit (D-24, README Limits). |
| R2-7 | Code | Low | `bool("false")` is true in the approval handler. | Fixed. Only the JSON value `true` approves a command. |
| R2-8..13 | Code | Low | DRY (two `ChatClient` protocols), the dashboard mock server, an unused config key, a long `create_app`, gzip size limit, ASD-STE100 passive voice. | Recorded. Not fixed in the time box, except the documentation sentences. |
| R3-1 | Demo | High | Step 4: the agent shell had no `python` with pytest. The approved test run failed. | Fixed. `scripts/demo.sh` makes a separate venv `cache/agent_venv` with pytest. The agent shell PATH has it first. The project venv stays outside the agent PATH. |
| R3-2 | Demo | Medium | A case file fell back with no reason in the response or the log. | Fixed. The watchdog logs the validator reason. The response has the field `reason`. |
| R3-3 | Demo | Medium | The campaign case file did not cite the campaign evidence. The model read negative log-odds as attack signs. | Fixed. Campaign items come first in the evidence list. The prompt explains the sign of `log_odds` and the campaign items. |
| R3-4 | Demo | Medium | About 14% of lab events did not arrive at the watchdog during the Hydra burst. The drop count was not visible. | Partly fixed. The sink timeout is 30 s (was 5 s). `GET /stats` and the lab pane show `events_dropped`. The cause is not confirmed. |
| R3-5 | Demo | Medium | Step 6: campaigns did not catch stealth accounts. | Recorded. README Demo step 6 and Limits state the result. The holdout shows the same result. |
| R3-6 | Demo | Low | Step 5: Hydra ends in about 90 s at the demo speed. The leaked count stops because the scenario ends. | Recorded. README Demo step 5 says this. |
| R3-7 | Demo | Low | The demo changed `reports/llm_cost.json` (agent and case-file calls). | Recorded. We restored the build value before the commit. |
| R3-8 | Demo | Low | `POST /api/session/new` needs `Content-Type: application/json`. `docs/API.md` did not say this. | Fixed in `docs/API.md`. |
| R3-9 | Demo | Low | The account reasons show only positive terms, also for low-score accounts. | Recorded. |
| R3-10 | Demo | Low | Evidence text contains harness strings from the data (for example `CLAUDE_CODE_EXECPATH`). | Recorded. Same as R1-M3. |
| R3-11 | Demo | Low | The generator counter `leaked` also counts normal traffic. | Recorded. The counter counts complete trajectories for all personas. |
| R3-12 | Demo | Low | Campaign `template` is `null`. | Recorded. Cut list item 2 (D-20). |
