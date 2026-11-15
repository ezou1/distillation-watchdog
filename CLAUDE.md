# Distillation Watchdog

Read `docs/TEAM_HANDOFF.md` first. It gives the setup from the repository and the data archive, how to run the demo, how the system works, and the rules for code changes.

Rules to keep in every change:

- **Data stays local.** Never commit `data/`, `cache/`, `models/`, or `.env`. Never send SWE-chat text to OpenRouter or any other external API.
- **No leaks into the evaluation.** Never use the holdout split to fit a model or a threshold. The watchdog never sees `x_replay`, labels, or the data source.
- **Tests pass:** `OMP_NUM_THREADS=1 .venv/bin/pytest -q -p no:cacheprovider` and `scripts/smoke.sh`.
- **Stop processes** with `scripts/stop.sh` or by PID, not with `pkill -f` in a combined command.
