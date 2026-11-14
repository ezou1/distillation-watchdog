#!/usr/bin/env bash
# Start all five processes. Use tmux if it is installed. Else start them in the background with logs in logs/.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
SCALE="${SCALE:-demo}"
mkdir -p logs agent_workspace

CMDS=(
  "lab|$PY -m dw.lab.gateway"
  "watchdog|$PY -m dw.watchdog.server"
  "agent|$PY -m dw.agent.server"
  "normal|$PY -m dw.sim.cli --role normal --scale $SCALE --ui"
  "attack|$PY -m dw.sim.cli --role attack --scale $SCALE --ui"
)

if command -v tmux >/dev/null 2>&1 && [ -z "${NO_TMUX:-}" ]; then
  tmux kill-session -t dw 2>/dev/null || true
  first=1
  for entry in "${CMDS[@]}"; do
    name="${entry%%|*}"; cmd="${entry#*|}"
    if [ $first = 1 ]; then
      tmux new-session -d -s dw -n main -x 240 -y 60 "bash -c '$cmd; exec bash'"
      first=0
    else
      tmux split-window -t dw:main "bash -c '$cmd; exec bash'"
      tmux select-layout -t dw:main tiled >/dev/null
    fi
    tmux select-pane -t dw:main -T "$name"
    sleep 1
  done
  tmux set -t dw pane-border-status top >/dev/null
  echo "Started tmux session 'dw'. Attach with: tmux attach -t dw"
else
  for entry in "${CMDS[@]}"; do
    name="${entry%%|*}"; cmd="${entry#*|}"
    nohup $cmd > "logs/$name.log" 2>&1 &
    echo $! > "logs/$name.pid"
    sleep 1
  done
  echo "Started in the background. Logs are in logs/."
fi

echo "Dashboard:         http://127.0.0.1:8010"
echo "Agent chat:        http://127.0.0.1:8003"
echo "Normal generator:  http://127.0.0.1:8001"
echo "Attack generator:  http://127.0.0.1:8002"
echo "Lab API:           http://127.0.0.1:8000"
