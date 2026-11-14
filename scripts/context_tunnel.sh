#!/usr/bin/env bash
# Open an SSH tunnel from 127.0.0.1:9100 on this machine to the scoring service on the GPU pod.
# Set DW_POD_HOST and DW_POD_PORT in .env (the "SSH over exposed TCP" values of the pod).
# The key is ~/.ssh/dw_runpod (a separate key for this project). Stop the tunnel with Ctrl-C.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
: "${DW_POD_HOST:?set DW_POD_HOST in .env}" "${DW_POD_PORT:?set DW_POD_PORT in .env}"
exec ssh -N -i ~/.ssh/dw_runpod -o BatchMode=yes -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \
  -L 9100:127.0.0.1:9100 -p "$DW_POD_PORT" "root@$DW_POD_HOST"
