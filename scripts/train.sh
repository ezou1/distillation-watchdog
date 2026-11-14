#!/usr/bin/env bash
# Train every learned model from the train split. Thresholds come from the validation split.
set -euo pipefail
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1  # small per-request numpy calls: threads only spin
cd "$(dirname "$0")/.."
DW_LLM_OFFLINE=1 .venv/bin/python -m dw.train.train_all
