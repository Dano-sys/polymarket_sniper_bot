"""Print per-stage funnel counts for the current sniper env (read-only diagnostic)."""
from __future__ import annotations

import polymarket_sniper_bot as bot


def main() -> None:
    markets = bot.fetch_scan_markets()
    counts = {
        "total_pool": len(markets),
        "accepting_orders": 0,
        "close_to_resolution": 0,
        "token_legs_with_ask": 0,
        "liquid": 0,
        "price_threshold": 0,
        "profit_room": 0,
        "min_market_score": 0,
        "final_candidates": 0,
    }
    missing_end = 0
    missing_book = 0

    for market in markets:
        if not market.get("accepting_orders", market.get("acceptingOrders", True)):
            continue
        counts["accepting_orders"] += 1
        if bot._market_end_datetime(market) is None:
            missing_end += 1
        if not market.get("enable_order_book", market.get("enableOrderBook", True)):
            continue
        if not bot.is_close_to_resolution(market):
            continue
        counts["close_to_resolution"] += 1
        condition_id = market.get("condition_id") or market.get("conditionId") or ""
        vol_hint = market.get("gamma_volume_24h")
        if vol_hint is None and condition_id:
            vol_hint = bot.gamma_volume_24h(condition_id)
        for tok in market.get("tokens") or []:
            if not isinstance(tok, dict):
                continue
            token_id = str(tok.get("token_id") or tok.get("tokenId") or "")
            if not token_id:
                continue
            price_hint = tok.get("price")
            if price_hint is not None:
                try:
                    if float(price_hint) < bot.PRICE_THRESHOLD:
                        continue
                except (TypeError, ValueError):
                    pass
            book_data = bot._fetch_order_book_payload(token_id)
            if not book_data:
                missing_book += 1
                continue
            is_liquid, spread_pct, depth, volume, _bid, ask = bot.check_liquidity(
                token_id, condition_id or None, vol_hint, book_data
            )
            if ask is None:
                continue
            counts["token_legs_with_ask"] += 1
            if not is_liquid:
                continue
            counts["liquid"] += 1
            if not bot.should_buy(ask, market):
                continue
            counts["price_threshold"] += 1
            if bot.MAX_ENTRY_ASK > 0 and ask > bot.MAX_ENTRY_ASK + 1e-9:
                continue
            if not bot.has_profit_room(ask):
                continue
            counts["profit_room"] += 1
            if spread_pct is None or volume is None:
                continue
            score = bot.score_market_for_upside(
                market,
                ask,
                spread_pct,
                float(volume),
                hours_left=bot.hours_until_resolution(market),
            )
            if score < bot.MIN_MARKET_SCORE:
                continue
            counts["min_market_score"] += 1
            counts["final_candidates"] += 1

    print("Sniper funnel (current env):")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    print(f"  markets_missing_end_date: {missing_end}")
    print(f"  token_legs_missing_clob_book: {missing_book}")
    print(
        "  config: "
        f"MIN_HOURS_TO_RESOLUTION={bot.MIN_HOURS_TO_RESOLUTION} "
        f"MAX_HOURS_TO_RESOLUTION={bot.MAX_HOURS_TO_RESOLUTION} "
        f"PRICE_THRESHOLD={bot.PRICE_THRESHOLD} "
        f"MAX_ENTRY_ASK={bot.MAX_ENTRY_ASK} "
        f"REQUIRE_PROFIT_ROOM={bot.REQUIRE_PROFIT_ROOM} "
        f"PROFIT_TARGET_PCT={bot.PROFIT_TARGET_PCT} "
        f"PROFIT_ROOM_PRICE_BUFFER={bot.PROFIT_ROOM_PRICE_BUFFER} "
        f"MIN_MARKET_SCORE={bot.MIN_MARKET_SCORE} "
        f"MAX_SPREAD_PCT={bot.MAX_SPREAD_PCT} "
        f"MIN_24H_VOLUME={bot.MIN_24H_VOLUME} "
        f"MIN_ORDER_BOOK_DEPTH={bot.MIN_ORDER_BOOK_DEPTH} "
        f"SNIPER_MARKET_SOURCE={bot.SNIPER_MARKET_SOURCE} "
        f"SNIPER_MARKET_POOL_SIZE={bot.SNIPER_MARKET_POOL_SIZE} "
        f"CLOB_SAMPLING_POOL_SIZE={bot.CLOB_SAMPLING_POOL_SIZE} "
        f"MIN_GAMMA_LIQUIDITY={bot.MIN_GAMMA_LIQUIDITY}"
    )


if __name__ == "__main__":
    main()
