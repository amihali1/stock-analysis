"""Position sizing with $5,000 max buy-in constraint."""

from __future__ import annotations

import logging
import math

from pydantic import BaseModel, Field

from src.models.ensemble import EnsembleScore
from src.config import get_settings

logger = logging.getLogger(__name__)

# Risk parameters
DEFAULT_STOP_LOSS_PCT = 0.05  # 5% stop-loss for shorts
DEFAULT_TARGET_PCT = 0.10  # 10% target for shorts


class ShortRecommendation(BaseModel):
    """A short-selling recommendation."""
    ticker: str
    strategy: str = "short"
    score: float
    directional_signal: float
    volatility_signal: float
    sentiment_signal: float
    entry_price: float
    stop_loss: float
    target_price: float
    shares: int
    position_size: float = Field(description="Dollar amount of position")
    max_loss: float = Field(description="Maximum loss in dollars")


class OptionsRecommendation(BaseModel):
    """An options recommendation (put buying)."""
    ticker: str
    strategy: str = "options"
    score: float
    directional_signal: float
    volatility_signal: float
    sentiment_signal: float
    entry_price: float  # Current stock price
    contracts: int
    premium_per_contract: float
    position_size: float
    max_loss: float  # Max loss = total premium paid
    strike: float
    expiry_days: int
    option_type: str = "put"


class PositionSizer:
    """Calculate position sizes within the $5,000 budget constraint."""

    def __init__(self, max_position: float | None = None):
        self.max_position = max_position or get_settings().max_position_size

    def size_short(
        self,
        score: EnsembleScore,
        current_price: float,
        stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
        target_pct: float = DEFAULT_TARGET_PCT,
    ) -> ShortRecommendation | None:
        """Size a short position.

        Margin requirement is typically 150% of position value for shorts.
        We constrain: shares * price * 1.5 <= max_position
        """
        if current_price <= 0:
            return None

        # Scale position by score confidence (higher score = larger position)
        confidence_scale = min(score.score * 2, 1.0)  # 0.5+ score = full size
        effective_max = self.max_position * confidence_scale

        # Margin requirement: 150% of position value
        margin_multiplier = 1.5
        max_shares = int(effective_max / (current_price * margin_multiplier))

        if max_shares < 1:
            return None

        position_value = max_shares * current_price
        margin_required = position_value * margin_multiplier

        stop_loss_price = current_price * (1 + stop_loss_pct)
        target_price = current_price * (1 - target_pct)
        max_loss = max_shares * (stop_loss_price - current_price)

        return ShortRecommendation(
            ticker=score.ticker,
            score=score.score,
            directional_signal=score.directional_signal,
            volatility_signal=score.volatility_signal,
            sentiment_signal=score.sentiment_signal,
            entry_price=round(current_price, 2),
            stop_loss=round(stop_loss_price, 2),
            target_price=round(target_price, 2),
            shares=max_shares,
            position_size=round(margin_required, 2),
            max_loss=round(max_loss, 2),
        )

    def size_options(
        self,
        score: EnsembleScore,
        current_price: float,
        premium_per_share: float | None = None,
        strike_offset_pct: float = 0.05,
        expiry_days: int = 30,
    ) -> OptionsRecommendation | None:
        """Size a put options position.

        Each contract = 100 shares. Total cost = premium * 100 * contracts <= max_position.
        Max loss on a long put = total premium paid.
        """
        if current_price <= 0:
            return None

        # Estimate premium if not provided (~2-5% of stock price for ATM puts)
        if premium_per_share is None:
            premium_per_share = current_price * 0.03  # Rough estimate

        cost_per_contract = premium_per_share * 100

        if cost_per_contract <= 0:
            return None

        # Scale by score confidence
        confidence_scale = min(score.score * 2, 1.0)
        effective_max = self.max_position * confidence_scale

        max_contracts = int(effective_max / cost_per_contract)

        if max_contracts < 1:
            return None

        total_cost = max_contracts * cost_per_contract
        strike = round(current_price * (1 - strike_offset_pct), 2)

        return OptionsRecommendation(
            ticker=score.ticker,
            score=score.score,
            directional_signal=score.directional_signal,
            volatility_signal=score.volatility_signal,
            sentiment_signal=score.sentiment_signal,
            entry_price=round(current_price, 2),
            contracts=max_contracts,
            premium_per_contract=round(cost_per_contract, 2),
            position_size=round(total_cost, 2),
            max_loss=round(total_cost, 2),  # Max loss = premium paid
            strike=strike,
            expiry_days=expiry_days,
        )

    def size_spread(
        self,
        score: EnsembleScore,
        current_price: float,
        implied_vol: float = 0.30,
        expiry_days: int = 30,
        chain_data: list[dict] | None = None,
    ):
        """Size an options spread position using SpreadBuilder.

        Args:
            chain_data: Optional real options chain data. When provided,
                SpreadBuilder uses real premiums/strikes instead of BS estimates.

        Returns a SpreadRecommendation or None.
        """
        from src.models.options_strategies import SpreadBuilder

        builder = SpreadBuilder(max_position=self.max_position)
        return builder.suggest_spread(
            score, current_price, implied_vol, expiry_days, chain_data=chain_data,
        )
