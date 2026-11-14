#!/usr/bin/env bash
# Run the offline evaluation on the holdout scenario, then the leakage controls.
set -euo pipefail
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1  # small per-request numpy calls: threads only spin
cd "$(dirname "$0")/.."
DW_LLM_OFFLINE=1 .venv/bin/python -m dw.eval.run_eval
DW_LLM_OFFLINE=1 .venv/bin/python -m dw.eval.controls
.venv/bin/python -m dw.eval.readme
