#!/usr/bin/env python3
"""
One-shot live round-trip: pick a merged scan-pool leg, buy at minimum notional, sell back.

Uses the same pool filters as polymarket_sniper_bot.scan_and_trade (timing, liquidity, price).
Secrets load via sniper_env (copy_bot .env, then local .env).

Examples:
  python3 live_roundtrip_test.py --list
  python3 live_roundtrip_test.py --live --yes --pick 0
  python3 live_roundtrip_test.py --live --yes --notional 1.0 --max-notional 15
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from sniper_env import load_sniper_env

load_sniper_env()

import polymarket_sniper_bot as bot
from live_clob import CLOB_MIN_ORDER_USDC, ClobBuySkipChainPrice, live_trader_singleton, safe_json


def _live_keys_ok() -> bool:
    return bot._live_keys_ok()


def _buy_limit_price(ask: float) -> float:
    return min(ask + bot.SLIPPAGE_TOLERANCE, 0.99)


def resolve_buy_qty(
    trader: Any,
    token_id: str,
    condition_id: Optional[str],
    ask: float,
    target_notional: float,
    *,
    max_notional: float = 15.0,
) -> tuple[float, float, float]:
    """Return (share_qty, limit_price, used_notional) with CLOB cent alignment."""
    opts = trader.fetch_order_options(token_id, (condition_id or "").strip() or None)
    tick_size = getattr(opts, "tick_size", "0.01") or "0.01"
    limit_price = _buy_limit_price(ask)
    start = max(target_notional, CLOB_MIN_ORDER_USDC)
    cap = max(start, max_notional)
    cents = int(round(start * 100))
    cap_cents = int(round(cap * 100))
    for usd_cents in range(cents, cap_cents + 1):
        notional = usd_cents / 100.0
        qty = notional / ask if ask > 0 else 0.0
        try:
            place_size, order_price = trader._quantize_clob_buy_order(qty, limit_price, tick_size)
        except ClobBuySkipChainPrice:
            continue
        if place_size <= 0:
            continue
        used = place_size * order_price
        if used + 1e-9 < CLOB_MIN_ORDER_USDC:
            continue
        return place_size, order_price, used
    raise ValueError(
        f"No cent-aligned BUY size between ${start:.2f} and ${cap:.2f} at limit {limit_price:.4f} "
        f"(tick={tick_size}). Try another leg or raise --max-notional."
    )


def collect_candidates() -> List[dict]:
    """Same funnel as scan_and_trade, without position limits or DRY_RUN."""
    markets = bot.fetch_scan_markets()
    opportunities: List[dict] = []

    for market in markets:
        if not market.get("accepting_orders", market.get("acceptingOrders", True)):
            continue
        if not market.get("enable_order_book", market.get("enableOrderBook", True)):
            continue
        if not bot.is_close_to_resolution(market):
            continue

        condition_id = market.get("condition_id") or market.get("conditionId") or ""
        vol_hint = market.get("gamma_volume_24h")
        if vol_hint is None and condition_id:
            vol_hint = bot.gamma_volume_24h(condition_id)
        question = str(market.get("question") or market.get("title") or "N/A")
        neg_flag = market.get("neg_risk", market.get("negRisk"))

        for tok in market.get("tokens") or []:
            if not isinstance(tok, dict):
                continue
            token_id = str(tok.get("token_id") or tok.get("tokenId") or "")
            if not token_id:
                continue
            outcome = str(tok.get("outcome", "Unknown"))
            price_hint = tok.get("price")
            if price_hint is not None:
                try:
                    if float(price_hint) < bot.PRICE_THRESHOLD:
                        continue
                except (TypeError, ValueError):
                    pass

            book_data = bot._fetch_order_book_payload(token_id)
            if not book_data:
                continue
            is_liquid, spread_pct, depth, volume, bid, ask = bot.check_liquidity(
                token_id, condition_id or None, vol_hint, book_data
            )
            if ask is None or not is_liquid:
                continue
            if not bot.should_buy(ask):
                continue
            if not bot.has_profit_room(ask):
                continue
            if spread_pct is None or volume is None:
                continue
            market_score = bot.score_market_for_upside(market, ask, spread_pct, float(volume))
            if market_score < bot.MIN_MARKET_SCORE:
                continue

            opportunities.append(
                {
                    "market_id": condition_id or market.get("id", ""),
                    "condition_id": condition_id,
                    "market_question": question,
                    "token_id": token_id,
                    "outcome": outcome,
                    "ask_price": ask,
                    "bid": bid,
                    "spread_pct": spread_pct,
                    "depth": depth,
                    "volume_24h": volume,
                    "hours_to_resolution": bot.hours_until_resolution(market),
                    "neg_risk_hint": bool(neg_flag) if neg_flag is not None else None,
                    "market_score": market_score,
                }
            )

    seen: set[str] = set()
    deduped: List[dict] = []
    for row in sorted(opportunities, key=lambda x: (-x["market_score"], x["ask_price"])):
        tid = row["token_id"]
        if tid in seen:
            continue
        seen.add(tid)
        deduped.append(row)
    return deduped


def print_candidates(candidates: List[dict]) -> None:
    print(f"Merged scan pool candidates: {len(candidates)}")
    print(f"  source={bot.SNIPER_MARKET_SOURCE}  window={bot.format_resolution_window()}")
    print(f"  min_notional=${CLOB_MIN_ORDER_USDC:.2f} (CLOB_MIN_ORDER_USDC)")
    if not candidates:
        print("  (none — relax .env filters or run show_pools.py)")
        return
    for idx, row in enumerate(candidates[:25]):
        bid = row.get("bid")
        ask = row["ask_price"]
        bid_txt = f"{bid:.4f}" if bid is not None else "—"
        hrs = row.get("hours_to_resolution")
        hrs_txt = f"{hrs:.1f}h" if hrs is not None else "—"
        print(
            f"  [{idx}] score={row['market_score']}  {row['outcome']}  "
            f"bid={bid_txt}  ask={ask:.4f}  hrs={hrs_txt}  "
            f"spread={row['spread_pct']:.2f}%  depth=${row['depth']:.0f}  "
            f"vol24h=${row['volume_24h']:.0f}"
        )
        print(f"       {row['market_question'][:88]}")
    if len(candidates) > 25:
        print(f"  ... {len(candidates) - 25} more (use --pick)")


def run_roundtrip(candidate: dict, notional_usd: float, *, max_notional: float) -> int:
    token_id = candidate["token_id"]
    ask = float(candidate["ask_price"])

    bid, live_ask = bot.fetch_order_book(token_id)
    if live_ask is not None:
        ask = live_ask
    if bid is None or ask is None or ask <= 0:
        print("Refusing trade: order book missing bid/ask.")
        return 1

    trader = live_trader_singleton()
    try:
        qty, limit_price, used_notional = resolve_buy_qty(
            trader,
            token_id,
            candidate.get("condition_id") or "",
            ask,
            notional_usd,
            max_notional=max_notional,
        )
    except ValueError as exc:
        print(f"\nBUY sizing failed: {exc}")
        return 1

    print("\nRound-trip target:")
    print(f"  {candidate['outcome']}  token={token_id[:20]}...")
    print(f"  {candidate['market_question']}")
    print(
        f"  bid={bid:.4f}  ask={ask:.4f}  target_notional=${notional_usd:.2f}  "
        f"planned_notional≈${used_notional:.2f}  qty≈{qty:.4f}  limit≈{limit_price:.4f}"
    )

    buy_resp: Any = {}
    try:
        buy_resp = trader.place_buy_fak(
            token_id,
            qty,
            ask,
            (candidate.get("condition_id") or "").strip() or None,
            bot.SLIPPAGE_TOLERANCE,
        )
        print("\nBUY response:")
        print(json.dumps(safe_json(buy_resp), indent=2, default=str))
    except Exception as exc:
        print(f"\nBUY failed: {exc}")
        return 1

    print(f"\nWaiting {bot.SELL_ALLOWANCE_DELAY_SEC:.0f}s for conditional allowance...")
    time.sleep(bot.SELL_ALLOWANCE_DELAY_SEC)

    bid, _ = bot.fetch_order_book(token_id)
    if bid is None or bid <= 0:
        print("SELL skipped: no bid after buy.")
        return 1

    sell_qty = max(0.0, round(float(qty), 4))
    if sell_qty <= 0:
        print("SELL skipped: zero size.")
        return 1

    try:
        trader.refresh_conditional_allowance(token_id)
        sell_resp = trader.place_sell_fak(
            token_id,
            sell_qty,
            bid,
            (candidate.get("condition_id") or "").strip() or None,
            bot.SLIPPAGE_TOLERANCE_SELL,
        )
        print("\nSELL response:")
        print(json.dumps(safe_json(sell_resp), indent=2, default=str))
    except Exception as exc:
        print(f"\nSELL failed: {exc}")
        return 1

    print("\nRound-trip submitted (check wallet / Polymarket UI for fills).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Live min-notional buy then sell on one pool leg.")
    parser.add_argument("--list", action="store_true", help="List pool candidates and exit.")
    parser.add_argument("--live", action="store_true", help="Submit real BUY then SELL orders.")
    parser.add_argument("--pick", type=int, default=0, help="Candidate index from --list (default 0).")
    parser.add_argument(
        "--notional",
        type=float,
        default=CLOB_MIN_ORDER_USDC,
        help=f"Target USDC notional for the buy (default {CLOB_MIN_ORDER_USDC}).",
    )
    parser.add_argument(
        "--max-notional",
        type=float,
        default=15.0,
        help="Upper bound when bumping notional to satisfy CLOB cent alignment (default 15).",
    )
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation.")
    args = parser.parse_args()

    if args.notional < CLOB_MIN_ORDER_USDC:
        print(f"--notional must be >= ${CLOB_MIN_ORDER_USDC:.2f} (CLOB minimum).")
        return 1
    if args.max_notional < args.notional:
        print("--max-notional must be >= --notional.")
        return 1

    print(f"Live round-trip probe @ {datetime.now().isoformat(timespec='seconds')}")
    candidates = collect_candidates()
    print_candidates(candidates)

    if args.list or not args.live:
        if not args.list and not args.live:
            print("\nDry run only. Re-run with --live --yes to trade.")
        return 0 if candidates else 1

    if not candidates:
        return 1
    if args.pick < 0 or args.pick >= len(candidates):
        print(f"--pick {args.pick} out of range (0..{len(candidates) - 1}).")
        return 1
    if not _live_keys_ok():
        print("Missing PRIVATE_KEY / PROXY_PRIVATE_KEY in environment.")
        return 1

    chosen = candidates[args.pick]
    if not args.yes:
        prompt = (
            f"Submit live BUY+SELL on [{args.pick}] {chosen['outcome']} "
            f"for ~${args.notional:.2f}? [y/N]: "
        )
        if input(prompt).strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    return run_roundtrip(chosen, args.notional, max_notional=args.max_notional)


if __name__ == "__main__":
    sys.exit(main())
