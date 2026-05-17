"""
Polymarket Late-Stage Outcome Sniper Bot
==========================================
Buys outcomes at or above PRICE_THRESHOLD (often Yes or No), takes profit before resolution.

Env loading: sniper_env.load_sniper_env() — same secrets as polymarket_copy_bot_v2
(COPY_BOT_ENV_PATH or ../polymarket_copy_bot_v2/.env, then local .env overrides).

Live: set DRY_RUN=false, PRIVATE_KEY + FUNDER_ADDRESS (+ POLY_BUILDER_CODE for CLOB V2 attribution) per copy_bot env.
Paper / observe: DRY_RUN=true (default).

Run: python polymarket_sniper_bot.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

# Load copy_bot .env first, then sniper .env (see sniper_env.py).
from sniper_env import load_sniper_env
import sniper_log as slog

load_sniper_env()
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)


def _resolve_state_path(env_key: str, default_name: str) -> str:
    raw = (os.getenv(env_key) or default_name).strip()
    path = Path(raw)
    if path.is_absolute():
        return str(path)
    data_root = (os.getenv("SNIPER_DATA_DIR") or os.getenv("DATA_DIR") or "").strip()
    if data_root:
        return str(Path(data_root).expanduser() / path.name)
    return raw


def _ensure_parent_dir(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)

API_BASE = os.getenv("CLOB_API", "https://clob.polymarket.com").rstrip("/")
GAMMA_API = os.getenv("GAMMA_API", "https://gamma-api.polymarket.com").rstrip("/")
TESTNET_MODE = os.getenv("TESTNET_MODE", "False").lower() == "true"
if TESTNET_MODE:
    API_BASE = os.getenv("TESTNET_CLOB_API", "https://amoy-testnet.polymarket.com").rstrip("/")
    slog.warn("TESTNET MODE — limited markets")

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
DRY_RUN_RESET_ON_START = os.getenv("DRY_RUN_RESET_ON_START", "false").lower() == "true"
PROFIT_TARGET_PCT = float(os.getenv("PROFIT_TARGET_PCT", 2.5))
MAX_OUTCOME_PRICE = float(os.getenv("MAX_OUTCOME_PRICE", "1.0"))
REQUIRE_PROFIT_ROOM = os.getenv("REQUIRE_PROFIT_ROOM", "true").lower() == "true"
PROFIT_ROOM_PRICE_BUFFER = max(0.0, float(os.getenv("PROFIT_ROOM_PRICE_BUFFER", "0")))
_max_entry_ask = (os.getenv("MAX_ENTRY_ASK") or "").strip()
MAX_ENTRY_ASK = float(_max_entry_ask) if _max_entry_ask else 0.0
PRICE_THRESHOLD = float(os.getenv("PRICE_THRESHOLD", 0.80))
POSITION_SIZE_USD = float(os.getenv("POSITION_SIZE_USD", 100))
COMPOUND_POSITIONS = os.getenv("COMPOUND_POSITIONS", "true").lower() == "true"
_min_stake_raw = (os.getenv("MIN_POSITION_SIZE_USD") or "").strip()
MIN_POSITION_SIZE_USD = (
    float(_min_stake_raw)
    if _min_stake_raw
    else max(1.0, float(os.getenv("CLOB_MIN_ORDER_USDC", "1.0")))
)
_max_stake_raw = (os.getenv("MAX_POSITION_SIZE_USD") or "").strip()
MAX_POSITION_SIZE_USD = float(_max_stake_raw) if _max_stake_raw else 0.0
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", 5))
MIN_HOURS_TO_RESOLUTION = float(os.getenv("MIN_HOURS_TO_RESOLUTION", "0"))
_max_hours_raw = (os.getenv("MAX_HOURS_TO_RESOLUTION") or "").strip()
if _max_hours_raw:
    MAX_HOURS_TO_RESOLUTION = float(_max_hours_raw)
else:
    MAX_HOURS_TO_RESOLUTION = float(os.getenv("HOURS_TO_RESOLUTION", "12"))
# Legacy alias for older scripts; 0 means no upper cap on hours-to-end.
HOURS_TO_RESOLUTION = int(MAX_HOURS_TO_RESOLUTION) if MAX_HOURS_TO_RESOLUTION > 0 else 0
# Max spread: env fraction 0.02 = 2% of mid (same as SETUP_GUIDE); internally compared to spread_pct 0–100 scale.
MIN_BID_ASK_SPREAD = float(os.getenv("MIN_BID_ASK_SPREAD", 0.02))
MIN_24H_VOLUME = float(os.getenv("MIN_24H_VOLUME", 1000))
MIN_ORDER_BOOK_DEPTH = float(os.getenv("MIN_ORDER_BOOK_DEPTH", 500))
SNIPER_MARKET_SOURCE = (os.getenv("SNIPER_MARKET_SOURCE", "both") or "both").strip().lower()
SNIPER_MARKET_POOL_SIZE = max(1, int(os.getenv("SNIPER_MARKET_POOL_SIZE", "150")))
GAMMA_MARKET_PAGE_LIMIT = max(1, min(200, int(os.getenv("GAMMA_MARKET_PAGE_LIMIT", "100"))))
CLOB_SAMPLING_POOL_SIZE = max(1, int(os.getenv("CLOB_SAMPLING_POOL_SIZE", "1000")))
MIN_GAMMA_LIQUIDITY = float(os.getenv("MIN_GAMMA_LIQUIDITY", "5000"))
SLIPPAGE_TOLERANCE = float(os.getenv("SLIPPAGE_TOLERANCE", "0.04"))
SLIPPAGE_TOLERANCE_SELL = float(os.getenv("SLIPPAGE_TOLERANCE_SELL", SLIPPAGE_TOLERANCE))
SNIPER_SLEEP_SECONDS = int(os.getenv("SNIPER_SLEEP_SECONDS", "60"))
SELL_ALLOWANCE_DELAY_SEC = float(os.getenv("SELL_ALLOWANCE_DELAY_SEC", "8"))
HTTP_REQUEST_TIMEOUT_SEC = float(os.getenv("HTTP_REQUEST_TIMEOUT_SEC", "25"))
ORDER_BOOK_TOP_LEVELS = max(1, int(os.getenv("ORDER_BOOK_TOP_LEVELS", "5")))

STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "3.0"))
STOP_LOSS_GRACE_SECONDS = max(0, int(os.getenv("STOP_LOSS_GRACE_SECONDS", "120") or "0"))
# Max (est. fill − bid) / bid in percent; rejects wide spreads + slippage before entry.
MAX_ENTRY_DRAG_PCT = max(0.0, float(os.getenv("MAX_ENTRY_DRAG_PCT", "5.0") or "0"))
# Slippage assumed only for entry-drag screening (actual fills may use SLIPPAGE_TOLERANCE).
ENTRY_DRAG_SLIPPAGE = max(0.0, float(os.getenv("ENTRY_DRAG_SLIPPAGE", "0.015") or "0"))
REQUIRE_MARKET_MOMENTUM = os.getenv("REQUIRE_MARKET_MOMENTUM", "false").lower() == "true"
MIN_MARKET_SCORE = int(os.getenv("MIN_MARKET_SCORE", "0"))

UPSIDE_SCORE_MAX = max(1, int(os.getenv("UPSIDE_SCORE_MAX", "10")))
UPSIDE_POINTS_CATALYST = int(os.getenv("UPSIDE_POINTS_CATALYST", "3"))
UPSIDE_POINTS_VOLUME = int(os.getenv("UPSIDE_POINTS_VOLUME", "2"))
UPSIDE_POINTS_MOMENTUM = int(os.getenv("UPSIDE_POINTS_MOMENTUM", "2"))
UPSIDE_VOLUME_SPIKE_MULTIPLIER = float(os.getenv("UPSIDE_VOLUME_SPIKE_MULTIPLIER", "2.0"))
UPSIDE_MOMENTUM_MAX_SPREAD_PCT = float(os.getenv("UPSIDE_MOMENTUM_MAX_SPREAD_PCT", "1.5"))
UPSIDE_MOMENTUM_MIN_ASK = float(os.getenv("UPSIDE_MOMENTUM_MIN_ASK", "1.0"))
UPSIDE_PRINT_MIN_SCORE = int(os.getenv("UPSIDE_PRINT_MIN_SCORE", "0"))

_raw_keywords = (os.getenv("UPSIDE_CATALYST_KEYWORDS") or "").strip()
UPSIDE_CATALYST_KEYWORDS: frozenset[str] = frozenset(
    p.strip().lower() for p in _raw_keywords.split(",") if p.strip()
)

_paper_start_raw = (os.getenv("PAPER_STARTING_BALANCE_USD") or "").strip()
PAPER_STARTING_BALANCE_USD = (
    float(_paper_start_raw)
    if _paper_start_raw
    else max(1000.0, POSITION_SIZE_USD * max(1, MAX_POSITIONS))
)

POSITIONS_FILE = _resolve_state_path("SNIPER_POSITIONS_FILE", "sniper_positions.json")
TRADE_LOG_FILE = _resolve_state_path("SNIPER_TRADE_LOG_FILE", "trade_log.json")
OPEN_ORDERS_FILE = _resolve_state_path("SNIPER_OPEN_ORDERS_FILE", "sniper_open_orders.json")
PAPER_ACCOUNT_FILE = _resolve_state_path("SNIPER_PAPER_ACCOUNT_FILE", "sniper_paper_account.json")
DASHBOARD_SNAPSHOT_FILE = _resolve_state_path("DASHBOARD_SNAPSHOT_FILE", "pool_snapshot.json")

SNIPER_KILL_SWITCH = os.getenv("SNIPER_KILL_SWITCH", "true").lower() == "true"
SNIPER_KILL_MAX_LOSS_USD = max(0.0, float(os.getenv("SNIPER_KILL_MAX_LOSS_USD", "0") or "0"))
SNIPER_KILL_MAX_SESSION_LOSS_USD = max(
    0.0, float(os.getenv("SNIPER_KILL_MAX_SESSION_LOSS_USD", "0") or "0")
)
SNIPER_KILL_MAX_CONSECUTIVE_ERRORS = max(
    0, int(os.getenv("SNIPER_KILL_MAX_CONSECUTIVE_ERRORS", "0") or "0")
)
SNIPER_KILL_LOSS_ACTION = (os.getenv("SNIPER_KILL_LOSS_ACTION", "soft") or "soft").strip().lower()
SNIPER_KILL_ERROR_ACTION = (os.getenv("SNIPER_KILL_ERROR_ACTION", "soft") or "soft").strip().lower()
SNIPER_KILL_SOFT_SLEEP_SECONDS = max(
    1, int(os.getenv("SNIPER_KILL_SOFT_SLEEP_SECONDS", "300") or "300")
)
SNIPER_KILL_CLEAR_ON_START = os.getenv("SNIPER_KILL_CLEAR_ON_START", "false").lower() == "true"
SNIPER_HALT_FILE = (os.getenv("SNIPER_HALT_FILE") or "").strip()
SNIPER_HALT_FILE_MODE = (os.getenv("SNIPER_HALT_FILE_MODE", "soft") or "soft").strip().lower()
SNIPER_KILL_STATE_FILE = _resolve_state_path("SNIPER_KILL_STATE_FILE", "sniper_kill_state.json")

# spread_pct is 0–100 (e.g. 2.5 means 2.5%). MIN_BID_ASK_SPREAD in .env is fractional (0.02 = 2%).
MAX_SPREAD_PCT = MIN_BID_ASK_SPREAD * 100.0 if MIN_BID_ASK_SPREAD <= 1.0 else MIN_BID_ASK_SPREAD

_gamma_vol_cache: Dict[str, float] = {}
_proxy = (os.getenv("POLYMARKET_PROXY") or "").strip()
_req_proxies = {"http": _proxy, "https": _proxy} if _proxy else None


def _get(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", HTTP_REQUEST_TIMEOUT_SEC)
    if _req_proxies:
        kwargs.setdefault("proxies", _req_proxies)
    return requests.get(url, **kwargs)


def gamma_volume_24h(condition_id: str) -> float:
    if not condition_id:
        return 0.0
    if condition_id in _gamma_vol_cache:
        return _gamma_vol_cache[condition_id]
    try:
        r = _get(f"{GAMMA_API}/markets", params={"condition_ids": condition_id})
        if not r.ok:
            _gamma_vol_cache[condition_id] = 0.0
            return 0.0
        data = r.json()
        rows = data if isinstance(data, list) else data.get("data") or []
        vol = 0.0
        for row in rows:
            if str(row.get("conditionId") or "").lower() != condition_id.lower():
                continue
            v = row.get("volume24hrClob")
            if v is None:
                v = row.get("volume24hr")
            if v is not None:
                vol = float(v)
            break
        _gamma_vol_cache[condition_id] = vol
        return vol
    except Exception:
        _gamma_vol_cache[condition_id] = 0.0
        return 0.0


positions: Dict[str, Dict[str, Any]] = {}
trade_log: List[Any] = []
open_orders: Dict[str, Dict[str, Any]] = {}
paper_cash_usd: float = PAPER_STARTING_BALANCE_USD

_kill_consecutive_errors = 0
_kill_session_start_pnl: Optional[float] = None
_kill_soft_until = 0.0
_kill_soft_reason = ""
_kill_hard_latched = False
_kill_hard_reason = ""
_scan_cycle = 0


class KillSwitchHardExit(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def load_state() -> None:
    global positions, trade_log, open_orders, paper_cash_usd
    if os.path.isfile(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE, "r") as f:
                positions = json.load(f)
            if not isinstance(positions, dict):
                positions = {}
        except Exception as e:
            print(f"Warning: could not load {POSITIONS_FILE}: {e}")
            positions = {}
    if os.path.isfile(TRADE_LOG_FILE):
        try:
            with open(TRADE_LOG_FILE, "r") as f:
                trade_log = json.load(f)
            if not isinstance(trade_log, list):
                trade_log = []
        except Exception:
            trade_log = []
    if os.path.isfile(OPEN_ORDERS_FILE):
        try:
            with open(OPEN_ORDERS_FILE, "r") as f:
                open_orders = json.load(f)
            if not isinstance(open_orders, dict):
                open_orders = {}
        except Exception as e:
            print(f"Warning: could not load {OPEN_ORDERS_FILE}: {e}")
            open_orders = {}
    if os.path.isfile(PAPER_ACCOUNT_FILE):
        try:
            with open(PAPER_ACCOUNT_FILE, "r") as f:
                paper_data = json.load(f)
            if isinstance(paper_data, dict) and paper_data.get("cash_usd") is not None:
                paper_cash_usd = float(paper_data["cash_usd"])
        except Exception as e:
            print(f"Warning: could not load {PAPER_ACCOUNT_FILE}: {e}")
    elif DRY_RUN and not positions:
        paper_cash_usd = PAPER_STARTING_BALANCE_USD


def save_state() -> None:
    try:
        for path in (POSITIONS_FILE, TRADE_LOG_FILE, OPEN_ORDERS_FILE, PAPER_ACCOUNT_FILE):
            _ensure_parent_dir(path)
        with open(POSITIONS_FILE, "w") as f:
            json.dump(positions, f, indent=2)
        with open(TRADE_LOG_FILE, "w") as f:
            json.dump(trade_log, f, indent=2)
        with open(OPEN_ORDERS_FILE, "w") as f:
            json.dump(open_orders, f, indent=2)
        if DRY_RUN:
            with open(PAPER_ACCOUNT_FILE, "w") as f:
                json.dump(
                    {
                        "cash_usd": round(float(paper_cash_usd), 2),
                        "starting_balance_usd": PAPER_STARTING_BALANCE_USD,
                        "updated_at": datetime.now().isoformat(),
                    },
                    f,
                    indent=2,
                )
    except Exception as e:
        print(f"Warning: save_state failed: {e}")


def reset_dry_run_state() -> None:
    """Clear paper positions, orders, and trade log for a fresh dry-run session."""
    global positions, trade_log, open_orders, paper_cash_usd
    positions = {}
    trade_log = []
    open_orders = {}
    paper_cash_usd = PAPER_STARTING_BALANCE_USD
    save_state()
    print(
        f"Dry run state reset: fresh paper portfolio "
        f"(${PAPER_STARTING_BALANCE_USD:,.2f}), no open positions."
    )


def _normalize_kill_action(action: str) -> str:
    return "hard" if action.strip().lower() == "hard" else "soft"


def _portfolio_pnl() -> Tuple[float, float, float]:
    unrealized = 0.0
    for position in positions.values():
        pnl, _, _, _, _ = _position_mark_pnl(position)
        unrealized += pnl
    realized = _realized_pnl_usd()
    return realized, unrealized, realized + unrealized


def _ensure_kill_session_baseline() -> None:
    global _kill_session_start_pnl
    if _kill_session_start_pnl is None:
        _, _, total = _portfolio_pnl()
        _kill_session_start_pnl = total


def _save_kill_state() -> None:
    if not SNIPER_KILL_STATE_FILE:
        return
    payload = {
        "hard_latched": _kill_hard_latched,
        "hard_reason": _kill_hard_reason,
        "soft_reason": _kill_soft_reason,
        "soft_until_monotonic": _kill_soft_until,
        "session_start_pnl": _kill_session_start_pnl,
        "consecutive_errors": _kill_consecutive_errors,
        "updated_at": datetime.now().isoformat(),
    }
    try:
        with open(SNIPER_KILL_STATE_FILE, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        print(f"Warning: could not save {SNIPER_KILL_STATE_FILE}: {e}")


def _load_kill_state() -> None:
    global _kill_consecutive_errors, _kill_session_start_pnl, _kill_soft_until, _kill_soft_reason
    global _kill_hard_latched, _kill_hard_reason
    if SNIPER_KILL_CLEAR_ON_START:
        _kill_hard_latched = False
        _kill_hard_reason = ""
        _kill_soft_reason = ""
        _kill_soft_until = 0.0
        _kill_consecutive_errors = 0
        return
    if not os.path.isfile(SNIPER_KILL_STATE_FILE):
        return
    try:
        with open(SNIPER_KILL_STATE_FILE, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
        _kill_hard_latched = bool(data.get("hard_latched"))
        _kill_hard_reason = str(data.get("hard_reason") or "")
        _kill_soft_reason = str(data.get("soft_reason") or "")
        _kill_soft_until = float(data.get("soft_until_monotonic") or 0.0)
        if data.get("session_start_pnl") is not None:
            _kill_session_start_pnl = float(data["session_start_pnl"])
        _kill_consecutive_errors = int(data.get("consecutive_errors") or 0)
    except Exception as e:
        print(f"Warning: could not load {SNIPER_KILL_STATE_FILE}: {e}")


def _halt_file_active() -> bool:
    return bool(SNIPER_HALT_FILE) and os.path.isfile(SNIPER_HALT_FILE)


def _kill_switch_configured() -> bool:
    if not SNIPER_KILL_SWITCH:
        return False
    return any(
        (
            SNIPER_KILL_MAX_LOSS_USD > 0,
            SNIPER_KILL_MAX_SESSION_LOSS_USD > 0,
            SNIPER_KILL_MAX_CONSECUTIVE_ERRORS > 0,
            bool(SNIPER_HALT_FILE),
        )
    )


def _loss_kill_trip() -> Optional[Tuple[str, str]]:
    if not SNIPER_KILL_SWITCH:
        return None
    if SNIPER_KILL_MAX_LOSS_USD <= 0 and SNIPER_KILL_MAX_SESSION_LOSS_USD <= 0:
        return None
    realized, unrealized, total = _portfolio_pnl()
    _ensure_kill_session_baseline()
    session_delta = total - float(_kill_session_start_pnl or 0.0)
    if SNIPER_KILL_MAX_LOSS_USD > 0 and total <= -SNIPER_KILL_MAX_LOSS_USD:
        return (
            (
                f"total PnL ${total:+,.2f} (realized ${realized:+,.2f}, "
                f"unrealized ${unrealized:+,.2f}) breached SNIPER_KILL_MAX_LOSS_USD="
                f"{SNIPER_KILL_MAX_LOSS_USD:,.2f}"
            ),
            _normalize_kill_action(SNIPER_KILL_LOSS_ACTION),
        )
    if SNIPER_KILL_MAX_SESSION_LOSS_USD > 0 and session_delta <= -SNIPER_KILL_MAX_SESSION_LOSS_USD:
        return (
            (
                f"session PnL ${session_delta:+,.2f} breached "
                f"SNIPER_KILL_MAX_SESSION_LOSS_USD={SNIPER_KILL_MAX_SESSION_LOSS_USD:,.2f}"
            ),
            _normalize_kill_action(SNIPER_KILL_LOSS_ACTION),
        )
    return None


def _error_kill_trip() -> Optional[Tuple[str, str]]:
    if not SNIPER_KILL_SWITCH or SNIPER_KILL_MAX_CONSECUTIVE_ERRORS <= 0:
        return None
    if _kill_consecutive_errors < SNIPER_KILL_MAX_CONSECUTIVE_ERRORS:
        return None
    return (
        (
            f"{_kill_consecutive_errors} consecutive scan errors "
            f"(limit {SNIPER_KILL_MAX_CONSECUTIVE_ERRORS})"
        ),
        _normalize_kill_action(SNIPER_KILL_ERROR_ACTION),
    )


def _activate_kill_switch(reason: str, action: str) -> None:
    global _kill_soft_until, _kill_soft_reason, _kill_hard_latched, _kill_hard_reason
    action = _normalize_kill_action(action)
    print(f"\n🛑 KILL SWITCH ({action}): {reason}")
    if action == "hard":
        _kill_hard_latched = True
        _kill_hard_reason = reason
        _save_kill_state()
        raise KillSwitchHardExit(reason)
    _kill_soft_reason = reason
    _kill_soft_until = time.monotonic() + SNIPER_KILL_SOFT_SLEEP_SECONDS
    _save_kill_state()


def _clear_soft_kill_if_recovered() -> None:
    global _kill_soft_until, _kill_soft_reason, _kill_consecutive_errors
    if not _kill_soft_reason:
        return
    if _loss_kill_trip() is not None:
        return
    if _kill_consecutive_errors > 0:
        return
    if time.monotonic() < _kill_soft_until:
        return
    print("\n✅ Kill switch soft pause cleared — resuming new entries.")
    _kill_soft_reason = ""
    _kill_soft_until = 0.0
    _save_kill_state()


def _kill_blocks_new_entries() -> bool:
    if not SNIPER_KILL_SWITCH:
        return False
    if _halt_file_active():
        return True
    if _kill_hard_latched:
        return True
    if _kill_soft_reason and time.monotonic() < _kill_soft_until:
        return True
    return False


def _kill_pause_message() -> str:
    if _halt_file_active():
        mode = _normalize_kill_action(SNIPER_HALT_FILE_MODE)
        return f"halt file active ({SNIPER_HALT_FILE}, mode={mode})"
    if _kill_soft_reason and time.monotonic() < _kill_soft_until:
        remaining = max(0, int(_kill_soft_until - time.monotonic()))
        return f"{_kill_soft_reason} (soft pause ~{remaining}s remaining)"
    if _kill_hard_latched:
        return _kill_hard_reason or "hard kill latched"
    return "kill switch active"


def _record_scan_success() -> None:
    global _kill_consecutive_errors
    _kill_consecutive_errors = 0
    _clear_soft_kill_if_recovered()
    _save_kill_state()


def _record_scan_error(exc: BaseException) -> None:
    global _kill_consecutive_errors
    _kill_consecutive_errors += 1
    print(
        f"\n⚠️  Scan error ({_kill_consecutive_errors}/"
        f"{SNIPER_KILL_MAX_CONSECUTIVE_ERRORS or '∞'}): {exc}"
    )
    trip = _error_kill_trip()
    if trip:
        _activate_kill_switch(trip[0], trip[1])
    _save_kill_state()


def _enforce_kill_switch_before_cycle() -> None:
    if _halt_file_active() and _normalize_kill_action(SNIPER_HALT_FILE_MODE) == "hard":
        _activate_kill_switch(f"halt file present: {SNIPER_HALT_FILE}", "hard")
    if _kill_hard_latched:
        raise KillSwitchHardExit(_kill_hard_reason or "hard kill latched from prior run")
    trip = _loss_kill_trip()
    if trip:
        _activate_kill_switch(trip[0], trip[1])
    trip = _error_kill_trip()
    if trip:
        _activate_kill_switch(trip[0], trip[1])


def _print_kill_switch_banner() -> None:
    if not _kill_switch_configured():
        return
    print("\nKill switch:")
    print(f"  SNIPER_KILL_SWITCH={SNIPER_KILL_SWITCH}")
    if SNIPER_KILL_MAX_LOSS_USD > 0:
        print(
            f"  Max total loss: ${SNIPER_KILL_MAX_LOSS_USD:,.2f} "
            f"(action={_normalize_kill_action(SNIPER_KILL_LOSS_ACTION)})"
        )
    if SNIPER_KILL_MAX_SESSION_LOSS_USD > 0:
        print(
            f"  Max session loss: ${SNIPER_KILL_MAX_SESSION_LOSS_USD:,.2f} "
            f"(action={_normalize_kill_action(SNIPER_KILL_LOSS_ACTION)})"
        )
    if SNIPER_KILL_MAX_CONSECUTIVE_ERRORS > 0:
        print(
            f"  Max consecutive scan errors: {SNIPER_KILL_MAX_CONSECUTIVE_ERRORS} "
            f"(action={_normalize_kill_action(SNIPER_KILL_ERROR_ACTION)})"
        )
    if SNIPER_HALT_FILE:
        print(
            f"  Halt file: {SNIPER_HALT_FILE} "
            f"(mode={_normalize_kill_action(SNIPER_HALT_FILE_MODE)})"
        )
    print(f"  Soft pause: {SNIPER_KILL_SOFT_SLEEP_SECONDS}s")
    if SNIPER_KILL_STATE_FILE:
        print(f"  Kill state file: {SNIPER_KILL_STATE_FILE}")
    if _kill_hard_latched:
        print(f"  ⚠️  Hard kill latched: {_kill_hard_reason}")


def _json_list_field(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            pass
    return []


def _gamma_volume_24h(row: dict) -> float:
    for key in ("volume24hrClob", "volume24hr", "volume24Hr"):
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _gamma_liquidity(row: dict) -> float:
    for key in ("liquidityClob", "liquidityNum", "liquidity"):
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def normalize_gamma_market(row: dict) -> dict:
    condition_id = str(row.get("conditionId") or row.get("condition_id") or "")
    outcomes = _json_list_field(row.get("outcomes"))
    token_ids = _json_list_field(row.get("clobTokenIds"))
    prices = _json_list_field(row.get("outcomePrices"))
    tokens: List[dict] = []
    for idx, token_id in enumerate(token_ids):
        outcome = outcomes[idx] if idx < len(outcomes) else f"Outcome {idx + 1}"
        token: Dict[str, Any] = {
            "token_id": token_id,
            "tokenId": token_id,
            "outcome": outcome,
        }
        if idx < len(prices):
            try:
                token["price"] = float(prices[idx])
            except (TypeError, ValueError):
                pass
        tokens.append(token)
    return {
        "question": row.get("question") or row.get("title") or "N/A",
        "description": row.get("description") or "",
        "slug": row.get("slug") or row.get("market_slug") or "",
        "groupItemTitle": row.get("groupItemTitle"),
        "condition_id": condition_id,
        "conditionId": condition_id,
        "end_date_iso": row.get("endDate") or row.get("endDateIso") or row.get("end_date_iso"),
        "accepting_orders": row.get("acceptingOrders", row.get("accepting_orders", True)),
        "neg_risk": row.get("negRisk", row.get("neg_risk")),
        "tokens": tokens,
        "gamma_volume_24h": _gamma_volume_24h(row),
        "gamma_liquidity": _gamma_liquidity(row),
        "enable_order_book": row.get("enableOrderBook", row.get("enable_order_book", True)),
    }


def fetch_sampling_markets() -> List[dict]:
    """Active CLOB markets (paginated sample); same family of data as copy_bot / wallet tests."""
    collected: List[dict] = []
    cursor: Optional[str] = None
    while len(collected) < CLOB_SAMPLING_POOL_SIZE:
        page_size = min(1000, CLOB_SAMPLING_POOL_SIZE - len(collected))
        params: Dict[str, Any] = {"closed": "false", "limit": page_size}
        if cursor:
            params["next_cursor"] = cursor
        try:
            r = _get(f"{API_BASE}/sampling-markets", params=params)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            print(f"Error fetching sampling markets: {e}")
            break
        if isinstance(data, list):
            rows = data
            cursor = None
        else:
            rows = data.get("data") or data.get("markets") or []
            next_cursor = data.get("next_cursor")
            cursor = str(next_cursor) if next_cursor else None
        if not rows:
            break
        collected.extend(rows)
        if len(collected) >= CLOB_SAMPLING_POOL_SIZE:
            break
        if not cursor:
            break
    return collected[:CLOB_SAMPLING_POOL_SIZE]


def fetch_gamma_liquid_markets() -> List[dict]:
    """Top Gamma markets by CLOB 24h volume, pre-filtered for pool depth."""
    collected: List[dict] = []
    offset = 0
    while len(collected) < SNIPER_MARKET_POOL_SIZE:
        page_size = min(GAMMA_MARKET_PAGE_LIMIT, SNIPER_MARKET_POOL_SIZE - len(collected))
        try:
            r = _get(
                f"{GAMMA_API}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": page_size,
                    "offset": offset,
                    "order": "volume24hrClob",
                    "ascending": "false",
                },
            )
            r.raise_for_status()
            data = r.json()
            rows = data if isinstance(data, list) else data.get("data") or []
        except requests.RequestException as e:
            print(f"Error fetching Gamma liquid markets: {e}")
            break
        if not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            if not row.get("enableOrderBook", row.get("enable_order_book", True)):
                continue
            if not row.get("acceptingOrders", row.get("accepting_orders", True)):
                continue
            if _gamma_volume_24h(row) < MIN_24H_VOLUME:
                continue
            if _gamma_liquidity(row) < MIN_GAMMA_LIQUIDITY:
                continue
            collected.append(normalize_gamma_market(row))
            if len(collected) >= SNIPER_MARKET_POOL_SIZE:
                break
        offset += len(rows)
        if len(rows) < page_size:
            break
    return collected


def fetch_scan_markets() -> List[dict]:
    """Merged market universe: Gamma liquid pool + CLOB sampling (deduped by condition id)."""
    source = SNIPER_MARKET_SOURCE
    if source == "gamma":
        return fetch_gamma_liquid_markets()
    if source == "sampling":
        return fetch_sampling_markets()
    if source != "both":
        print(f"Unknown SNIPER_MARKET_SOURCE={SNIPER_MARKET_SOURCE!r}; using gamma + sampling.")
    merged: Dict[str, dict] = {}
    for market in fetch_gamma_liquid_markets() + fetch_sampling_markets():
        condition_id = str(market.get("condition_id") or market.get("conditionId") or "").lower()
        key = condition_id or str(market.get("question") or market.get("id") or id(market))
        merged[key] = market
    return list(merged.values())


def _market_end_datetime(market: dict) -> Optional[datetime]:
    end = market.get("end_date_iso") or market.get("endDate") or market.get("end_date")
    if not end:
        return None
    try:
        if isinstance(end, str):
            return datetime.fromisoformat(end.replace("Z", "+00:00"))
        return datetime.fromtimestamp(end)
    except Exception:
        return None


def hours_until_resolution(market: dict) -> Optional[float]:
    end_time = _market_end_datetime(market)
    if not end_time:
        return None
    try:
        now = datetime.now(end_time.tzinfo) if end_time.tzinfo else datetime.now(tz=None)
        if end_time.tzinfo is None:
            now = datetime.now()
        delta = end_time - now
        secs = delta.total_seconds()
        if secs <= 0:
            return None
        return secs / 3600.0
    except Exception:
        return None


def hours_until_resolution_iso(resolution_end: Optional[str]) -> Optional[float]:
    if not resolution_end:
        return None
    try:
        end_time = datetime.fromisoformat(str(resolution_end).replace("Z", "+00:00"))
        now = datetime.now(end_time.tzinfo) if end_time.tzinfo else datetime.now()
        if end_time.tzinfo is None:
            now = datetime.now()
        secs = (end_time - now).total_seconds()
        if secs <= 0:
            return 0.0
        return secs / 3600.0
    except Exception:
        return None


def _position_resolution_end(position: dict) -> Optional[str]:
    end = position.get("resolution_end")
    if end:
        return str(end)
    return None


def _position_hours_to_resolution(position: dict) -> Optional[float]:
    return hours_until_resolution_iso(_position_resolution_end(position))


def format_resolution_window() -> str:
    if MAX_HOURS_TO_RESOLUTION > 0 and MIN_HOURS_TO_RESOLUTION > 0:
        return f"{MIN_HOURS_TO_RESOLUTION:.0f}–{MAX_HOURS_TO_RESOLUTION:.0f}h to resolution"
    if MAX_HOURS_TO_RESOLUTION > 0:
        return f"<{MAX_HOURS_TO_RESOLUTION:.0f}h to resolution"
    if MIN_HOURS_TO_RESOLUTION > 0:
        return f">={MIN_HOURS_TO_RESOLUTION:.0f}h to resolution"
    return "any time before resolution"


def is_close_to_resolution(market: dict) -> bool:
    hrs = hours_until_resolution(market)
    if hrs is None:
        return False
    if MAX_HOURS_TO_RESOLUTION > 0 and hrs >= MAX_HOURS_TO_RESOLUTION:
        return False
    if MIN_HOURS_TO_RESOLUTION > 0 and hrs < MIN_HOURS_TO_RESOLUTION:
        return False
    return True


def _fetch_order_book_payload(token_id: str) -> Optional[dict]:
    try:
        r = _get(f"{API_BASE}/book", params={"token_id": token_id})
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else None
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        print(f"Error fetching order book for {token_id}: {e}")
        return None
    except requests.RequestException as e:
        print(f"Error fetching order book for {token_id}: {e}")
        return None


def _best_bid_ask_from_book(data: dict) -> Tuple[Optional[float], Optional[float]]:
    bids = data.get("bids", [])
    asks = data.get("asks", [])
    if not bids or not asks:
        return None, None
    best_bid = max(float(b["price"]) for b in bids)
    best_ask = min(float(a["price"]) for a in asks)
    return best_bid, best_ask


def fetch_order_book(token_id: str) -> Tuple[Optional[float], Optional[float]]:
    data = _fetch_order_book_payload(token_id)
    if not data:
        return None, None
    return _best_bid_ask_from_book(data)


def check_liquidity(
    token_id: str,
    condition_id: Optional[str],
    volume_24h_hint: Optional[float] = None,
    book_data: Optional[dict] = None,
) -> Tuple[bool, Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
    """
    Returns (is_liquid, spread_pct, depth_usd, volume_24h, best_bid, best_ask).
    spread_pct is percent (0–100). Compare to MAX_SPREAD_PCT derived from MIN_BID_ASK_SPREAD.
    """
    try:
        data = book_data if book_data is not None else _fetch_order_book_payload(token_id)
        if not data:
            return False, None, None, None, None, None
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        if not bids or not asks:
            return False, None, None, None, None, None
        best_bid, best_ask = _best_bid_ask_from_book(data)
        if best_bid is None or best_ask is None:
            return False, None, None, None, None, None
        mid = (best_bid + best_ask) / 2
        spread_pct = ((best_ask - best_bid) / mid) * 100 if mid > 0 else 999.0
        n = ORDER_BOOK_TOP_LEVELS
        top_asks = sum(float(a.get("size", 0)) * float(a.get("price", 0)) for a in asks[:n])
        top_bids = sum(float(b.get("size", 0)) * float(b.get("price", 0)) for b in bids[:n])
        total_depth = top_asks + top_bids
        if volume_24h_hint is not None:
            volume_24h = float(volume_24h_hint)
        else:
            volume_24h = gamma_volume_24h(condition_id or "")
        is_liquid = (
            spread_pct <= MAX_SPREAD_PCT
            and total_depth >= MIN_ORDER_BOOK_DEPTH
            and volume_24h >= MIN_24H_VOLUME
        )
        return is_liquid, spread_pct, total_depth, volume_24h, best_bid, best_ask
    except Exception as e:
        print(f"Error checking liquidity for {token_id}: {e}")
        return False, None, None, None, None, None


def score_market_for_upside(
    market: dict,
    ask: float,
    spread_pct: float,
    volume_24h: float,
) -> int:
    """
    Upside 0..UPSIDE_SCORE_MAX from env-driven rules only (no literals in body).
    If REQUIRE_MARKET_MOMENTUM and the momentum rule does not fire, returns 0.
    """
    score = 0
    momentum_ok = False

    min_ask_line = PRICE_THRESHOLD * UPSIDE_MOMENTUM_MIN_ASK
    if ask >= min_ask_line and spread_pct <= UPSIDE_MOMENTUM_MAX_SPREAD_PCT:
        momentum_ok = True
        score += UPSIDE_POINTS_MOMENTUM

    vol_line = MIN_24H_VOLUME * UPSIDE_VOLUME_SPIKE_MULTIPLIER
    if volume_24h >= vol_line:
        score += UPSIDE_POINTS_VOLUME

    if UPSIDE_CATALYST_KEYWORDS:
        parts: List[str] = []
        q = market.get("question") or market.get("title")
        if q:
            parts.append(str(q).lower())
        for key in ("description", "groupItemTitle", "slug"):
            v = market.get(key)
            if v:
                parts.append(str(v).lower())
        hay = " ".join(parts)
        if any(k in hay for k in UPSIDE_CATALYST_KEYWORDS):
            score += UPSIDE_POINTS_CATALYST

    if REQUIRE_MARKET_MOMENTUM and not momentum_ok:
        return 0

    return max(0, min(UPSIDE_SCORE_MAX, score))


def should_buy(outcome_price: float) -> bool:
    return outcome_price >= PRICE_THRESHOLD


def estimated_entry_fill_price(reference_ask: float, *, for_drag_screen: bool = False) -> float:
    if for_drag_screen:
        slip = ENTRY_DRAG_SLIPPAGE
        return round(min(float(reference_ask) + slip, 0.99), 4)
    if DRY_RUN:
        return _paper_buy_fill_price(reference_ask)
    return round(min(float(reference_ask) + SLIPPAGE_TOLERANCE, 0.99), 4)


def entry_drag_pct(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None:
        return None
    try:
        bid_f = float(bid)
        ask_f = float(ask)
    except (TypeError, ValueError):
        return None
    if bid_f <= 0 or ask_f <= 0:
        return None
    fill = estimated_entry_fill_price(ask_f, for_drag_screen=True)
    return ((fill - bid_f) / bid_f) * 100.0


def passes_entry_execution_quality(
    bid: Optional[float], ask: Optional[float], *, log_skip: bool = False
) -> bool:
    if bid is None or ask is None:
        return False
    drag = entry_drag_pct(bid, ask)
    if drag is None:
        return False
    if MAX_ENTRY_DRAG_PCT <= 0:
        return True
    ok = drag <= MAX_ENTRY_DRAG_PCT + 1e-9
    if not ok and log_skip:
        slog.warn(
            f"Skipping entry: est. fill drag {drag:.2f}% > max {MAX_ENTRY_DRAG_PCT:.2f}% "
            f"(bid {float(bid):.4f}, ask {float(ask):.4f})"
        )
    return ok


def profit_room_exit_cap() -> float:
    return max(0.0, MAX_OUTCOME_PRICE - PROFIT_ROOM_PRICE_BUFFER)


def effective_max_entry_ask() -> float:
    if MAX_ENTRY_ASK > 0:
        return MAX_ENTRY_ASK
    exit_cap = profit_room_exit_cap()
    if exit_cap <= 0:
        return 0.0
    return exit_cap / (1.0 + PROFIT_TARGET_PCT / 100.0)


def format_entry_ask_window() -> str:
    max_ask = effective_max_entry_ask()
    return (
        f"ask {PRICE_THRESHOLD:.2f}–{max_ask:.2f} "
        f"({PRICE_THRESHOLD * 100:.0f}%–{max_ask * 100:.0f}% implied)"
    )


def has_profit_room(entry_price: float) -> bool:
    if not REQUIRE_PROFIT_ROOM:
        return True
    if entry_price > effective_max_entry_ask() + 1e-9:
        return False
    exit_cap = profit_room_exit_cap()
    if exit_cap <= 0:
        return False
    return entry_price * (1.0 + PROFIT_TARGET_PCT / 100.0) <= exit_cap + 1e-9


def _position_portfolio_totals() -> Tuple[float, float, float]:
    unrealized = 0.0
    cost_basis = 0.0
    mtm = 0.0
    for position in positions.values():
        pnl, position_mtm, basis, _bid, _ask = _position_mark_pnl(position)
        unrealized += pnl
        mtm += position_mtm
        cost_basis += basis
    return unrealized, mtm, cost_basis


def _deployable_capital_usd() -> float:
    if DRY_RUN:
        return max(0.0, float(paper_cash_usd))
    if not _live_keys_ok():
        return 0.0
    try:
        from live_clob import live_trader_singleton

        return max(0.0, float(live_trader_singleton().get_collateral_balance_usdc()))
    except Exception as e:
        slog.warn(f"could not read collateral balance for sizing: {e}")
        return 0.0


def _compound_surplus_usd(slots_remaining: int) -> float:
    deployable = _deployable_capital_usd()
    if DRY_RUN:
        return max(0.0, deployable - PAPER_STARTING_BALANCE_USD)
    slots = max(1, int(slots_remaining))
    base_budget = float(POSITION_SIZE_USD) * slots
    return max(0.0, deployable - base_budget)


def calculate_position_stake_usd(slots_remaining: int) -> float:
    if POSITION_SIZE_USD <= 0:
        return 0.0
    if not COMPOUND_POSITIONS:
        return float(POSITION_SIZE_USD)
    deployable = _deployable_capital_usd()
    if deployable <= 0:
        return 0.0
    slots = max(1, int(slots_remaining))
    surplus = _compound_surplus_usd(slots)
    stake = float(POSITION_SIZE_USD) + surplus / slots
    stake = min(stake, deployable)
    stake = max(MIN_POSITION_SIZE_USD, stake)
    if MAX_POSITION_SIZE_USD > 0:
        stake = min(stake, MAX_POSITION_SIZE_USD)
    return round(stake, 2)


def calculate_position_size(entry_price: float, *, slots_remaining: int = 1) -> float:
    if entry_price == 0:
        return 0.0
    stake_usd = calculate_position_stake_usd(slots_remaining)
    if stake_usd <= 0:
        return 0.0
    return stake_usd / entry_price


def _paper_buy_fill_price(reference_ask: float) -> float:
    limit = min(float(reference_ask) + SLIPPAGE_TOLERANCE, 0.99)
    return round(max(limit, 0.01), 4)


def _paper_sell_fill_price(reference_bid: float) -> float:
    limit = max(float(reference_bid) - SLIPPAGE_TOLERANCE_SELL, 0.01)
    return round(min(limit, 0.99), 4)


def _position_entry_bid(position: dict) -> float:
    raw = position.get("entry_bid")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return float(position.get("price", 0) or 0)


def _seconds_since_entry(position: dict) -> Optional[float]:
    raw = position.get("entry_time")
    if not raw:
        return None
    try:
        entry_dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    now = datetime.now(entry_dt.tzinfo) if entry_dt.tzinfo else datetime.now()
    return max(0.0, (now - entry_dt).total_seconds())


def _stop_loss_allowed(position: dict) -> bool:
    if STOP_LOSS_GRACE_SECONDS <= 0:
        return True
    elapsed = _seconds_since_entry(position)
    if elapsed is None:
        return True
    return elapsed >= float(STOP_LOSS_GRACE_SECONDS)


def _stop_loss_bid_floor(position: dict) -> float:
    entry_bid = _position_entry_bid(position)
    return entry_bid * (1.0 - STOP_LOSS_PCT / 100.0)


def _live_keys_ok() -> bool:
    pk = (os.getenv("PRIVATE_KEY") or os.getenv("PROXY_PRIVATE_KEY") or "").strip()
    return bool(pk)


def _order_id_from_clob(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    for key in ("orderID", "orderId", "order_id", "id"):
        val = payload.get(key)
        if val:
            return str(val)
    order = payload.get("order")
    if isinstance(order, dict):
        for key in ("orderID", "orderId", "order_id", "id"):
            val = order.get(key)
            if val:
                return str(val)
    return None


def _normalize_clob_open_order(row: dict) -> Optional[dict]:
    if not isinstance(row, dict):
        return None
    order_id = _order_id_from_clob(row)
    if not order_id:
        return None
    token_id = str(row.get("asset_id") or row.get("token_id") or row.get("tokenId") or "")
    side = str(row.get("side") or "").upper()
    price_raw = row.get("price")
    if price_raw is None:
        price_raw = row.get("limit_price")
    size_raw = row.get("size")
    if size_raw is None:
        size_raw = row.get("original_size")
    if size_raw is None:
        size_raw = row.get("remaining_size")
    try:
        price = float(price_raw) if price_raw is not None else None
    except (TypeError, ValueError):
        price = None
    try:
        size = float(size_raw) if size_raw is not None else None
    except (TypeError, ValueError):
        size = None
    return {
        "order_id": order_id,
        "token_id": token_id,
        "side": side,
        "price": price,
        "size": size,
        "market_id": str(row.get("market") or row.get("condition_id") or row.get("conditionId") or ""),
        "status": str(row.get("status") or row.get("state") or "open"),
        "updated_at": datetime.now().isoformat(),
        "raw": row,
    }


def _record_open_order_from_response(resp: Any, *, token_id: str, side: str, condition_id: str = "") -> None:
    if DRY_RUN or not _live_keys_ok():
        return
    payload = resp if isinstance(resp, dict) else {}
    order_id = _order_id_from_clob(payload)
    if not order_id:
        return
    open_orders[order_id] = {
        "order_id": order_id,
        "token_id": token_id,
        "side": side.upper(),
        "price": payload.get("price"),
        "size": payload.get("size") or payload.get("original_size"),
        "market_id": condition_id,
        "status": str(payload.get("status") or payload.get("state") or "submitted"),
        "updated_at": datetime.now().isoformat(),
        "raw": payload,
    }
    save_state()


def sync_live_open_orders() -> None:
    if DRY_RUN or not _live_keys_ok():
        return
    from live_clob import live_trader_singleton

    try:
        rows = live_trader_singleton().fetch_open_orders()
    except Exception as e:
        print(f"Warning: could not refresh open orders: {e}")
        return
    refreshed: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        normalized = _normalize_clob_open_order(row)
        if not normalized:
            continue
        refreshed[normalized["order_id"]] = normalized
    open_orders.clear()
    open_orders.update(refreshed)
    save_state()


def _has_resting_sell_order(token_id: str) -> bool:
    for row in open_orders.values():
        if str(row.get("token_id") or "") != token_id:
            continue
        if str(row.get("side") or "").upper() == "SELL":
            return True
    return False


def _position_mark_pnl(position: dict) -> Tuple[float, float, float, Optional[float], Optional[float]]:
    qty = float(position.get("qty", 0) or 0)
    entry = float(position.get("price", 0) or 0)
    cost = float(position.get("cost_usd", entry * qty) or 0)
    if qty <= 0:
        return 0.0, 0.0, cost, None, None
    bid, ask = fetch_order_book(str(position.get("token_id") or ""))
    if bid is None or bid <= 0:
        return 0.0, cost, cost, bid, ask
    mtm = bid * qty
    return mtm - cost, mtm, cost, bid, ask


def _print_open_positions() -> None:
    if not positions:
        return
    slog.emit("  Open positions:")
    ordered = sorted(
        positions.values(),
        key=lambda pos: str(pos.get("market_question") or pos.get("outcome") or ""),
    )
    for pos in ordered:
        question = str(pos.get("market_question") or "Unknown market")
        outcome = str(pos.get("outcome") or "?")
        qty = float(pos.get("qty", 0) or 0)
        entry = float(pos.get("price", 0) or 0)
        target = float(pos.get("target_exit_price", 0) or 0)
        pnl, _mtm, _basis, bid, ask = _position_mark_pnl(pos)
        slog.open_position_row(
            question=question,
            outcome=outcome,
            qty=qty,
            entry=entry,
            bid=bid,
            ask=ask,
            unrealized_pnl=pnl,
            target=target,
            hours_to_resolution=_position_hours_to_resolution(pos),
        )

def _realized_pnl_usd() -> float:
    total = 0.0
    for row in trade_log:
        if not isinstance(row, dict) or row.get("event") != "sell":
            continue
        if row.get("pnl_usd") is not None:
            try:
                total += float(row["pnl_usd"])
                continue
            except (TypeError, ValueError):
                pass
        try:
            entry = float(row.get("entry_price"))
            bid = float(row.get("reference_bid"))
            qty = float(row.get("qty"))
        except (TypeError, ValueError):
            continue
        total += (bid - entry) * qty
    return total


def print_paper_account_summary() -> None:
    unrealized, mtm, cost_basis = _position_portfolio_totals()
    realized = _realized_pnl_usd()
    equity = float(paper_cash_usd) + mtm

    slog.account_summary_paper(
        cash_usd=float(paper_cash_usd),
        open_positions=len(positions),
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        mtm=mtm,
        cost_basis=cost_basis,
        equity=equity,
        starting_balance=PAPER_STARTING_BALANCE_USD,
    )
    if positions:
        _print_open_positions()


def print_live_account_summary() -> None:
    if not _live_keys_ok():
        return
    from live_clob import live_trader_singleton

    try:
        balance = live_trader_singleton().get_collateral_balance_usdc()
    except Exception as e:
        print(f"Warning: could not read wallet balance: {e}")
        balance = None

    unrealized, mtm, cost_basis = _position_portfolio_totals()
    realized = _realized_pnl_usd()

    slog.account_summary_live(
        balance=balance,
        open_positions=len(positions),
        resting_orders=len(open_orders),
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        mtm=mtm,
        cost_basis=cost_basis,
    )
    if positions:
        _print_open_positions()
    if open_orders:
        slog.emit("  Resting orders:")
        for row in sorted(open_orders.values(), key=lambda item: str(item.get("order_id") or "")):
            price = row.get("price")
            size = row.get("size")
            price_txt = f"{float(price):.4f}" if price is not None else "—"
            size_txt = f"{float(size):.2f}" if size is not None else "—"
            token = str(row.get("token_id") or "")[:12]
            slog.resting_order_row(
                side=str(row.get("side", "?")),
                size_txt=size_txt,
                price_txt=price_txt,
                token=token,
                order_id=str(row.get("order_id") or "")[:18],
            )


def print_account_summary() -> None:
    if DRY_RUN:
        print_paper_account_summary()
        return
    if _live_keys_ok():
        sync_live_open_orders()
        print_live_account_summary()


def place_buy_order(
    token_id: str,
    market_id: str,
    outcome: str,
    price: float,
    qty: float,
    *,
    condition_id: Optional[str] = None,
    market_question: str = "",
    neg_risk_hint: Optional[bool] = None,
    resolution_end: Optional[str] = None,
    hours_to_resolution: Optional[float] = None,
    entry_bid: Optional[float] = None,
) -> Optional[dict]:
    """Open a position: live CLOB when DRY_RUN=false and keys set; paper portfolio in dry run."""
    if _kill_blocks_new_entries():
        print(f"\n⏸️  Kill switch — skipping BUY ({_kill_pause_message()})")
        return None
    pos_key = f"{market_id}_{token_id}"
    if POSITION_SIZE_USD <= 0:
        print(
            f"\n[DRY RUN] Would BUY {outcome} @ ${price:.4f} qty≈{qty:.2f} (POSITION_SIZE_USD=0)"
        )
        return None

    bid_now, ask_now = fetch_order_book(token_id)
    ref_ask = ask_now if ask_now is not None else price
    ref_bid = bid_now if bid_now is not None else entry_bid
    # Stricter at order time: full slippage estimate (blocks books that only looked OK on scan).
    if ref_bid is not None and ref_ask is not None and MAX_ENTRY_DRAG_PCT > 0:
        fill_est = estimated_entry_fill_price(ref_ask, for_drag_screen=False)
        order_drag = ((fill_est - float(ref_bid)) / float(ref_bid)) * 100.0
        if order_drag > MAX_ENTRY_DRAG_PCT + 1.0 + 1e-9:
            slog.warn(
                f"Skipping {outcome}: order-time fill drag {order_drag:.2f}% "
                f"> max {MAX_ENTRY_DRAG_PCT:.2f}% (bid {float(ref_bid):.4f}, ask {float(ref_ask):.4f})"
            )
            return None
    elif not passes_entry_execution_quality(ref_bid, ref_ask, log_skip=True):
        return None

    if DRY_RUN:
        global paper_cash_usd
        fill_price = _paper_buy_fill_price(price)
        bid_at_entry = ref_bid
        cost_usd = round(float(fill_price) * float(qty), 2)
        if cost_usd <= 0:
            return None
        if paper_cash_usd + 1e-9 < cost_usd:
            print(
                f"\n[DRY RUN] Insufficient paper cash for {outcome}: "
                f"need ${cost_usd:.2f}, have ${paper_cash_usd:.2f}"
            )
            return None
        slog.buy_action(
            dry_run=True,
            outcome=outcome,
            price=fill_price,
            qty=qty,
            notional=cost_usd,
            target_exit=fill_price * (1 + PROFIT_TARGET_PCT / 100),
            profit_target_pct=PROFIT_TARGET_PCT,
            hours_to_resolution=hours_to_resolution,
        )
        order = {
            "token_id": token_id,
            "market_id": market_id,
            "condition_id": condition_id or "",
            "outcome": outcome,
            "side": "buy",
            "price": fill_price,
            "entry_bid": bid_at_entry,
            "qty": qty,
            "cost_usd": cost_usd,
            "entry_time": datetime.now().isoformat(),
            "target_exit_price": fill_price * (1 + PROFIT_TARGET_PCT / 100),
            "market_question": market_question,
            "neg_risk_hint": neg_risk_hint,
            "resolution_end": resolution_end or "",
            "paper": True,
        }
        paper_cash_usd = round(float(paper_cash_usd) - cost_usd, 2)
        positions[pos_key] = order
        trade_log.append({"event": "buy", **order})
        save_state()
        return order

    if not _live_keys_ok():
        print("LIVE: missing PRIVATE_KEY — set copy_bot env or DRY_RUN=true")
        return None

    from live_clob import live_trader_singleton, safe_json

    trader = live_trader_singleton()
    out: dict = {}
    try:
        resp = trader.place_buy_fak(
            token_id,
            qty,
            price,
            condition_id,
            SLIPPAGE_TOLERANCE,
        )
        out = safe_json(resp) if resp is not None else {}
        _record_open_order_from_response(out, token_id=token_id, side="BUY", condition_id=condition_id or "")
    except Exception as e:
        slog.error(f"LIVE BUY failed: {e}")
        traceback.print_exc()
        return None

    order = {
        "token_id": token_id,
        "market_id": market_id,
        "condition_id": condition_id or "",
        "outcome": outcome,
        "side": "buy",
        "price": price,
        "entry_bid": ref_bid,
        "qty": qty,
        "cost_usd": price * qty,
        "entry_time": datetime.now().isoformat(),
        "target_exit_price": price * (1 + PROFIT_TARGET_PCT / 100),
        "market_question": market_question,
        "neg_risk_hint": neg_risk_hint,
        "resolution_end": resolution_end or "",
    }
    positions[pos_key] = order
    trade_log.append({"event": "buy", **order, "clob_response": out})
    save_state()
    slog.buy_action(
        dry_run=False,
        outcome=outcome,
        price=price,
        qty=qty,
        notional=price * qty,
        target_exit=order["target_exit_price"],
        profit_target_pct=PROFIT_TARGET_PCT,
        hours_to_resolution=hours_to_resolution,
    )
    return order


def _sell_position_live(pos_key: str, position: dict, reason: str) -> bool:
    if not _live_keys_ok():
        print("LIVE SELL: missing PRIVATE_KEY — cannot submit")
        return False
    from live_clob import live_trader_singleton, safe_json

    token_id = position["token_id"]
    bid, _ = fetch_order_book(token_id)
    if bid is None or bid <= 0:
        print(f"LIVE SELL skip: no bid for {token_id[:16]}...")
        return False
    qty = float(position.get("qty", 0))
    entry = float(position.get("price", 0))
    try:
        trader = live_trader_singleton()
        time.sleep(SELL_ALLOWANCE_DELAY_SEC)
        trader.refresh_conditional_allowance(token_id)
        resp = trader.place_sell_fak(
            token_id,
            qty,
            bid,
            (position.get("condition_id") or "").strip() or None,
            SLIPPAGE_TOLERANCE_SELL,
        )
        out = safe_json(resp)
        print(f"LIVE SELL ({reason}): {out}")
        _record_open_order_from_response(
            out,
            token_id=token_id,
            side="SELL",
            condition_id=(position.get("condition_id") or "").strip(),
        )
    except Exception as e:
        print(f"LIVE SELL failed ({reason}): {e}")
        traceback.print_exc()
        return False
    exit_row = {
        "event": "sell",
        "reason": reason,
        "token_id": token_id,
        "outcome": position.get("outcome"),
        "exit_time": datetime.now().isoformat(),
        "reference_bid": bid,
        "entry_price": entry,
        "qty": qty,
        "pnl_usd": (bid - entry) * qty,
    }
    trade_log.append(exit_row)
    save_state()
    return True


def _close_position_paper(pos_key: str, position: dict, reason: str, bid: float) -> bool:
    global paper_cash_usd
    time.sleep(SELL_ALLOWANCE_DELAY_SEC)
    qty = float(position.get("qty", 0))
    entry = float(position.get("price", 0))
    fill_bid = _paper_sell_fill_price(bid)
    proceeds = round(float(fill_bid) * qty, 2)
    paper_cash_usd = round(float(paper_cash_usd) + proceeds, 2)
    exit_row = {
        "event": "sell",
        "reason": reason,
        "token_id": position.get("token_id"),
        "outcome": position.get("outcome"),
        "exit_time": datetime.now().isoformat(),
        "reference_bid": bid,
        "fill_price": fill_bid,
        "entry_price": entry,
        "qty": qty,
        "pnl_usd": (fill_bid - entry) * qty,
        "paper": True,
    }
    trade_log.append(exit_row)
    del positions[pos_key]
    save_state()
    return True


def check_exit_conditions() -> List[str]:
    """Use best bid for exitability (sell into bid)."""
    exited: List[str] = []
    for pos_key, position in list(positions.items()):
        token_id = position["token_id"]
        target = float(position["target_exit_price"])
        entry = float(position["price"])
        bid, ask = fetch_order_book(token_id)
        if bid is None:
            continue

        if bid >= target:
            profit_pct = ((bid - entry) / entry) * 100
            profit_usd = (bid - entry) * float(position["qty"])
            slog.sell_action(
                dry_run=DRY_RUN,
                reason="profit_target",
                outcome=str(position.get("outcome", "")),
                bid=bid,
                pnl_usd=profit_usd,
                pnl_pct=profit_pct,
            )
            if DRY_RUN:
                if _close_position_paper(pos_key, position, "profit_target", bid):
                    exited.append(str(position.get("outcome", "")))
                continue
            if _has_resting_sell_order(token_id):
                print(f"   Resting SELL already on book for {token_id[:16]}... — skip duplicate exit")
                continue
            if _sell_position_live(pos_key, position, "profit_target"):
                del positions[pos_key]
                save_state()
                exited.append(str(position.get("outcome", "")))
        elif _stop_loss_allowed(position) and bid < _stop_loss_bid_floor(position):
            loss_pct = ((bid - entry) / entry) * 100
            loss_usd = (bid - entry) * float(position["qty"])
            slog.sell_action(
                dry_run=DRY_RUN,
                reason="stop_loss",
                outcome=str(position.get("outcome", "")),
                bid=bid,
                pnl_usd=loss_usd,
                pnl_pct=loss_pct,
            )
            if DRY_RUN:
                if _close_position_paper(pos_key, position, "stop_loss", bid):
                    exited.append(str(position.get("outcome", "")))
                continue
            if _has_resting_sell_order(token_id):
                print(f"   Resting SELL already on book for {token_id[:16]}... — skip duplicate exit")
                continue
            if _sell_position_live(pos_key, position, "stop_loss"):
                del positions[pos_key]
                save_state()
                exited.append(str(position.get("outcome", "")))
    return exited


def _print_opportunity_line(opp: dict, *, note: str = "") -> None:
    slog.opportunity_card(
        opp,
        note=note,
        upside_max=UPSIDE_SCORE_MAX,
        print_min_score=UPSIDE_PRINT_MIN_SCORE,
        profit_target_pct=PROFIT_TARGET_PCT,
    )


def scan_and_trade() -> None:
    global _scan_cycle
    _scan_cycle += 1
    cycle_started = time.monotonic()
    _enforce_kill_switch_before_cycle()
    print_account_summary()

    slog.scan_cycle_header(
        cycle=_scan_cycle,
        when=datetime.now(),
        active_positions=len(positions),
        max_positions=MAX_POSITIONS,
        dry_run=DRY_RUN,
        kill_message=_kill_pause_message() if _kill_blocks_new_entries() else "",
        resolution_window=format_resolution_window(),
    )

    check_exit_conditions()

    if _kill_blocks_new_entries():
        slog.emit("⏸  Kill switch active — managing exits only (no new entries).")
        slog.cycle_footer(
            duration_sec=time.monotonic() - cycle_started,
            sleep_sec=SNIPER_SLEEP_SECONDS,
            placed=0,
            candidates=0,
        )
        return

    if len(positions) >= MAX_POSITIONS:
        slog.warn(f"At max positions ({MAX_POSITIONS}). Skipping new entries.")
        slog.cycle_footer(
            duration_sec=time.monotonic() - cycle_started,
            sleep_sec=SNIPER_SLEEP_SECONDS,
            placed=0,
            candidates=0,
        )
        return

    markets = fetch_scan_markets()
    slog.market_pool_loaded(
        count=len(markets),
        source=SNIPER_MARKET_SOURCE,
        gamma_cap=SNIPER_MARKET_POOL_SIZE,
        clob_cap=CLOB_SAMPLING_POOL_SIZE,
    )
    slog.emit("Filtering by timing, liquidity, and upside rules…")

    opportunities: List[dict] = []
    skipped_profit_room = 0
    filter_stats = {
        "closed": 0,
        "no book": 0,
        "illiquid": 0,
        "below ask floor": 0,
        "above max ask": 0,
        "no TP room": 0,
        "entry drag": 0,
        "low score": 0,
    }
    for market in markets:
        if not market.get("accepting_orders", market.get("acceptingOrders", True)):
            continue
        if not market.get("enable_order_book", market.get("enableOrderBook", True)):
            continue
        if not is_close_to_resolution(market):
            filter_stats["closed"] += 1
            continue
        condition_id = market.get("condition_id") or market.get("conditionId") or ""
        vol_hint = market.get("gamma_volume_24h")
        if vol_hint is None and condition_id:
            vol_hint = gamma_volume_24h(condition_id)
        question = market.get("question", "N/A")
        neg_flag = market.get("neg_risk", market.get("negRisk"))
        tokens = market.get("tokens") or []
        for tok in tokens:
            if not isinstance(tok, dict):
                continue
            token_id = str(tok.get("token_id") or tok.get("tokenId") or "")
            if not token_id:
                continue
            outcome_title = str(tok.get("outcome", "Unknown"))
            price_hint = tok.get("price")
            if price_hint is not None:
                try:
                    if float(price_hint) < PRICE_THRESHOLD:
                        continue
                except (TypeError, ValueError):
                    pass
            book_data = _fetch_order_book_payload(token_id)
            if not book_data:
                filter_stats["no book"] += 1
                continue
            is_liquid, spread_pct, depth, volume, bid, ask = check_liquidity(
                token_id, condition_id or None, vol_hint, book_data
            )
            if ask is None:
                filter_stats["no book"] += 1
                continue
            if not is_liquid:
                filter_stats["illiquid"] += 1
                continue
            if not should_buy(ask):
                filter_stats["below ask floor"] += 1
                continue
            if MAX_ENTRY_ASK > 0 and ask > MAX_ENTRY_ASK + 1e-9:
                filter_stats["above max ask"] += 1
                continue
            if not has_profit_room(ask):
                skipped_profit_room += 1
                filter_stats["no TP room"] += 1
                continue
            if not passes_entry_execution_quality(bid, ask):
                filter_stats["entry drag"] += 1
                continue
            if spread_pct is None or volume is None:
                filter_stats["illiquid"] += 1
                continue
            market_score = score_market_for_upside(market, ask, spread_pct, float(volume))
            if market_score < MIN_MARKET_SCORE:
                filter_stats["low score"] += 1
                continue
            hrs_left = hours_until_resolution(market)
            end_dt = _market_end_datetime(market)
            opportunities.append(
                {
                    "market_id": condition_id or market.get("id", ""),
                    "condition_id": condition_id,
                    "market_question": question,
                    "token_id": token_id,
                    "outcome": outcome_title,
                    "ask_price": ask,
                    "bid": bid,
                    "entry_drag_pct": entry_drag_pct(bid, ask),
                    "spread_pct": spread_pct,
                    "depth": depth,
                    "volume_24h": volume,
                    "hours_to_resolution": hrs_left,
                    "resolution_end": end_dt.isoformat() if end_dt else None,
                    "neg_risk_hint": bool(neg_flag) if neg_flag is not None else None,
                    "market_score": market_score,
                }
            )

    seen_tokens: set = set()
    deduped: List[dict] = []
    for o in sorted(
        opportunities,
        key=lambda x: (-x["market_score"], x.get("entry_drag_pct") or 999.0, x["ask_price"]),
    ):
        tid = o["token_id"]
        if tid in seen_tokens:
            continue
        seen_tokens.add(tid)
        deduped.append(o)
    opportunities = deduped
    window_hrs = format_resolution_window()
    slog.scan_filter_funnel(filter_stats)
    slog.candidates_summary(
        count=len(opportunities),
        filters_text=(
            f"{format_entry_ask_window()}, +{PROFIT_TARGET_PCT}% TP room, {window_hrs}, "
            f"spread≤{MAX_SPREAD_PCT:.2f}%, drag≤{MAX_ENTRY_DRAG_PCT:.1f}%, "
            f"vol≥${MIN_24H_VOLUME:.0f}, depth≥${MIN_ORDER_BOOK_DEPTH:.0f}"
        ),
        skipped_profit_room=skipped_profit_room,
        profit_target_pct=PROFIT_TARGET_PCT,
        exit_cap=profit_room_exit_cap(),
    )

    for opp in opportunities:
        pos_key = f"{opp['market_id']}_{opp['token_id']}"
        note = "already held" if pos_key in positions else ""
        _print_opportunity_line(opp, note=note)

    slots = MAX_POSITIONS - len(positions)
    placed = 0
    for opp in opportunities:
        if placed >= slots:
            break
        pos_key = f"{opp['market_id']}_{opp['token_id']}"
        if pos_key in positions:
            continue
        slots_remaining = max(1, MAX_POSITIONS - len(positions))
        qty = calculate_position_size(opp["ask_price"], slots_remaining=slots_remaining)
        place_buy_order(
            opp["token_id"],
            opp["market_id"],
            opp["outcome"],
            opp["ask_price"],
            qty,
            condition_id=opp.get("condition_id") or "",
            market_question=opp["market_question"],
            neg_risk_hint=opp.get("neg_risk_hint"),
            resolution_end=opp.get("resolution_end"),
            hours_to_resolution=opp.get("hours_to_resolution"),
            entry_bid=opp.get("bid"),
        )
        placed += 1

    if not opportunities:
        slog.emit("  No candidates matched filters this cycle.")

    try:
        from dashboard_snapshot import write_scan_snapshot

        write_scan_snapshot(
            markets,
            opportunities,
            scan_cycle=_scan_cycle,
            skipped_profit_room=skipped_profit_room,
            path=DASHBOARD_SNAPSHOT_FILE,
        )
    except Exception as exc:
        slog.warn(f"could not write {DASHBOARD_SNAPSHOT_FILE}: {exc}")

    slog.cycle_footer(
        duration_sec=time.monotonic() - cycle_started,
        sleep_sec=SNIPER_SLEEP_SECONDS,
        placed=placed,
        candidates=len(opportunities),
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    load_state()
    if DRY_RUN and DRY_RUN_RESET_ON_START:
        reset_dry_run_state()
    _load_kill_state()
    _ensure_kill_session_baseline()
    if not DRY_RUN and POSITION_SIZE_USD > 0 and not _live_keys_ok():
        slog.error(
            "DRY_RUN=false and POSITION_SIZE_USD>0 but no PRIVATE_KEY/PROXY_PRIVATE_KEY in environment."
        )
        slog.error("Load copy_bot .env via COPY_BOT_ENV_PATH or default ../polymarket_copy_bot_v2/.env")
        sys.exit(1)
    if _kill_hard_latched and not SNIPER_KILL_CLEAR_ON_START:
        slog.error(f"hard kill switch latched — {_kill_hard_reason or 'prior run halted trading'}")
        slog.error(f"Clear {SNIPER_KILL_STATE_FILE} or set SNIPER_KILL_CLEAR_ON_START=true to resume.")
        sys.exit(1)
    print_account_summary()

    slog.startup_banner()
    if COMPOUND_POSITIONS:
        sizing_line = (
            f"Position sizing: ${POSITION_SIZE_USD:,.2f} base + surplus cash / open slots "
            f"(min ${MIN_POSITION_SIZE_USD:,.2f}"
        )
        if MAX_POSITION_SIZE_USD > 0:
            sizing_line += f", max ${MAX_POSITION_SIZE_USD:,.2f}"
        sizing_line += ")"
    else:
        sizing_line = f"Position sizing: fixed ${POSITION_SIZE_USD:,.2f}"
    config_lines = [
        f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}  ·  {sizing_line}",
        (
            f"Entry ≥ {PRICE_THRESHOLD:.2f}  ·  TP +{PROFIT_TARGET_PCT}%  ·  "
            f"SL -{STOP_LOSS_PCT}% from entry bid"
            + (f" (grace {STOP_LOSS_GRACE_SECONDS}s)" if STOP_LOSS_GRACE_SECONDS > 0 else "")
            + (f"  ·  max entry drag {MAX_ENTRY_DRAG_PCT:.1f}%" if MAX_ENTRY_DRAG_PCT > 0 else "")
        ),
    ]
    if REQUIRE_PROFIT_ROOM:
        exit_cap = profit_room_exit_cap()
        cap_note = (
            f"TP exit must stay ≤{exit_cap:.4f}"
            if PROFIT_ROOM_PRICE_BUFFER > 0
            else f"TP exit must stay ≤{exit_cap:.4f} (outcome cap)"
        )
        if MAX_ENTRY_ASK > 0:
            cap_note = f"hard MAX_ENTRY_ASK; {cap_note}"
        config_lines.append(
            f"Max entry ask {effective_max_entry_ask():.4f} ({cap_note}, +{PROFIT_TARGET_PCT}% TP)"
        )
    config_lines.extend(
        [
            f"Resolution window: {format_resolution_window()}",
            f"Max spread {MAX_SPREAD_PCT:.2f}%  ·  scan sleep {SNIPER_SLEEP_SECONDS}s",
            f"Positions file: {POSITIONS_FILE}",
            f"Open orders file: {OPEN_ORDERS_FILE}",
        ]
    )
    if DRY_RUN:
        config_lines.append(f"Paper account file: {PAPER_ACCOUNT_FILE}")
        config_lines.append(f"Paper starting balance: ${PAPER_STARTING_BALANCE_USD:,.2f}")
    config_lines.append(
        f"Market pool: {SNIPER_MARKET_SOURCE} "
        f"(gamma top≤{SNIPER_MARKET_POOL_SIZE}, CLOB≤{CLOB_SAMPLING_POOL_SIZE}, deduped; "
        f"gamma liq≥${MIN_GAMMA_LIQUIDITY:.0f}, vol≥${MIN_24H_VOLUME:.0f}, depth≥${MIN_ORDER_BOOK_DEPTH:.0f})"
    )
    if MIN_MARKET_SCORE > 0:
        config_lines.append(
            f"Upside score ≥ {MIN_MARKET_SCORE}/{UPSIDE_SCORE_MAX}  ·  "
            f"REQUIRE_MARKET_MOMENTUM={REQUIRE_MARKET_MOMENTUM}"
        )
    slog.config_block(config_lines)
    _print_kill_switch_banner()
    slog.emit()
    slog.emit("Ctrl+C to stop.")
    slog.emit()

    try:
        while True:
            try:
                scan_and_trade()
                _record_scan_success()
            except KillSwitchHardExit as exc:
                print(f"\n🛑 Hard kill switch — shutting down: {exc.reason}")
                save_state()
                sys.exit(2)
            except Exception as exc:
                try:
                    _record_scan_error(exc)
                except KillSwitchHardExit as hard_exc:
                    print(f"\n🛑 Hard kill switch — shutting down: {hard_exc.reason}")
                    save_state()
                    sys.exit(2)
                traceback.print_exc()
            sleep_seconds = SNIPER_SLEEP_SECONDS
            if _kill_soft_reason and time.monotonic() < _kill_soft_until:
                sleep_seconds = max(sleep_seconds, SNIPER_KILL_SOFT_SLEEP_SECONDS)
            slog.sleep_notice(sleep_seconds)
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        save_state()
        slog.stopped(
            positions=len(positions),
            orders=len(open_orders),
            positions_file=POSITIONS_FILE,
            orders_file=OPEN_ORDERS_FILE,
        )


if __name__ == "__main__":
    main()
