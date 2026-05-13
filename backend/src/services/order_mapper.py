"""Map platform recommendations to Alpaca order parameters."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.config import get_settings

logger = logging.getLogger(__name__)


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


class OrderMapper:
    """Translate recommendations into Alpaca order params."""

    def __init__(self, max_position: float | None = None):
        self.max_position = max_position or get_settings().max_position_size

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
                strike, option_type or "put", buying_power, dry_run,
                strategy_label="options",
            )
        elif strategy == "call_options":
            return self._map_call_options(
                ticker, entry_price, position_size, contracts,
                strike, buying_power, dry_run,
            )
        elif strategy == "spread":
            return self._map_spread(
                ticker, entry_price, position_size, contracts,
                buying_power, dry_run, strategy_label="spread",
            )
        elif strategy == "bull_spread":
            return self._map_bull_spread(
                ticker, entry_price, position_size, contracts,
                buying_power, dry_run,
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
        if shares < 1:
            return None

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
        buying_power: float | None,
        dry_run: bool,
        strategy_label: str = "options",
    ) -> AlpacaOrderParams | None:
        """Map an options recommendation to a single-leg order.

        Note: Alpaca options require the OCC symbol format (e.g. AAPL250418P00150000).
        This mapper builds the equity-based params; the execution engine handles
        OCC symbol construction when options trading is available.
        """
        if not contracts or contracts < 1:
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
        )

    def _map_call_options(
        self,
        ticker: str,
        entry_price: float,
        position_size: float | None,
        contracts: int | None,
        strike: float | None,
        buying_power: float | None,
        dry_run: bool,
    ) -> AlpacaOrderParams | None:
        """Map a long-call recommendation to a single-leg buy order.

        Same shape as `_map_options` for puts — buying premium for an OTM call.
        Execution-engine OCC construction is needed for real submission.
        """
        return self._map_options(
            ticker, entry_price, position_size, contracts,
            strike, "call", buying_power, dry_run,
            strategy_label="call_options",
        )

    def _map_spread(
        self,
        ticker: str,
        entry_price: float,
        position_size: float | None,
        contracts: int | None,
        buying_power: float | None,
        dry_run: bool,
        strategy_label: str = "spread",
    ) -> AlpacaOrderParams | None:
        """Map a bearish credit/debit spread recommendation to a placeholder order.

        Spreads are multi-leg orders. Without OCC symbol construction and chain
        data the mapper can only emit a placeholder sized to the recommendation's
        net cost; the execution engine is responsible for the actual leg build.
        Max loss is the position_size from the recommendation (defined risk).
        """
        if not contracts or contracts < 1:
            return None

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
        )

    def _map_bull_spread(
        self,
        ticker: str,
        entry_price: float,
        position_size: float | None,
        contracts: int | None,
        buying_power: float | None,
        dry_run: bool,
    ) -> AlpacaOrderParams | None:
        """Map a bullish spread recommendation to a placeholder order."""
        return self._map_spread(
            ticker, entry_price, position_size, contracts,
            buying_power, dry_run, strategy_label="bull_spread",
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
