#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

export DRY_RUN=true
export DRY_RUN_RESET_ON_START=true
export TESTNET_MODE=false
export POSITION_SIZE_USD="${POSITION_SIZE_USD:-100}"
export COMPOUND_POSITIONS="${COMPOUND_POSITIONS:-true}"
export SNIPER_SLEEP_SECONDS="${SNIPER_SLEEP_SECONDS:-30}"

# Optional: .env.sniper-tuned overrides .env when set (loaded in polymarket_sniper_bot.py)
export LOAD_SNIPER_TUNED="${LOAD_SNIPER_TUNED:-0}"

exec python3 polymarket_sniper_bot.py
