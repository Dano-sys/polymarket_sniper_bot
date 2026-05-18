#!/usr/bin/env bash
# Run the bot with local code + .env improvements (not .env.sniper-tuned).
set -euo pipefail
cd "$(dirname "$0")"

export DRY_RUN=true
export LOAD_SNIPER_TUNED=0
export DRY_RUN_RESET_ON_START="${DRY_RUN_RESET_ON_START:-false}"
export POSITION_SIZE_USD="${POSITION_SIZE_USD:-100}"
export SNIPER_SLEEP_SECONDS="${SNIPER_SLEEP_SECONDS:-30}"

echo "Local improvements run:"
echo "  code:  polymarket_sniper_bot.py (working tree)"
echo "  config: .env (TRAILING_STOP, PARTIAL_EXIT, etc.)"
echo "  reset paper: DRY_RUN_RESET_ON_START=${DRY_RUN_RESET_ON_START}"
echo ""

exec python3 polymarket_sniper_bot.py
