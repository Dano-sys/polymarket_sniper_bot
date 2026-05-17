"""Print market pools first, then scan summary at the end."""
from __future__ import annotations

import argparse
from collections import Counter
from typing import List

import polymarket_sniper_bot as bot
from dashboard_snapshot import (
    LegView,
    build_full_snapshot,
    condition_key,
    scan_window_legs,
    write_snapshot,
)


def _fmt_end(market: dict) -> str:
    end = bot._market_end_datetime(market)
    return end.isoformat() if end else "—"


def _fmt_vol(market: dict) -> str:
    vol = market.get("gamma_volume_24h")
    if vol is None:
        return "—"
    return f"${float(vol):,.0f}"


def _fmt_liq(market: dict) -> str:
    liq = market.get("gamma_liquidity")
    if liq is None:
        return "—"
    return f"${float(liq):,.0f}"


def _outcome_prices(market: dict) -> str:
    parts: List[str] = []
    for tok in market.get("tokens") or []:
        if not isinstance(tok, dict):
            continue
        outcome = str(tok.get("outcome", "?"))
        price = tok.get("price")
        if price is None:
            parts.append(outcome)
            continue
        try:
            p = float(price)
            label = f"{outcome}={p:.4f}"
            if p >= bot.PRICE_THRESHOLD:
                label += " [favored]"
            parts.append(label)
        except (TypeError, ValueError):
            parts.append(outcome)
    return ", ".join(parts) if parts else "—"


def _print_market_pool(title: str, markets: List[dict], *, limit: int) -> None:
    print(f"\n{title} ({len(markets)} markets)")
    print("-" * len(title))
    cap = len(markets) if limit <= 0 else min(limit, len(markets))
    for idx in range(cap):
        market = markets[idx]
        question = str(market.get("question") or market.get("title") or "N/A")
        close = "yes" if bot.is_close_to_resolution(market) else "no"
        hrs = bot.hours_until_resolution(market)
        hrs_txt = f"{hrs:.1f}h" if hrs is not None else "—"
        book = "yes" if market.get("enable_order_book", market.get("enableOrderBook", True)) else "no"
        window = bot.format_resolution_window()
        print(
            f"{idx + 1:>4}. {question[:72]}"
            f"\n      end={_fmt_end(market)}  hrs_to_end={hrs_txt}  in_window({window})={close}"
            f"  vol24h={_fmt_vol(market)}  gamma_liq={_fmt_liq(market)}  book={book}"
            f"\n      outcomes: {_outcome_prices(market)}"
        )
    if limit > 0 and len(markets) > limit:
        print(f"      ... {len(markets) - limit} more (raise --limit or use 0 for all)")


def _print_info_footer(
    gamma: List[dict],
    sampling: List[dict],
    pool: List[dict],
    overlap: int,
    window_legs: List[LegView],
    selected: List[LegView],
) -> None:
    print("\n" + "=" * 72)
    print("INFO (scan summary — tune .env from here)")
    print("=" * 72)

    print("\nPool sizes:")
    print(f"  SNIPER_MARKET_SOURCE={bot.SNIPER_MARKET_SOURCE}")
    print(f"  gamma_liquid_pool={len(gamma)}")
    print(f"  clob_sampling_pool={len(sampling)}")
    print(f"  overlap={overlap}")
    print(f"  merged_scan_pool={len(pool)}")

    print("\nActive env:")
    print(f"  MIN_HOURS_TO_RESOLUTION={bot.MIN_HOURS_TO_RESOLUTION}")
    print(f"  MAX_HOURS_TO_RESOLUTION={bot.MAX_HOURS_TO_RESOLUTION}")
    print(f"  resolution_window={bot.format_resolution_window()}")
    print(f"  PRICE_THRESHOLD={bot.PRICE_THRESHOLD}")
    print(f"  MIN_MARKET_SCORE={bot.MIN_MARKET_SCORE}")
    print(f"  MIN_24H_VOLUME={bot.MIN_24H_VOLUME}")
    print(f"  MIN_ORDER_BOOK_DEPTH={bot.MIN_ORDER_BOOK_DEPTH}")
    print(f"  MIN_BID_ASK_SPREAD={bot.MIN_BID_ASK_SPREAD}  (max spread {bot.MAX_SPREAD_PCT:.2f}%)")
    print(f"  MIN_GAMMA_LIQUIDITY={bot.MIN_GAMMA_LIQUIDITY}")
    print(f"  SNIPER_MARKET_POOL_SIZE={bot.SNIPER_MARKET_POOL_SIZE}")
    print(f"  PROFIT_TARGET_PCT={bot.PROFIT_TARGET_PCT}")
    print(f"  STOP_LOSS_PCT={bot.STOP_LOSS_PCT}")
    if bot.REQUIRE_PROFIT_ROOM:
        print(
            f"  max_entry_ask={bot.effective_max_entry_ask():.4f} "
            f"(exit cap {bot.profit_room_exit_cap():.4f}, REQUIRE_PROFIT_ROOM)"
        )

    print("\nSelection funnel (in-window legs, live book checks):")
    print(f"  legs_in_window={len(window_legs)}")
    print(f"  with_clob_book={sum(1 for leg in window_legs if leg.has_book)}")
    print(f"  liquid={sum(1 for leg in window_legs if leg.liquid)}")
    print(f"  price_ok={sum(1 for leg in window_legs if leg.price_ok)}")
    print(f"  score_ok={sum(1 for leg in window_legs if leg.score_ok)}")
    print(f"  selected={len(selected)}")

    counts: Counter[str] = Counter()
    for leg in window_legs:
        for reason in leg.blockers:
            counts[reason] += 1
    if counts:
        print("\nBlockers (in-window legs):")
        for reason, n in counts.most_common():
            hint = {
                "outside_resolution_window": "adjust MIN_HOURS_TO_RESOLUTION / MAX_HOURS_TO_RESOLUTION (0 max = no upper cap)",
                "too_soon_to_resolution": "lower MIN_HOURS_TO_RESOLUTION",
                "gamma_price_below_threshold": "lower PRICE_THRESHOLD",
                "ask_below_threshold": "lower PRICE_THRESHOLD",
                "no_profit_room": "lower MAX_ENTRY_ASK, raise PROFIT_ROOM_PRICE_BUFFER, or lower PROFIT_TARGET_PCT",
                "upside_score": "lower MIN_MARKET_SCORE or UPSIDE_POINTS_*",
                "liquidity": "lower MIN_24H_VOLUME / MIN_ORDER_BOOK_DEPTH / MIN_BID_ASK_SPREAD",
                "no_clob_book": "settled or no CLOB book",
            }.get(reason, "")
            suffix = f"  -> {hint}" if hint else ""
            print(f"  {reason}: {n}{suffix}")

    print(f"\nWould trade now ({len(selected)}):")
    if not selected:
        print("  (none)")
    else:
        for leg in selected[:25]:
            ask = f"{leg.ask:.4f}" if leg.ask is not None else "—"
            print(f"  - {leg.outcome} @ {ask}  score={leg.score}  {leg.question[:56]}")
        if len(selected) > 25:
            print(f"  ... {len(selected) - 25} more")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pools first, scan info at the end.")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max markets per pool section (0 = all)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Write pool_snapshot.json for the dashboard instead of printing pools",
    )
    args = parser.parse_args()

    if args.json:
        path = write_snapshot(build_full_snapshot())
        print(f"Wrote dashboard snapshot to {path}")
        return

    gamma = bot.fetch_gamma_liquid_markets()
    sampling = bot.fetch_sampling_markets()
    overlap = len({condition_key(m) for m in gamma} & {condition_key(m) for m in sampling})
    pool = bot.fetch_scan_markets()

    _print_market_pool("Gamma liquid pool", gamma, limit=args.limit)
    _print_market_pool("CLOB sampling pool", sampling, limit=args.limit)
    _print_market_pool("Merged scan pool", pool, limit=args.limit)

    window_legs = scan_window_legs(pool)
    selected = [leg for leg in window_legs if leg.selected]
    selected.sort(key=lambda leg: (leg.score, leg.ask or 0.0), reverse=True)
    _print_info_footer(gamma, sampling, pool, overlap, window_legs, selected)


if __name__ == "__main__":
    main()
