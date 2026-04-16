"""Trading safety rails — hard limits that gate all order execution."""

from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.models import TradingLog, PaperTrade
from src.services.order_mapper import AlpacaOrderParams

logger = logging.getLogger(__name__)


class TradingSafetyRails:
    """Hard safety limits for trading. All checks must pass before any order reaches Alpaca."""

    def __init__(self, db: Session):
        self.db = db
        settings = get_settings()
        self.mode = settings.trading_mode
        self.max_daily_loss = settings.max_daily_loss
        self.max_open_positions = settings.max_open_positions
        self.max_single_position = settings.max_position_size
        self.max_daily_orders = settings.max_daily_orders
        self.allowed_hours_only = settings.allowed_hours_only
        self.blocked_tickers = set(settings.blocked_tickers)

    def check_order(self, order: AlpacaOrderParams, buying_power: float = 0, market_open: bool = True) -> tuple[bool, str]:
        """Run all safety checks on an order.

        Returns (allowed, reason_if_blocked).
        """
        checks = [
            self._check_mode(),
            self._check_market_hours(market_open),
            self._check_blocked_ticker(order.ticker),
            self._check_position_limit(),
            self._check_daily_order_limit(),
            self._check_single_position_size(order),
        ]

        for allowed, reason in checks:
            if not allowed:
                self._log(order, "block", reason, passed=False)
                return False, reason

        return True, ""

    def _check_mode(self) -> tuple[bool, str]:
        if self.mode == "disabled":
            return False, "Trading mode is disabled"
        return True, ""

    def _check_market_hours(self, market_open: bool) -> tuple[bool, str]:
        if self.allowed_hours_only and not market_open:
            return False, "Market is closed and allowed_hours_only is enabled"
        return True, ""

    def _check_blocked_ticker(self, ticker: str) -> tuple[bool, str]:
        if ticker in self.blocked_tickers:
            return False, f"Ticker {ticker} is in blocked list"
        return True, ""

    def _check_position_limit(self) -> tuple[bool, str]:
        # Count open positions (paper trades + alpaca positions)
        open_count = self.db.query(PaperTrade).filter_by(status="open").count()
        try:
            from src.db.models import AlpacaPosition
            open_count += self.db.query(AlpacaPosition).count()
        except Exception:
            pass

        if open_count >= self.max_open_positions:
            return False, f"At position limit: {open_count}/{self.max_open_positions}"
        return True, ""

    def _check_daily_order_limit(self) -> tuple[bool, str]:
        today = date.today()
        today_start = datetime(today.year, today.month, today.day)
        order_count = (
            self.db.query(TradingLog)
            .filter(TradingLog.action == "submit", TradingLog.created_at >= today_start)
            .count()
        )
        if order_count >= self.max_daily_orders:
            return False, f"Daily order limit reached: {order_count}/{self.max_daily_orders}"
        return True, ""

    def _check_single_position_size(self, order: AlpacaOrderParams) -> tuple[bool, str]:
        price = order.limit_price or 0
        value = order.qty * price
        if order.strategy == "short":
            value *= 1.5  # Margin

        if value > self.max_single_position:
            return False, f"Position ${value:.0f} exceeds max ${self.max_single_position:.0f}"
        return True, ""

    def _log(self, order: AlpacaOrderParams, action: str, reason: str = "", passed: bool = True):
        """Record order attempt to trading_log."""
        self.db.add(TradingLog(
            ticker=order.ticker,
            action=action,
            strategy=order.strategy,
            qty=order.qty,
            side=order.side,
            reason=reason,
            passed_safety=1 if passed else 0,
        ))
        self.db.commit()

    def log_submission(self, order: AlpacaOrderParams, order_id: str):
        """Log a successful order submission."""
        self.db.add(TradingLog(
            ticker=order.ticker,
            action="submit",
            strategy=order.strategy,
            qty=order.qty,
            side=order.side,
            order_id=order_id,
            passed_safety=1,
        ))
        self.db.commit()
