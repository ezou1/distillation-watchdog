#!/usr/bin/env bash
# End-to-end smoke test: start all five processes, send small normal and hydra traffic, check the results.
# The lab uses a recorded model response, so this test does not call OpenRouter.
set -uo pipefail
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1  # small per-request numpy calls: threads only spin
cd "$(dirname "$0")/.."
PY=.venv/bin/python
SPEED="${SPEED:-3000}"
mkdir -p logs
TMP=$(mktemp -d)
scripts/stop.sh >/dev/null 2>&1
export DW_LIVE_FIXTURE="$PWD/tests/fixtures/openrouter_lab_final.json"
export DW_LLM_OFFLINE=1

$PY -m dw.watchdog.server > logs/smoke_watchdog.log 2>&1 & WD=$!
$PY -m dw.lab.gateway > logs/smoke_lab.log 2>&1 & LAB=$!
$PY -m dw.agent.server > logs/smoke_agent.log 2>&1 & AG=$!
cleanup() { kill $WD $LAB $AG 2>/dev/null; wait 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT

for port in 8010 8000 8003; do
  for _ in $(seq 60); do curl -sf "http://127.0.0.1:$port/" >/dev/null 2>&1 || curl -sf "http://127.0.0.1:$port/health" >/dev/null 2>&1 && break; sleep 1; done
done
curl -s -X POST http://127.0.0.1:8010/admin/reset >/dev/null

$PY -m dw.sim.cli --role normal --scale small --speed "$SPEED" --once --labels-out "$TMP/normal.json" & N=$!
$PY -m dw.sim.cli --role attack --persona hydra --scale small --speed "$SPEED" --once --labels-out "$TMP/attack.json" || exit 1
wait $N || exit 1
sleep 12   # one rescore and one campaign pass in the watchdog
$PY -m dw.eval.smoke_check "$TMP/normal.json" "$TMP/attack.json"
