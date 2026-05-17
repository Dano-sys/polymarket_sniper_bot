#!/bin/sh
set -eu
cd /app

PORT="${PORT:-8080}"
export SNIPER_DATA_DIR="${SNIPER_DATA_DIR:-${DATA_DIR:-/data}}"
export DATA_DIR="${DATA_DIR:-$SNIPER_DATA_DIR}"
mkdir -p "$SNIPER_DATA_DIR"

echo "Starting dashboard on 0.0.0.0:${PORT}" >&2
python3 serve_dashboard.py --host 0.0.0.0 --port "$PORT" &
DASH_PID=$!

echo "Starting sniper (DRY_RUN=${DRY_RUN:-true})" >&2
python3 polymarket_sniper_bot.py &
BOT_PID=$!

trap 'kill "$DASH_PID" "$BOT_PID" 2>/dev/null || true; exit 0' TERM INT
wait "$BOT_PID"
kill "$DASH_PID" 2>/dev/null || true
