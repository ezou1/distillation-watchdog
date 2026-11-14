#!/usr/bin/env bash
# Build all data files (docs/CONTRACTS.md section 1). Steps with existing outputs are skipped.
# Needs .env with HF_TOKEN and OPENROUTER_API_KEY, and the venv in .venv.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python

missing() { [ ! -s "$1" ]; }

$PY -c "from dw.common.config import require_keys; require_keys()"

if missing data/raw/swechat/conversations.parquet; then
  echo "download SWE-chat (about 6.4 GB)"
  $PY - <<'PY'
import os
from huggingface_hub import hf_hub_download
for name in ("sessions.parquet", "conversations.parquet", "repositories.parquet"):
    hf_hub_download("SALT-NLP/SWE-chat", name, repo_type="dataset", local_dir="data/raw/swechat",
                    token=os.environ.get("HF_TOKEN"))
PY
fi
if missing data/raw/openswe_xharness.parquet; then $PY -m dw.data.fetch xharness; fi
if missing data/raw/openswe.parquet; then $PY -m dw.data.fetch openswe; fi

for src in openswe openswe_xharness swechat; do
  if missing "data/canonical/$src.jsonl"; then $PY -m dw.data.normalize build --only "$src"; fi
done
if missing reports/scaffold_ngrams.txt; then $PY -m dw.data.normalize mine --round 1; fi
if missing data/splits/manifest.json; then $PY -m dw.data.split; fi
if missing data/timing_fit.json; then $PY -m dw.data.timing; fi
$PY -m dw.data.synth
echo "data build done"
