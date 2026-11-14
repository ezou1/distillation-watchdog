#!/usr/bin/env bash
# Train every learned model from the train split. Thresholds come from the validation split.
set -euo pipefail
cd "$(dirname "$0")/.."
DW_LLM_OFFLINE=1 .venv/bin/python -m dw.train.train_all
