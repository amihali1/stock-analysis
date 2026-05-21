"""Map platform recommendations to Alpaca order parameters."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date

from src.config import get_settings

logger = logging.getLogger(__name__)


def build_occ_symbol(ticker: str, expiry: date, option_type: str, strike: float) -> str:
    """Build an OCC option symbol.

    Format: ROOT + YYMMDD + C/P + 8-digit strike-times-1000.
    e.g. AAPL 2025-04-18 put strike $150 -> "AAPL250418P00150000".
    """
    if not ticker:
        raise ValueError("ticker required")
    if strike <= 0:
        raise ValueError(f"strike must be positive, got {strike}")
    side = option_type.strip().lower()
    if side in ("c", "call"):
        code = "C"
    elif side in ("p", "put"):
        code = "P"
    else:
        raise ValueError(f"option_type must be call|put, got {option_type!r}")
    strike_int = int(round(strike * 1000))
    return f"{ticker.upper()}{expiry.strftime('%y%m%d')}{code}{strike_int:08d}"


@dataclass
class AlpacaOrderParams:
    """Alpaca-ready order parameters."""
    ticker: str
    qty: float
    side: str  # "buy" or "sell"
    order_type: str  # "market", "limit"
    limit_price: float | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    time_in_force: str = "gtc"
    is_bracket: bool = False
    strategy: str = ""
    dry_run: bool = False
    occ_symbol: str | None = None
    # Multi-leg spread support. Each leg dict carries:
    #   {"occ_symbol": str, "side": "buy"|"sell", "ratio_qty": int, "option_type": "call"|"put", "strike": float}
    # When legs is set, the alpaca client routes via OptionOrderRequest(legs=[...])
    # instead of the single-symbol submit path.
    legs: list[dict] | None = None


class OrderMapper:
    """Translate recommendations into Alpaca order params."""

    def __init__(self, max_position: float | None = None):
        settings = get_settings()
        self.max_position = max_position or settings.effective_per_trade_cap
        self.enable_fractional = settings.enable_fractional_shares

    def recommendation_to_order(
        self,
        ticker: str,
        strategy: str,
        entry_price: float,
        stop_loss: float | None,
        target_price: float | None,
        position_size: float | None,
        contracts: int | None = None,
        strike: float | None = None,
        option_type: str | None = None,
        expiry: date | None = None,
        legs_json: str | None = None,
        buying_power: float | None = None,
        dry_run: bool = False,
    ) -> AlpacaOrderParams | None:
        """Convert a recommendation to Alpaca order params.

        Returns None if the order can't be built (e.g. insufficient buying power).
        """
        if strategy == "short":
            return self._map_short(
                ticker, entry_price, stop_loss, target_price,
                position_size, buying_power, dry_run,
            )
        elif strategy == "long":
            return self._map_long(
                ticker, entry_price, stop_loss, target_price,
                position_size, buying_power, dry_run,
            )
        elif strategy == "options":
            return self._map_options(
                ticker, entry_price, position_size, contracts,
                strike, option_type or "put", expiry, buying_power, dry_run,
                strategy_label="options",
            )
        elif strategy == "call_options":
            return self._map_call_options(
                ticker, entry_price, position_size, contracts,
                strike, expiry, buying_power, dry_run,
            )
        elif strategy == "spread":
            return self._map_spread(
                ticker, entry_price, position_size, contracts, expiry,
                legs_json, buying_power, dry_run, strategy_label="spread",
            )
        elif strategy == "bull_spread":
            return self._map_bull_spread(
                ticker, entry_price, position_size, contracts, expiry,
                legs_json, buying_power, dry_run,
            )
        else:
            logger.warning(f"Unknown strategy: {strategy}")
            return None

    def _map_short(
        self,
        ticker: str,
        entry_price: float,
        stop_loss: float | None,
        target_price: float | None,
        position_size: float | None,
        buying_power: float | None,
        dry_run: bool,
    ) -> AlpacaOrderParams | None:
        """Map a short recommendation to a bracket sell-short order."""
        if entry_price <= 0:
            return None

        # Calculate shares from position size (margin-adjusted)
        size = min(position_size or self.max_position, self.max_position)
        shares = int(size / (entry_price * 1.5))  # 150% margin
        if shares < 1:
            return None

        actual_size = shares * entry_price * 1.5

        # Check buying power
        if buying_power is not None and actual_size > buying_power:
            logger.warning(
                f"Insufficient buying power for {ticker} short: "
                f"need ${actual_size:.0f}, have ${buying_power:.0f}"
            )
            return None

        return AlpacaOrderParams(
            ticker=ticker,
            qty=shares,
            side="sell",
            order_type="limit",
            limit_price=round(entry_price, 2),
            stop_loss_price=round(stop_loss, 2) if stop_loss else None,
            take_profit_price=round(target_price, 2) if target_price else None,
            is_bracket=stop_loss is not None or target_price is not None,
            strategy="short",
            dry_run=dry_run,
        )

    def _map_long(
        self,
        ticker: str,
        entry_price: float,
        stop_loss: float | None,
        target_price: float | None,
        position_size: float | None,
        buying_power: float | None,
        dry_run: bool,
    ) -> AlpacaOrderParams | None:
        """Map a long-stock recommendation to a bracket buy order."""
        if entry_price <= 0:
            return None

        size = min(position_size or self.max_position, self.max_position)
        shares = int(size / entry_price)

        # Fractional fallback: only when whole-share floor is 0 AND the
        # setting is on. Alpaca only supports fractional as market orders
        # without brackets — limit/stop/target are silently dropped at the
        # API layer if we send them with a non-integer qty, so we strip them
        # here. Stays opt-in so paper mode keeps its bracket protections.
        if shares < 1:
            if not self.enable_fractional:
                return None
            qty = round(size / entry_price, 4)
            notional = qty * entry_price
            if notional < 1.0:  # Alpaca fractional minimum
                return None
            if buying_power is not None and notional > buying_power:
                logger.warning(
                    f"Insufficient buying power for {ticker} long (fractional): "
                    f"need ${notional:.2f}, have ${buying_power:.0f}"
                )
                return None
            return AlpacaOrderParams(
                ticker=ticker,
                qty=qty,
                side="buy",
                order_type="market",
                limit_price=None,
                stop_loss_price=None,
                take_profit_price=None,
                is_bracket=False,
                strategy="long",
                dry_run=dry_run,
            )

        actual_size = shares * entry_price
        if buying_power is not None and actual_size > buying_power:
            logger.warning(
                f"Insufficient buying power for {ticker} long: "
                f"need ${actual_size:.0f}, have ${buying_power:.0f}"
            )
            return None

        return AlpacaOrderParams(
            ticker=ticker,
            qty=shares,
            side="buy",
            order_type="limit",
            limit_price=round(entry_price, 2),
            stop_loss_price=round(stop_loss, 2) if stop_loss else None,
            take_profit_price=round(target_price, 2) if target_price else None,
            is_bracket=stop_loss is not None or target_price is not None,
            strategy="long",
            dry_run=dry_run,
        )

    def _map_options(
        self,
        ticker: str,
        entry_price: float,
        position_size: float | None,
        contracts: int | None,
        strike: float | None,
        option_type: str | None,
        expiry: date | None,
        buying_power: float | None,
        dry_run: bool,
        strategy_label: str = "options",
    ) -> AlpacaOrderParams | None:
        """Map a single-leg options recommendation to an Alpaca order.

        When `expiry`, `strike`, and `option_type` are all present, builds the
        OCC symbol on params; downstream Alpaca submission keys off occ_symbol
        for option contracts. Without expiry/strike, returns None — research-
        only recs (no chain selection) can't be routed.
        """
        if not contracts or contracts < 1:
            return None
        if expiry is None or strike is None or not option_type:
            logger.warning(
                f"Cannot route {ticker} {strategy_label}: missing expiry/strike/option_type "
                f"(expiry={expiry}, strike={strike}, option_type={option_type})"
            )
            return None

        try:
            occ = build_occ_symbol(ticker, expiry, option_type, strike)
        except ValueError as e:
            logger.warning(f"OCC symbol build failed for {ticker} {strategy_label}: {e}")
            return None

        # Estimate premium cost
        premium_per_share = entry_price * 0.03  # Rough estimate if not provided
        total_cost = premium_per_share * 100 * contracts
        total_cost = min(total_cost, self.max_position)

        if buying_power is not None and total_cost > buying_power:
            logger.warning(
                f"Insufficient buying power for {ticker} {strategy_label}: "
                f"need ${total_cost:.0f}, have ${buying_power:.0f}"
            )
            return None

        return AlpacaOrderParams(
            ticker=ticker,
            qty=contracts,
            side="buy",
            order_type="limit",
            limit_price=round(premium_per_share, 2),
            strategy=strategy_label,
            dry_run=dry_run,
            occ_symbol=occ,
        )

    def _map_call_options(
        self,
        ticker: str,
        entry_price: float,
        position_size: float | None,
        contracts: int | None,
        strike: float | None,
        expiry: date | None,
        buying_power: float | None,
        dry_run: bool,
    ) -> AlpacaOrderParams | None:
        """Map a long-call recommendation to a single-leg buy order.

        Same shape as `_map_options` for puts — buying premium for an OTM call.
        """
        return self._map_options(
            ticker, entry_price, position_size, contracts,
            strike, "call", expiry, buying_power, dry_run,
            strategy_label="call_options",
        )

    def _map_spread(
        self,
        ticker: str,
        entry_price: float,
        position_size: float | None,
        contracts: int | None,
        expiry: date | None,
        legs_json: str | None,
        buying_power: float | None,
        dry_run: bool,
        strategy_label: str = "spread",
    ) -> AlpacaOrderParams | None:
        """Map a multi-leg spread recommendation to a multi-leg option order.

        Reads the per-leg payload from legs_json (written at rec-generation time
        by the scheduler from SpreadRecommendation.legs). Builds an OCC symbol
        per leg and emits AlpacaOrderParams.legs with the Alpaca leg shape.
        Without legs_json or expiry, returns None — the rec cannot be routed.

        The top-level qty is the spread's contract count; per-leg ratio_qty is
        ALWAYS 1 because SpreadRecommendation legs already carry the resolved
        per-spread contract count (multi-spread orders are submitted N spreads
        x 1 ratio rather than 1 spread x N ratio per the Alpaca convention).
        """
        if not contracts or contracts < 1:
            return None
        if expiry is None:
            logger.warning(f"Cannot route {ticker} {strategy_label}: missing expiry")
            return None
        if not legs_json:
            logger.warning(f"Cannot route {ticker} {strategy_label}: missing legs_json")
            return None

        try:
            raw_legs = json.loads(legs_json)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Cannot route {ticker} {strategy_label}: legs_json parse failed: {e}")
            return None
        if not isinstance(raw_legs, list) or len(raw_legs) < 2:
            logger.warning(
                f"Cannot route {ticker} {strategy_label}: legs_json must be a list of "
                f"2+ legs, got {raw_legs!r}"
            )
            return None

        legs: list[dict] = []
        for i, leg in enumerate(raw_legs):
            try:
                opt_type = str(leg["option_type"])
                action = str(leg["action"]).strip().lower()
                strike = float(leg["strike"])
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(
                    f"Cannot route {ticker} {strategy_label}: leg {i} malformed: {e} (leg={leg!r})"
                )
                return None
            if action not in ("buy", "sell"):
                logger.warning(
                    f"Cannot route {ticker} {strategy_label}: leg {i} action must be buy|sell, "
                    f"got {action!r}"
                )
                return None
            try:
                occ = build_occ_symbol(ticker, expiry, opt_type, strike)
            except ValueError as e:
                logger.warning(f"OCC symbol build failed for {ticker} {strategy_label} leg {i}: {e}")
                return None
            legs.append({
                "occ_symbol": occ,
                "side": action,
                "ratio_qty": 1,
                "option_type": opt_type,
                "strike": strike,
            })

        cost = min(position_size or self.max_position, self.max_position)
        if cost <= 0:
            return None
        if buying_power is not None and cost > buying_power:
            logger.warning(
                f"Insufficient buying power for {ticker} {strategy_label}: "
                f"need ${cost:.0f}, have ${buying_power:.0f}"
            )
            return None

        per_contract = cost / contracts
        return AlpacaOrderParams(
            ticker=ticker,
            qty=contracts,
            side="buy",
            order_type="limit",
            limit_price=round(per_contract, 2),
            strategy=strategy_label,
            dry_run=dry_run,
            legs=legs,
        )

    def _map_bull_spread(
        self,
        ticker: str,
        entry_price: float,
        position_size: float | None,
        contracts: int | None,
        expiry: date | None,
        legs_json: str | None,
        buying_power: float | None,
        dry_run: bool,
    ) -> AlpacaOrderParams | None:
        """Map a bullish spread recommendation to a multi-leg option order."""
        return self._map_spread(
            ticker, entry_price, position_size, contracts, expiry,
            legs_json, buying_power, dry_run, strategy_label="bull_spread",
        )

    def validate_order(self, params: AlpacaOrderParams) -> tuple[bool, str]:
        """Final validation before submission."""
        if params.qty <= 0:
            return False, "Quantity must be positive"
        if params.limit_price is not None and params.limit_price <= 0:
            return False, "Limit price must be positive"
        if params.strategy == "short":
            estimated_value = params.qty * (params.limit_price or 0)
            if estimated_value * 1.5 > self.max_position:
                return False, f"Position ${estimated_value * 1.5:.0f} exceeds max ${self.max_position:.0f}"
        elif params.strategy == "long":
            estimated_value = params.qty * (params.limit_price or 0)
            if estimated_value > self.max_position:
                return False, f"Position ${estimated_value:.0f} exceeds max ${self.max_position:.0f}"
        return True, ""
