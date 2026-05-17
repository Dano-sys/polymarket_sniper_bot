"""Build JSON snapshots for the sniper dashboard."""
from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import polymarket_sniper_bot as bot

DEFAULT_SNAPSHOT_FILE = os.getenv("DASHBOARD_SNAPSHOT_FILE", "pool_snapshot.json")
MAX_POOL_ROWS = max(25, int(os.getenv("DASHBOARD_POOL_ROW_LIMIT", "250")))


@dataclass
class LegView:
    question: str
    outcome: str
    in_window: bool
    has_book: bool
    liquid: bool
    price_ok: bool
    score: int
    score_ok: bool
    selected: bool
    ask: Optional[float]
    blockers: List[str]


def condition_key(market: dict) -> str:
    condition_id = str(market.get("condition_id") or market.get("conditionId") or "").lower()
    if condition_id:
        return condition_id
    return str(market.get("question") or market.get("id") or id(market))


def _market_end_iso(market: dict) -> Optional[str]:
    end = bot._market_end_datetime(market)
    return end.isoformat() if end else None


def serialize_market(market: dict, *, source: str = "merged") -> dict:
    question = str(market.get("question") or market.get("title") or "N/A")
    hrs = bot.hours_until_resolution(market)
    outcomes: List[dict] = []
    for tok in market.get("tokens") or []:
        if not isinstance(tok, dict):
            continue
        outcome = str(tok.get("outcome", "?"))
        price = tok.get("price")
        favored = False
        if price is not None:
            try:
                favored = float(price) >= bot.PRICE_THRESHOLD
            except (TypeError, ValueError):
                pass
        outcomes.append(
            {
                "outcome": outcome,
                "price": float(price) if price is not None else None,
                "favored": favored,
                "token_id": str(tok.get("token_id") or tok.get("tokenId") or ""),
            }
        )
    return {
        "source": source,
        "condition_id": str(market.get("condition_id") or market.get("conditionId") or ""),
        "question": question,
        "end_date": _market_end_iso(market),
        "hours_to_resolution": round(hrs, 2) if hrs is not None else None,
        "in_window": bot.is_close_to_resolution(market),
        "volume_24h": market.get("gamma_volume_24h"),
        "gamma_liquidity": market.get("gamma_liquidity"),
        "order_book_enabled": bool(
            market.get("enable_order_book", market.get("enableOrderBook", True))
        ),
        "accepting_orders": bool(
            market.get("accepting_orders", market.get("acceptingOrders", True))
        ),
        "outcomes": outcomes,
    }


def serialize_leg(leg: LegView) -> dict:
    row = asdict(leg)
    if leg.ask is not None:
        row["ask"] = round(float(leg.ask), 6)
    return row


def serialize_opportunity(opp: dict) -> dict:
    return {
        "market_id": opp.get("market_id"),
        "condition_id": opp.get("condition_id"),
        "market_question": opp.get("market_question"),
        "token_id": opp.get("token_id"),
        "outcome": opp.get("outcome"),
        "ask_price": round(float(opp["ask_price"]), 6) if opp.get("ask_price") is not None else None,
        "bid": round(float(opp["bid"]), 6) if opp.get("bid") is not None else None,
        "spread_pct": round(float(opp["spread_pct"]), 4) if opp.get("spread_pct") is not None else None,
        "depth": opp.get("depth"),
        "volume_24h": opp.get("volume_24h"),
        "hours_to_resolution": (
            round(float(opp["hours_to_resolution"]), 2)
            if opp.get("hours_to_resolution") is not None
            else None
        ),
        "market_score": opp.get("market_score"),
        "neg_risk_hint": opp.get("neg_risk_hint"),
    }


def _config_payload() -> dict:
    return {
        "dry_run": bot.DRY_RUN,
        "market_source": bot.SNIPER_MARKET_SOURCE,
        "pool_size": bot.SNIPER_MARKET_POOL_SIZE,
        "resolution_window": bot.format_resolution_window(),
        "min_hours_to_resolution": bot.MIN_HOURS_TO_RESOLUTION,
        "max_hours_to_resolution": bot.MAX_HOURS_TO_RESOLUTION,
        "price_threshold": bot.PRICE_THRESHOLD,
        "min_market_score": bot.MIN_MARKET_SCORE,
        "min_24h_volume": bot.MIN_24H_VOLUME,
        "min_order_book_depth": bot.MIN_ORDER_BOOK_DEPTH,
        "max_spread_pct": bot.MAX_SPREAD_PCT,
        "min_gamma_liquidity": bot.MIN_GAMMA_LIQUIDITY,
        "profit_target_pct": bot.PROFIT_TARGET_PCT,
        "stop_loss_pct": bot.STOP_LOSS_PCT,
        "max_positions": bot.MAX_POSITIONS,
        "position_size_usd": bot.POSITION_SIZE_USD,
    }


def evaluate_leg(market: dict, tok: dict) -> LegView:
    question = str(market.get("question") or market.get("title") or "N/A")
    outcome = str(tok.get("outcome", "Unknown"))
    blockers: List[str] = []

    if not market.get("accepting_orders", market.get("acceptingOrders", True)):
        blockers.append("not_accepting_orders")
    if not market.get("enable_order_book", market.get("enableOrderBook", True)):
        blockers.append("order_book_disabled")
    if not bot.is_close_to_resolution(market):
        hrs = bot.hours_until_resolution(market)
        if hrs is not None and bot.MIN_HOURS_TO_RESOLUTION > 0 and hrs < bot.MIN_HOURS_TO_RESOLUTION:
            blockers.append("too_soon_to_resolution")
        elif (
            hrs is not None
            and bot.MAX_HOURS_TO_RESOLUTION > 0
            and hrs >= bot.MAX_HOURS_TO_RESOLUTION
        ):
            blockers.append("outside_resolution_window")
        else:
            blockers.append("outside_resolution_window")

    token_id = str(tok.get("token_id") or tok.get("tokenId") or "")
    if not token_id:
        blockers.append("missing_token_id")
        return LegView(
            question=question,
            outcome=outcome,
            in_window=bot.is_close_to_resolution(market),
            has_book=False,
            liquid=False,
            price_ok=False,
            score=0,
            score_ok=False,
            selected=False,
            ask=None,
            blockers=blockers,
        )

    price_hint = tok.get("price")
    if price_hint is not None:
        try:
            if float(price_hint) < bot.PRICE_THRESHOLD:
                blockers.append("gamma_price_below_threshold")
                return LegView(
                    question=question,
                    outcome=outcome,
                    in_window=bot.is_close_to_resolution(market),
                    has_book=False,
                    liquid=False,
                    price_ok=False,
                    score=0,
                    score_ok=False,
                    selected=False,
                    ask=None,
                    blockers=blockers,
                )
        except (TypeError, ValueError):
            pass

    condition_id = market.get("condition_id") or market.get("conditionId") or ""
    vol_hint = market.get("gamma_volume_24h")
    if vol_hint is None and condition_id:
        vol_hint = bot.gamma_volume_24h(condition_id)

    book_data = bot._fetch_order_book_payload(token_id)
    has_book = book_data is not None
    if not has_book:
        blockers.append("no_clob_book")

    ask: Optional[float] = None
    liquid = False
    price_ok = False
    score = 0
    score_ok = False

    if has_book:
        is_liquid, spread_pct, _depth, volume_24h, _bid, ask = bot.check_liquidity(
            token_id, condition_id or None, vol_hint, book_data
        )
        liquid = is_liquid
        if not liquid:
            blockers.append("liquidity")
        if ask is None:
            blockers.append("no_ask")
        else:
            price_ok = bot.should_buy(ask)
            if not price_ok:
                blockers.append("ask_below_threshold")
            elif not bot.has_profit_room(ask):
                blockers.append("no_profit_room")
            if spread_pct is not None and volume_24h is not None:
                score = bot.score_market_for_upside(market, ask, spread_pct, float(volume_24h))
                score_ok = score >= bot.MIN_MARKET_SCORE
                if not score_ok:
                    blockers.append("upside_score")

    return LegView(
        question=question,
        outcome=outcome,
        in_window=bot.is_close_to_resolution(market),
        has_book=has_book,
        liquid=liquid,
        price_ok=price_ok,
        score=score,
        score_ok=score_ok,
        selected=not blockers,
        ask=ask,
        blockers=blockers,
    )


def scan_window_legs(pool: List[dict]) -> List[LegView]:
    legs: List[LegView] = []
    for market in pool:
        if not bot.is_close_to_resolution(market):
            continue
        for tok in market.get("tokens") or []:
            if isinstance(tok, dict):
                legs.append(evaluate_leg(market, tok))
    return legs


def _serialize_pool_rows(markets: List[dict], *, source: str) -> List[dict]:
    rows = [serialize_market(market, source=source) for market in markets]
    if len(rows) > MAX_POOL_ROWS:
        return rows[:MAX_POOL_ROWS]
    return rows


def build_full_snapshot(*, include_pools: bool = True) -> dict:
    gamma = bot.fetch_gamma_liquid_markets()
    sampling = bot.fetch_sampling_markets()
    gamma_keys = {condition_key(market) for market in gamma}
    sampling_keys = {condition_key(market) for market in sampling}
    overlap = len(gamma_keys & sampling_keys)
    pool = bot.fetch_scan_markets()
    window_legs = scan_window_legs(pool)
    selected = [leg for leg in window_legs if leg.selected]
    selected.sort(key=lambda leg: (leg.score, leg.ask or 0.0), reverse=True)

    blockers: Counter[str] = Counter()
    for leg in window_legs:
        for reason in leg.blockers:
            blockers[reason] += 1

    payload: Dict[str, Any] = {
        "updated_at": datetime.now().isoformat(),
        "scan_cycle": None,
        "mode": "full",
        "config": _config_payload(),
        "pool_counts": {
            "gamma": len(gamma),
            "sampling": len(sampling),
            "overlap": overlap,
            "merged": len(pool),
        },
        "funnel": {
            "legs_in_window": len(window_legs),
            "with_clob_book": sum(1 for leg in window_legs if leg.has_book),
            "liquid": sum(1 for leg in window_legs if leg.liquid),
            "price_ok": sum(1 for leg in window_legs if leg.price_ok),
            "score_ok": sum(1 for leg in window_legs if leg.score_ok),
            "selected": len(selected),
        },
        "blockers": dict(blockers),
        "selected": [serialize_leg(leg) for leg in selected],
        "window_legs": [serialize_leg(leg) for leg in window_legs],
    }
    if include_pools:
        payload["pools"] = {
            "gamma": _serialize_pool_rows(gamma, source="gamma"),
            "sampling": _serialize_pool_rows(sampling, source="sampling"),
            "merged": _serialize_pool_rows(pool, source="merged"),
        }
    return payload


def build_scan_snapshot(
    markets: List[dict],
    opportunities: List[dict],
    *,
    scan_cycle: int,
    skipped_profit_room: int = 0,
) -> dict:
    in_window_markets = sum(1 for market in markets if bot.is_close_to_resolution(market))
    return {
        "updated_at": datetime.now().isoformat(),
        "scan_cycle": scan_cycle,
        "mode": "scan",
        "config": _config_payload(),
        "pool_counts": {
            "gamma": None,
            "sampling": None,
            "overlap": None,
            "merged": len(markets),
        },
        "funnel": {
            "merged_pool": len(markets),
            "in_window_markets": in_window_markets,
            "candidates": len(opportunities),
            "skipped_profit_room": skipped_profit_room,
        },
        "blockers": {},
        "selected": [serialize_opportunity(opp) for opp in opportunities],
        "pools": {
            "merged": _serialize_pool_rows(markets, source="merged"),
        },
    }


def write_snapshot(payload: dict, path: str | Path | None = None) -> Path:
    target = Path(path or DEFAULT_SNAPSHOT_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return target


def write_scan_snapshot(
    markets: List[dict],
    opportunities: List[dict],
    *,
    scan_cycle: int,
    skipped_profit_room: int = 0,
    path: str | Path | None = None,
) -> Path:
    payload = build_scan_snapshot(
        markets,
        opportunities,
        scan_cycle=scan_cycle,
        skipped_profit_room=skipped_profit_room,
    )
    return write_snapshot(payload, path)
