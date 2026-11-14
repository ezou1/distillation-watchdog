#!/usr/bin/env bash
# Stop all five processes (tmux session and background processes).
cd "$(dirname "$0")/.."
tmux kill-session -t dw 2>/dev/null || true
for f in logs/*.pid; do
  [ -f "$f" ] || continue
  kill "$(cat "$f")" 2>/dev/null || true
  rm -f "$f"
done
pkill -f "dw.lab.gateway|dw.watchdog.server|dw.agent.server|dw.sim.cli" 2>/dev/null || true
echo "Stopped."
