"""Terminal output helpers for the sniper bot (ANSI when stdout is a TTY)."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

_WIDTH = max(60, int(os.getenv("SNIPER_LOG_WIDTH", "72") or "72"))
_USE_COLOR = os.getenv("SNIPER_LOG_COLOR", "auto").strip().lower()


def _color_enabled() -> bool:
    if _USE_COLOR in {"0", "false", "no", "off"}:
        return False
    if _USE_COLOR in {"1", "true", "yes", "on"}:
        return True
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _c(text: str, code: str) -> str:
    if not _color_enabled():
        return text
    return f"\033[{code}m{text}\033[0m"


def _dim(text: str) -> str:
    return _c(text, "2")


def _bold(text: str) -> str:
    return _c(text, "1")


def _cyan(text: str) -> str:
    return _c(text, "36")


def _green(text: str) -> str:
    return _c(text, "32")


def _yellow(text: str) -> str:
    return _c(text, "33")


def _red(text: str) -> str:
    return _c(text, "31")


def _magenta(text: str) -> str:
    return _c(text, "35")


def _blue(text: str) -> str:
    return _c(text, "34")


def _pnl_text(amount: float, *, prefix: str = "$") -> str:
    body = f"{prefix}{amount:+,.2f}" if prefix else f"{amount:+.2f}"
    if amount > 0:
        return _green(body)
    if amount < 0:
        return _red(body)
    return _dim(body)


def _pct_text(value: float, *, signed: bool = True) -> str:
    body = f"{value:+.2f}%" if signed else f"{value:.2f}%"
    if value > 0:
        return _green(body)
    if value < 0:
        return _red(body)
    return _dim(body)


def _line(char: str = "─") -> str:
    return _dim(char * _WIDTH)


def _hours_remaining(hours: Optional[float]) -> str:
    if hours is None:
        return "—"
    if hours <= 0:
        return _red("expired")
    if hours < 1:
        return f"{hours * 60:.0f}m left"
    if hours < 48:
        return f"{hours:.1f}h left"
    return f"{hours:.1f}h left ({hours / 24.0:.1f}d)"


def _hours_remaining_colored(hours: Optional[float]) -> str:
    if hours is None:
        return _dim("—")
    if hours <= 0:
        return _red("expired")
    if hours < 6:
        return _yellow(_hours_remaining(hours))
    return _hours_remaining(hours)


def _clip(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: max(0, width - 1)] + "…"


def emit(message: str = "") -> None:
    print(message, flush=True)


def warn(message: str) -> None:
    emit(f"{_yellow('WARN')}  {message}")


def error(message: str) -> None:
    emit(f"{_red('ERROR')}  {message}")


def startup_banner() -> None:
    emit()
    emit(_cyan("╔" + "═" * (_WIDTH - 2) + "╗"))
    emit(_cyan("║") + _bold("  POLYMARKET LATE-STAGE SNIPER BOT".ljust(_WIDTH - 2)) + _cyan("║"))
    emit(_cyan("║") + _dim("  Liquidity-filtered entries".ljust(_WIDTH - 2)) + _cyan("║"))
    emit(_cyan("╚" + "═" * (_WIDTH - 2) + "╝"))


def config_block(lines: Sequence[str]) -> None:
    emit()
    for line in lines:
        emit(f"  {_dim('│')} {line}")


def section(title: str, *, subtitle: str = "") -> None:
    emit()
    emit(_line())
    header = _bold(title)
    if subtitle:
        header = f"{header}  {_dim(subtitle)}"
    emit(header)
    emit(_line())


def scan_cycle_header(
    *,
    cycle: int,
    when: datetime,
    active_positions: int,
    max_positions: int,
    dry_run: bool,
    kill_message: str = "",
    resolution_window: str = "",
) -> None:
    mode = _yellow("DRY RUN") if dry_run else _red("LIVE")
    slots = f"{active_positions}/{max_positions}"
    slot_color = _green if active_positions < max_positions else _yellow
    emit()
    emit(_line("═"))
    emit(
        f"{_bold('SCAN')} #{cycle}  "
        f"{_dim(when.strftime('%Y-%m-%d %H:%M:%S'))}  "
        f"{_dim('slots')} {slot_color(slots)}  "
        f"{mode}"
    )
    if kill_message:
        emit(f"{_yellow('KILL')}  {_kill_message(kill_message)}")
    if resolution_window:
        emit(f"{_dim('Trade window')}  {resolution_window}")
    emit(_line("═"))


def _kill_message(message: str) -> str:
    return _yellow(message)


def account_summary_paper(
    *,
    cash_usd: float,
    open_positions: int,
    realized_pnl: float,
    unrealized_pnl: float,
    mtm: float,
    cost_basis: float,
    equity: float,
    starting_balance: float,
) -> None:
    total_pnl = realized_pnl + unrealized_pnl
    session_return = 0.0
    if starting_balance > 0:
        session_return = ((equity - starting_balance) / starting_balance) * 100.0

    section("DRY RUN ACCOUNT", subtitle=f"{open_positions} open")
    emit(f"  {_dim('Cash')}        {_bold(f'${cash_usd:,.2f}')}")
    emit(
        f"  {_dim('Equity')}      {_bold(f'${equity:,.2f}')}  "
        f"{_dim('MTM')} ${_dim(f'{mtm:,.2f}')}  "
        f"{_dim('basis')} ${_dim(f'{cost_basis:,.2f}')}"
    )
    emit(
        f"  {_dim('P&L')}         "
        f"realized {_pnl_text(realized_pnl)}  "
        f"unrealized {_pnl_text(unrealized_pnl)}  "
        f"total {_pnl_text(total_pnl)}  "
        f"session {_pct_text(session_return)}"
    )


def account_summary_live(
    *,
    balance: Optional[float],
    open_positions: int,
    resting_orders: int,
    realized_pnl: float,
    unrealized_pnl: float,
    mtm: float,
    cost_basis: float,
) -> None:
    total_pnl = realized_pnl + unrealized_pnl
    section("LIVE ACCOUNT", subtitle=f"{open_positions} open · {resting_orders} resting")
    if balance is not None:
        emit(f"  {_dim('Collateral')}  {_bold(f'${balance:,.2f}')}")
    emit(
        f"  {_dim('P&L')}         "
        f"realized {_pnl_text(realized_pnl)}  "
        f"unrealized {_pnl_text(unrealized_pnl)}  "
        f"total {_pnl_text(total_pnl)}"
    )
    if open_positions:
        emit(
            f"  {_dim('Holdings')}    MTM ${_dim(f'{mtm:,.2f}')}  "
            f"{_dim('basis')} ${_dim(f'{cost_basis:,.2f}')}"
        )


def open_position_row(
    *,
    question: str,
    outcome: str,
    qty: float,
    entry: float,
    bid: Optional[float],
    ask: Optional[float],
    unrealized_pnl: float,
    target: float,
    hours_to_resolution: Optional[float] = None,
) -> None:
    bid_txt = f"{bid:.4f}" if bid is not None else "—"
    ask_txt = f"{ask:.4f}" if ask is not None else "—"
    dist_to_target = ""
    if bid is not None and bid > 0 and target > 0:
        dist_to_target = f"  {_dim('to TP')} {_pct_text(((target - bid) / bid) * 100.0)}"
    emit(f"  {_dim('•')} {_clip(question, 54)}")
    emit(
        f"    {_bold(outcome)}  "
        f"{_dim('qty')} {qty:.2f}  "
        f"{_dim('entry')} {entry:.4f}  "
        f"{_dim('bid/ask')} {bid_txt}/{ask_txt}  "
        f"{_dim('window')} {_hours_remaining_colored(hours_to_resolution)}  "
        f"{_dim('uPnL')} {_pnl_text(unrealized_pnl)}  "
        f"{_dim('target')} ≥{target:.4f}{dist_to_target}"
    )


def resting_order_row(*, side: str, size_txt: str, price_txt: str, token: str, order_id: str) -> None:
    emit(
        f"    {_bold(str(side).upper())} {size_txt} @ {price_txt}  "
        f"{_dim('token')} {token}…  {_dim('id')} {order_id}…"
    )


def market_pool_loaded(*, count: int, source: str, gamma_cap: int, clob_cap: int) -> None:
    emit(
        f"{_blue('POOL')}  {count} markets  "
        f"{_dim('source')} {source}  "
        f"{_dim('gamma≤')}{gamma_cap}  {_dim('CLOB≤')}{clob_cap}  "
        f"{_dim('deduped')}"
    )


def scan_filter_funnel(stats: Mapping[str, int]) -> None:
    if not stats:
        return
    parts = [f"{label} {count}" for label, count in stats.items() if count]
    if not parts:
        return
    emit(f"{_dim('FILTER')}  " + _dim(" · ".join(parts)))


def candidates_summary(
    *,
    count: int,
    filters_text: str,
    skipped_profit_room: int,
    profit_target_pct: float,
    exit_cap: float,
) -> None:
    emit()
    emit(f"{_green('CANDIDATES')}  {count}  {_dim(filters_text)}")
    if skipped_profit_room:
        emit(
            f"  {_dim('Skipped')} {skipped_profit_room} legs without +{profit_target_pct:.1f}% TP room "
            f"(exit cap {exit_cap:.4f})"
        )


def opportunity_card(
    opp: Mapping[str, Any],
    *,
    note: str = "",
    upside_max: int,
    print_min_score: int,
    profit_target_pct: float,
) -> None:
    hrs = opp.get("hours_to_resolution")
    hrs_txt = _hours_remaining_colored(float(hrs) if hrs is not None else None)
    bid = opp.get("bid")
    ask = opp.get("ask_price")
    bid_txt = f"{bid:.4f}" if bid is not None else "—"
    mid_txt = f"{(bid + ask) / 2:.4f}" if bid is not None and ask is not None else "—"
    ask_pct = float(ask) * 100.0 if ask is not None else 0.0
    target_bid = float(ask) * (1.0 + profit_target_pct / 100.0) if ask is not None else 0.0
    suffix = f"  {_yellow(note)}" if note else ""
    score = int(opp.get("market_score", 0) or 0)
    score_txt = ""
    if score > print_min_score:
        score_txt = f"  {_magenta(f'score {score}/{upside_max}')}"

    emit()
    emit(f"  {_cyan('▸')} {_clip(str(opp.get('market_question', 'Unknown')), 58)}{suffix}")
    emit(
        f"    {_bold(str(opp.get('outcome', '?')))}  "
        f"{_dim('bid/mid/ask')} {bid_txt}/{mid_txt}/{ask:.4f} ({ask_pct:.1f}% implied)  "
        f"{_dim('window left')} {hrs_txt}"
    )
    emit(
        f"    {_dim('book')} spread {opp['spread_pct']:.2f}%  "
        f"depth ${float(opp['depth']):,.0f}  "
        f"vol24h ${float(opp['volume_24h']):,.0f}  "
        f"{_dim('TP bid')} ≥{target_bid:.4f}{score_txt}"
    )


def buy_action(
    *,
    dry_run: bool,
    outcome: str,
    price: float,
    qty: float,
    notional: float,
    target_exit: float,
    profit_target_pct: float,
    hours_to_resolution: Optional[float] = None,
) -> None:
    mode = _yellow("[DRY RUN]") if dry_run else _green("[LIVE]")
    emit()
    emit(
        f"{mode} BUY {_bold(outcome)}  "
        f"{_dim('@')} {price:.4f}  "
        f"{_dim('qty')} {qty:.2f}  "
        f"{_dim('notional')} ${notional:,.2f}"
    )
    emit(
        f"  {_dim('Exit target')} ≥{target_exit:.4f}  "
        f"{_dim('(+')}{profit_target_pct:.1f}{_dim('% on bid)')}  "
        f"{_dim('window left')} {_hours_remaining_colored(hours_to_resolution)}"
    )


def sell_action(*, dry_run: bool, reason: str, outcome: str, bid: float, pnl_usd: float, pnl_pct: float) -> None:
    label = "PROFIT TARGET" if reason == "profit_target" else "STOP LOSS"
    color = _green if reason == "profit_target" else _red
    emit()
    emit(f"{color(label)}  {_dim('bid')} {bid:.4f}")
    emit(f"  {_bold(outcome)}  P&L {_pnl_text(pnl_usd)} ({_pct_text(pnl_pct)})")
    if dry_run:
        emit(f"  {_dim('Paper close @ bid')} {bid:.4f}")


def cycle_footer(*, duration_sec: float, sleep_sec: int, placed: int, candidates: int) -> None:
    emit()
    emit(
        f"{_dim('Cycle')} {duration_sec:.1f}s  "
        f"{_dim('entries')} {placed}/{candidates}  "
        f"{_dim('sleep')} {sleep_sec}s"
    )


def sleep_notice(seconds: int) -> None:
    emit()
    emit(f"{_dim('⏳')} Sleeping {seconds}s…")


def stopped(*, positions: int, orders: int, positions_file: str, orders_file: str) -> None:
    emit()
    emit(_bold("Stopped."))
    emit(
        f"  {_dim('Saved')} {positions} positions → {positions_file}  "
        f"{orders} resting orders → {orders_file}"
    )
