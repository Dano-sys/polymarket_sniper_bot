"""
Minimal Polymarket CLOB live execution for the sniper bot.
Uses py-clob-client-v2 (CLOB V2 signing) with proxy wallet + optional builder code.
"""
from __future__ import annotations

import os
import logging
from decimal import Decimal
from typing import Any, Optional, Tuple

import requests

log = logging.getLogger(__name__)


class ClobBuySkipChainPrice(Exception):
    """No share size at this limit yields USDC maker amount on cent boundaries."""

CLOB_API = os.getenv("CLOB_API", "https://clob.polymarket.com").rstrip("/")
GAMMA_API = os.getenv("GAMMA_API", "https://gamma-api.polymarket.com").rstrip("/")
CLOB_MIN_ORDER_USDC = float(os.getenv("CLOB_MIN_ORDER_USDC", "1.0"))
NEG_RISK_PREFER_GAMMA = os.getenv("NEG_RISK_PREFER_GAMMA", "false").lower() == "true"

PRIVATE_KEY = (os.getenv("PRIVATE_KEY") or os.getenv("PROXY_PRIVATE_KEY") or "").strip()
FUNDER_ADDRESS = (os.getenv("FUNDER_ADDRESS") or os.getenv("PROXY_FUNDER_ADDRESS") or "").strip()
SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE") or os.getenv("PROXY_SIGNATURE_TYPE") or "0")
POLY_BUILDER_CODE = (os.getenv("POLY_BUILDER_CODE") or os.getenv("POLY_BUILDER_CODE_HEX") or "").strip()

_session: Optional[requests.Session] = None


def _http_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        proxy = (os.getenv("POLYMARKET_PROXY") or "").strip()
        if proxy:
            _session.proxies = {"http": proxy, "https": proxy}
    return _session


def http_get(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", 20)
    return _http_session().get(url, **kwargs)


def _gamma_market_by_condition_id(payload: Any, condition_id: str) -> Optional[dict]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data") or payload.get("markets") or []
    else:
        rows = []
    cid = (condition_id or "").lower()
    for row in rows:
        if not isinstance(row, dict):
            continue
        c = str(row.get("conditionId") or row.get("condition_id") or "").lower()
        if c == cid:
            return row
    return None


class SniperLiveTrader:
    """Thin live wrapper: init CLOB V2 client, place BUY/SELL FAK."""

    def __init__(self):
        self.client = None
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        if not PRIVATE_KEY:
            raise RuntimeError("PRIVATE_KEY (or PROXY_PRIVATE_KEY) not set — cannot trade live")
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import BuilderConfig

        kwargs = {
            "host": CLOB_API,
            "chain_id": 137,
            "key": PRIVATE_KEY,
            "signature_type": SIGNATURE_TYPE,
        }
        if FUNDER_ADDRESS:
            kwargs["funder"] = FUNDER_ADDRESS
        if POLY_BUILDER_CODE:
            kwargs["builder_config"] = BuilderConfig(builder_code=POLY_BUILDER_CODE)
            log.info("Sniper CLOB: builder code configured for order attribution")
        self.client = ClobClient(**kwargs)
        self.client.set_api_creds(self.client.create_or_derive_api_key())
        self._initialized = True
        log.info("Sniper CLOB client ready (py-clob-client-v2)")

    def _round_to_tick(self, price: float, tick_size: str) -> float:
        try:
            ts = float(tick_size) if tick_size else 0.01
            if ts <= 0:
                ts = 0.01
            decimals = len(str(tick_size).split(".")[-1].rstrip("0")) if "." in str(tick_size) else 2
            return round(round(price / ts) * ts, decimals)
        except (ValueError, TypeError):
            return round(price, 2)

    def _quantize_clob_buy_order(self, size: float, limit_price: float, tick_size: str) -> Tuple[float, float]:
        """CLOB BUY: maker (USDC) ≤2 decimals, taker (shares) ≤4, price on tick."""
        if size <= 0 or limit_price <= 0:
            return max(0.0, round(float(size), 4)), float(self._round_to_tick(float(limit_price), tick_size))
        try:
            from py_clob_client_v2.order_builder.builder import ROUNDING_CONFIG
            from py_clob_client_v2.order_builder.helpers import round_down, round_normal

            ts = tick_size if tick_size else "0.01"
            rc = ROUNDING_CONFIG.get(ts) or ROUNDING_CONFIG["0.01"]
            price_f = float(self._round_to_tick(float(limit_price), tick_size))
            raw = str(ts)
            if "." in raw:
                nd = len(raw.split(".")[-1].rstrip("0")) or 2
            else:
                nd = 2
            p_str = format(price_f, f".{nd}f")
            p_sdk = round_normal(float(price_f), rc.price)
            if p_sdk <= 0:
                return 0.0, float(p_str)
            t_max = round_down(float(size), rc.size)
            min_u = Decimal(str(CLOB_MIN_ORDER_USDC))
            p_d = Decimal(str(p_sdk))
            t = float(t_max)
            step = 10 ** (-rc.size)
            max_iters = 50000
            it = 0
            while t >= step and it < max_iters:
                it += 1
                t_adj = round_down(t, rc.size)
                m = Decimal(str(t_adj)) * p_d
                if m > 0 and m >= min_u and m % Decimal("0.01") == 0:
                    return float(str(t_adj)), float(str(p_sdk))
                t = round_down(t - step, rc.size)
            raise ClobBuySkipChainPrice(
                f"no maker–taker pair with USDC cents at 2dp at limit {p_str} "
                f"(tried from {t_max:.4f} down; last_t={t:.4f})"
            )
        except ClobBuySkipChainPrice:
            raise
        except Exception:
            price = float(self._round_to_tick(float(limit_price), tick_size))
            if price <= 0:
                return 0.0, price
            usdc = round(float(size) * price, 2)
            if usdc < CLOB_MIN_ORDER_USDC:
                raise ClobBuySkipChainPrice(f"notional ${usdc:.2f} < ${CLOB_MIN_ORDER_USDC}")
            sz = round(usdc / price, 4)
            return max(0.0, sz), price

    def fetch_order_options(self, token_id: str, condition_id: Optional[str]) -> Any:
        from py_clob_client_v2.clob_types import PartialCreateOrderOptions

        neg_risk = False
        tick_size = "0.01"
        try:
            self.initialize()
            book = self.client.get_order_book(token_id)
            ts_d = getattr(self.client, "_ClobClient__tick_sizes", None)
            if isinstance(ts_d, dict) and book is not None and getattr(book, "tick_size", None):
                ts_d[token_id] = str(book.tick_size)
            neg_risk = bool(self.client.get_neg_risk(token_id))
            tick_size = str(self.client.get_tick_size(token_id) or "0.01")
            cid = (condition_id or "").strip()
            if cid:
                r = http_get(f"{GAMMA_API}/markets", params={"condition_ids": cid})
                if r.ok:
                    gm = _gamma_market_by_condition_id(r.json(), cid)
                    if gm:
                        gn = gm.get("negRisk")
                        if gn is None:
                            gn = gm.get("neg_risk")
                        if gn is not None:
                            gnr = bool(gn)
                            if gnr != neg_risk:
                                if NEG_RISK_PREFER_GAMMA:
                                    neg_risk = gnr
                                else:
                                    log.warning(
                                        "neg_risk CLOB=%s vs Gamma=%s (token %s...) — using CLOB first",
                                        neg_risk,
                                        gnr,
                                        token_id[:12],
                                    )
        except Exception as e:
            log.debug("fetch_order_options: %s", e)
        try:
            nr_d = getattr(self.client, "_ClobClient__neg_risk", None)
            if isinstance(nr_d, dict):
                nr_d[token_id] = bool(neg_risk)
        except Exception:
            pass
        return PartialCreateOrderOptions(neg_risk=bool(neg_risk), tick_size=tick_size)

    def refresh_conditional_allowance(self, token_id: str) -> None:
        self.initialize()
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType

        try:
            self.client.update_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id)
            )
        except Exception as e:
            log.debug("update_balance_allowance: %s", e)

    def get_collateral_balance_usdc(self, refresh: bool = True) -> float:
        """Tradable CLOB collateral balance (USDC/pUSD units)."""
        self.initialize()
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType

        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=-1)
        if refresh:
            try:
                self.client.update_balance_allowance(params)
            except Exception as e:
                log.debug("update_balance_allowance collateral: %s", e)
        resp = self.client.get_balance_allowance(params)
        raw = resp.get("balance", "0") if isinstance(resp, dict) else "0"
        try:
            return max(0.0, float(int(str(raw))) / 1e6)
        except (ValueError, TypeError):
            try:
                return max(0.0, float(raw) / 1e6)
            except (ValueError, TypeError):
                return 0.0

    def fetch_open_orders(self) -> list:
        self.initialize()
        rows = self.client.get_open_orders()
        return rows if isinstance(rows, list) else []

    def place_buy_fak(
        self,
        token_id: str,
        size: float,
        reference_price: float,
        condition_id: Optional[str],
        slippage: float,
    ) -> dict:
        """BUY with limit = min(reference + slippage, 0.99), FAK then GTC fallback."""
        from py_clob_client_v2.clob_types import OrderArgs, OrderType
        from py_clob_client_v2.order_utils.model.side import Side

        self.initialize()
        opts = self.fetch_order_options(token_id, condition_id)
        tick_size = getattr(opts, "tick_size", "0.01") or "0.01"
        limit_price = self._round_to_tick(min(reference_price + slippage, 0.99), tick_size)
        limit_price = min(max(limit_price, 0.01), 0.99)
        place_size, order_price = self._quantize_clob_buy_order(size, limit_price, tick_size)
        if place_size <= 0:
            raise ValueError("BUY size quantized to zero")
        order_args = OrderArgs(token_id=token_id, price=order_price, size=place_size, side=Side.BUY)
        signed = self.client.create_order(order_args, opts)
        try:
            return self.client.post_order(signed, OrderType.FAK)
        except Exception as e:
            err = str(e).lower()
            if "no orders found to match" in err or "couldn't be fully filled" in err:
                log.info("BUY FAK unmatched, GTC fallback @ %s", order_price)
                return self.client.create_and_post_order(order_args, opts)
            raise

    def place_sell_fak(
        self,
        token_id: str,
        size: float,
        reference_bid: float,
        condition_id: Optional[str],
        slippage: float,
    ) -> dict:
        """SELL at max(reference_bid - slippage, 0.01), FAK."""
        from py_clob_client_v2.clob_types import OrderArgs, OrderType
        from py_clob_client_v2.order_utils.model.side import Side

        self.initialize()
        self.refresh_conditional_allowance(token_id)
        opts = self.fetch_order_options(token_id, condition_id)
        tick_size = getattr(opts, "tick_size", "0.01") or "0.01"
        limit_price = self._round_to_tick(max(reference_bid - slippage, 0.01), tick_size)
        limit_price = min(max(limit_price, 0.01), 0.99)
        place_size = max(0.0, round(float(size), 4))
        if place_size <= 0:
            raise ValueError("SELL size is zero")
        order_args = OrderArgs(token_id=token_id, price=limit_price, size=place_size, side=Side.SELL)
        signed = self.client.create_order(order_args, opts)
        try:
            return self.client.post_order(signed, OrderType.FAK)
        except Exception as e:
            err = str(e).lower()
            if "no orders found to match" in err or "couldn't be fully filled" in err:
                return self.client.create_and_post_order(order_args, opts)
            raise


def live_trader_singleton() -> SniperLiveTrader:
    if not hasattr(live_trader_singleton, "_inst"):
        setattr(live_trader_singleton, "_inst", SniperLiveTrader())
    return getattr(live_trader_singleton, "_inst")


def safe_json(resp: Any) -> Any:
    if resp is None:
        return {}
    if isinstance(resp, dict):
        return resp
    if hasattr(resp, "json"):
        try:
            return resp.json()
        except Exception:
            return {"raw": str(resp)}
    return resp
