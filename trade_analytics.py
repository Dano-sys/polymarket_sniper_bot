#!/usr/bin/env python3
"""Summarize sniper paper/live session from trade_log.json."""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from sniper_env import load_sniper_env

load_sniper_env()

LOG = Path(os.getenv("SNIPER_TRADE_LOG_FILE", "trade_log.json"))


def _load_log() -> list:
    if not LOG.is_file():
        print(f"No trade log at {LOG}")
        return []
    with open(LOG) as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def main() -> int:
    rows = _load_log()
    if not rows:
        return 1

    buys = [r for r in rows if r.get("event") == "buy"]
    sells = [r for r in rows if r.get("event") == "sell"]

    realized = sum(float(s.get("pnl_usd") or 0) for s in sells)
    wins = [s for s in sells if float(s.get("pnl_usd") or 0) > 0]
    losses = [s for s in sells if float(s.get("pnl_usd") or 0) < 0]

    false_profits = [
        s
        for s in sells
        if s.get("reason") in ("profit_target", "partial_profit")
        and float(s.get("pnl_usd") or 0) < 0
    ]

    by_reason: dict[str, list] = defaultdict(list)
    for s in sells:
        by_reason[str(s.get("reason") or "?")].append(float(s.get("pnl_usd") or 0))

    print(f"Trade log: {LOG}")
    print(f"  Buys:  {len(buys)}")
    print(f"  Sells: {len(sells)}")
    print(f"  Realized P&L: ${realized:+.2f}")
    if sells:
        print(f"  Win rate: {len(wins)}/{len(sells)} ({100 * len(wins) / len(sells):.0f}%)")
    print(f"  False profit exits (profit_target/partial with loss): {len(false_profits)}")
    print()
    print("P&L by exit reason:")
    for reason, pnls in sorted(by_reason.items()):
        total = sum(pnls)
        print(f"  {reason:16}  n={len(pnls):2}  total=${total:+.2f}")

    if buys:
        entries = [float(b.get("price") or 0) for b in buys]
        print()
        print(f"Avg entry fill: {sum(entries) / len(entries):.4f}  (min {min(entries):.4f}, max {max(entries):.4f})")

    if false_profits:
        print()
        print("False profit exits:")
        for s in false_profits:
            print(
                f"  {s.get('outcome', '?')[:40]:40}  "
                f"bid {s.get('reference_bid')}  fill {s.get('fill_price')}  "
                f"entry {s.get('entry_price')}  pnl ${float(s.get('pnl_usd') or 0):+.2f}"
            )

    paper = Path(os.getenv("SNIPER_PAPER_ACCOUNT_FILE", "sniper_paper_account.json"))
    if paper.is_file():
        with open(paper) as f:
            acct = json.load(f)
        cash = float(acct.get("cash_usd") or 0)
        start = float(acct.get("starting_balance_usd") or 0)
        print()
        print(f"Paper cash: ${cash:,.2f}  (started ${start:,.2f})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
