"""Alpaca Markets brokerage client wrapper."""

from __future__ import annotations

import logging
import re
from datetime import datetime

_OCC_RE = re.compile(r"^([A-Z]{1,6})\d{6}[CP]\d{8}$")


def _underlying_from_occ(symbol: str | None) -> str | None:
    """Extract underlying ticker from OCC option symbol (e.g. INTC260626C00122000 -> INTC)."""
    if not symbol:
        return None
    m = _OCC_RE.match(symbol)
    return m.group(1) if m else None

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    OptionLegRequest,
    StopLossRequest,
    TakeProfitRequest,
)
from alpaca.trading.enums import (
    OrderClass,
    OrderSide,
    OrderType,
    PositionIntent,
    QueryOrderStatus,
    TimeInForce,
)
from alpaca.common.exceptions import APIError

from src.config import get_settings

logger = logging.getLogger(__name__)


class AlpacaClient:
    """Wrapper around alpaca-py TradingClient for order execution and account management."""

    def __init__(self, client: TradingClient | None = None):
        if client is not None:
            self._client = client
        else:
            settings = get_settings()
            if not settings.alpaca_api_key or not settings.alpaca_secret_key:
                raise ValueError("Alpaca API credentials not configured")
            self._client = TradingClient(
                api_key=settings.alpaca_api_key,
                secret_key=settings.alpaca_secret_key,
                paper=("paper" in settings.alpaca_base_url),
                url_override=settings.alpaca_base_url,
            )

    # ── Account ──────────────────────────────────────────────

    def get_account(self) -> dict:
        """Get account info: equity, buying power, cash, day trade count."""
        acct = self._client.get_account()
        return {
            "equity": float(acct.equity),
            "buying_power": float(acct.buying_power),
            "cash": float(acct.cash),
            "day_trade_count": int(acct.daytrade_count),
            "pattern_day_trader": acct.pattern_day_trader,
            "currency": acct.currency,
            "status": acct.status.value if acct.status else "unknown",
        }

    # ── Positions ────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        """Get all open positions."""
        positions = self._client.get_all_positions()
        return [
            {
                "ticker": p.symbol,
                "qty": float(p.qty),
                "side": p.side.value if p.side else "long",
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc),
                "change_today": float(p.change_today) if p.change_today else 0,
            }
            for p in positions
        ]

    def close_position(self, ticker: str) -> dict:
        """Close a specific position by ticker."""
        result = self._client.close_position(ticker)
        return {"ticker": ticker, "status": "closing", "order_id": str(result.id)}

    def close_all_positions(self) -> list[dict]:
        """Liquidate all open positions."""
        results = self._client.close_all_positions(cancel_orders=True)
        return [
            {"ticker": r.symbol, "status": "closing"}
            for r in (results or [])
        ]

    # ── Orders ───────────────────────────────────────────────

    def submit_order(
        self,
        ticker: str,
        qty: float,
        side: str,
        order_type: str = "market",
        limit_price: float | None = None,
        time_in_force: str = "day",
    ) -> dict:
        """Submit a single order (market or limit)."""
        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        tif = TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC

        if order_type == "limit" and limit_price is not None:
            request = LimitOrderRequest(
                symbol=ticker,
                qty=qty,
                side=order_side,
                time_in_force=tif,
                limit_price=limit_price,
            )
        else:
            request = MarketOrderRequest(
                symbol=ticker,
                qty=qty,
                side=order_side,
                time_in_force=tif,
            )

        order = self._client.submit_order(request)
        return self._order_to_dict(order)

    def submit_bracket_order(
        self,
        ticker: str,
        qty: float,
        side: str,
        limit_price: float | None = None,
        stop_loss_price: float | None = None,
        take_profit_price: float | None = None,
        time_in_force: str = "gtc",
    ) -> dict:
        """Submit a bracket order with stop-loss and take-profit legs."""
        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        tif = TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC

        kwargs = {
            "symbol": ticker,
            "qty": qty,
            "side": order_side,
            "time_in_force": tif,
            "order_class": "bracket",
        }

        if stop_loss_price is not None:
            kwargs["stop_loss"] = StopLossRequest(stop_price=stop_loss_price)
        if take_profit_price is not None:
            kwargs["take_profit"] = TakeProfitRequest(limit_price=take_profit_price)

        if limit_price is not None:
            request = LimitOrderRequest(**kwargs, limit_price=limit_price)
        else:
            request = MarketOrderRequest(**kwargs)

        order = self._client.submit_order(request)
        return self._order_to_dict(order)

    def submit_spread_order(
        self,
        legs: list[dict],
        qty: float,
        limit_price: float | None = None,
        time_in_force: str = "day",
    ) -> dict:
        """Submit a multi-leg option spread order.

        `legs` is a list of dicts shaped per AlpacaOrderParams.legs:
            {"occ_symbol": str, "side": "buy"|"sell", "ratio_qty": int}

        Alpaca's multi-leg (mleg) order class fans out a single qty (number of
        spreads) into the per-leg ratio_qty contracts; for a 2-leg vertical we
        always use ratio_qty=1 per leg and let top-level qty carry the spread
        count. limit_price is the net debit (positive) / net credit (negative)
        the order should fill at. time_in_force defaults to DAY because mleg
        orders are not supported as GTC.
        """
        if not legs or len(legs) < 2:
            raise ValueError(f"submit_spread_order requires 2+ legs, got {len(legs) if legs else 0}")

        tif = TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC
        order_legs: list[OptionLegRequest] = []
        for i, leg in enumerate(legs):
            try:
                occ_symbol = str(leg["occ_symbol"])
                side_str = str(leg["side"]).strip().lower()
                ratio_qty = int(leg["ratio_qty"])
            except (KeyError, TypeError, ValueError) as e:
                raise ValueError(f"leg {i} malformed: {e} (leg={leg!r})") from e
            if side_str not in ("buy", "sell"):
                raise ValueError(f"leg {i} side must be buy|sell, got {side_str!r}")
            leg_side = OrderSide.BUY if side_str == "buy" else OrderSide.SELL
            order_legs.append(OptionLegRequest(
                symbol=occ_symbol,
                ratio_qty=ratio_qty,
                side=leg_side,
            ))

        kwargs = {
            "qty": qty,
            "time_in_force": tif,
            "order_class": OrderClass.MLEG,
            "legs": order_legs,
        }
        if limit_price is not None:
            request = LimitOrderRequest(**kwargs, limit_price=limit_price)
        else:
            request = MarketOrderRequest(**kwargs)

        order = self._client.submit_order(request)
        return self._order_to_dict(order)

    def get_order(self, order_id: str) -> dict:
        """Get a specific order by ID."""
        order = self._client.get_order_by_id(order_id)
        return self._order_to_dict(order)

    def get_orders(self, status: str = "all", limit: int = 50) -> list[dict]:
        """Get recent orders."""
        status_map = {
            "open": QueryOrderStatus.OPEN,
            "closed": QueryOrderStatus.CLOSED,
            "all": QueryOrderStatus.ALL,
        }
        request = GetOrdersRequest(
            status=status_map.get(status, QueryOrderStatus.ALL),
            limit=limit,
        )
        orders = self._client.get_orders(request)
        return [self._order_to_dict(o) for o in orders]

    def cancel_order(self, order_id: str) -> dict:
        """Cancel a specific order."""
        self._client.cancel_order_by_id(order_id)
        return {"order_id": order_id, "status": "cancel_requested"}

    def cancel_all_orders(self) -> dict:
        """Cancel all open orders."""
        results = self._client.cancel_orders()
        return {"canceled": len(results) if results else 0}

    # ── Market Clock ─────────────────────────────────────────

    def is_market_open(self) -> bool:
        """Check if the market is currently open."""
        clock = self._client.get_clock()
        return clock.is_open

    def get_clock(self) -> dict:
        """Get market clock info."""
        clock = self._client.get_clock()
        return {
            "is_open": clock.is_open,
            "next_open": clock.next_open.isoformat() if clock.next_open else None,
            "next_close": clock.next_close.isoformat() if clock.next_close else None,
        }

    # ── Health ───────────────────────────────────────────────

    def test_connection(self) -> dict:
        """Test connection to Alpaca API."""
        try:
            acct = self._client.get_account()
            return {
                "connected": True,
                "paper": "paper" in get_settings().alpaca_base_url,
                "status": acct.status.value if acct.status else "unknown",
            }
        except APIError as e:
            return {"connected": False, "error": str(e)}
        except Exception as e:
            return {"connected": False, "error": str(e)}

    # ── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _order_to_dict(order) -> dict:
        legs = getattr(order, "legs", None)
        leg_dicts = None
        if legs:
            leg_dicts = [
                {
                    "order_id": str(leg.id),
                    "symbol": leg.symbol,
                    "side": leg.side.value if leg.side else None,
                    "qty": float(leg.qty) if leg.qty else 0,
                    "status": leg.status.value if leg.status else None,
                    "filled_price": float(leg.filled_avg_price) if leg.filled_avg_price else None,
                    "filled_qty": float(leg.filled_qty) if leg.filled_qty else 0,
                }
                for leg in legs
            ]
        # MLEG parent orders have symbol=None; underlying lives only on legs.
        # Derive from the first leg's OCC symbol so downstream consumers
        # (alpaca_orders.ticker NOT NULL, capital-cap queries) get the
        # underlying ticker.
        ticker = order.symbol
        if ticker is None and leg_dicts:
            for leg in leg_dicts:
                u = _underlying_from_occ(leg.get("symbol"))
                if u:
                    ticker = u
                    break
        return {
            "order_id": str(order.id),
            "ticker": ticker,
            "side": order.side.value if order.side else None,
            "qty": float(order.qty) if order.qty else 0,
            "type": order.type.value if order.type else None,
            "status": order.status.value if order.status else None,
            "limit_price": float(order.limit_price) if order.limit_price else None,
            "stop_price": float(order.stop_price) if order.stop_price else None,
            "filled_price": float(order.filled_avg_price) if order.filled_avg_price else None,
            "filled_qty": float(order.filled_qty) if order.filled_qty else 0,
            "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
            "filled_at": order.filled_at.isoformat() if order.filled_at else None,
            "legs": leg_dicts,
        }
