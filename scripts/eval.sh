#!/usr/bin/env bash
# Run the offline evaluation on the holdout scenario, then the leakage controls.
set -euo pipefail
cd "$(dirname "$0")/.."
DW_LLM_OFFLINE=1 .venv/bin/python -m dw.eval.run_eval
DW_LLM_OFFLINE=1 .venv/bin/python -m dw.eval.controls
