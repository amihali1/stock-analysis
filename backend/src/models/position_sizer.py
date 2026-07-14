"""Position sizing with $1,000 max buy-in constraint."""

from __future__ import annotations

import logging
import math

from pydantic import BaseModel, Field

from src.models.ensemble import EnsembleScore
from src.models.options_strategies import _snap_strike
from src.config import get_settings

logger = logging.getLogger(__name__)

# Risk parameters
DEFAULT_STOP_LOSS_PCT = 0.05  # 5% stop-loss for shorts
DEFAULT_TARGET_PCT = 0.10  # 10% target for shorts


def _lookup_chain_premium(
    chain_data: list[dict] | None, target_strike: float, option_type: str
) -> tuple[float | None, float | None]:
    """Find nearest-strike contract for option_type; return (premium, strike).

    Premium = (bid+ask)/2 when both quoted, else last. Returns (None, None) if
    chain is empty / has no candidates / has no usable price — caller falls
    back to BS estimate + integer-snap. Strike returned is the actual chain
    strike (already a tradeable equity option strike), not the math target.
    """
    if not chain_data:
        return None, None
    candidates = [r for r in chain_data if r.get("option_type") == option_type]
    if not candidates:
        return None, None
    nearest = min(candidates, key=lambda r: abs(r["strike"] - target_strike))
    bid = nearest.get("bid") or 0
    ask = nearest.get("ask") or 0
    last = nearest.get("last") or 0
    if bid > 0 and ask > 0:
        premium = (bid + ask) / 2.0
    elif last > 0:
        premium = last
    else:
        return None, None
    return premium, nearest["strike"]


class ShortRecommendation(BaseModel):
    """A short-selling recommendation."""
    ticker: str
    strategy: str = "short"
    direction: str = "short"
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
    risk_type: str = "undefined"  # Naked shorts have unlimited risk


class LongRecommendation(BaseModel):
    """A long-stock recommendation."""
    ticker: str
    strategy: str = "long"
    direction: str = "long"
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
    risk_type: str = "defined"  # Long stock max loss = entry - stop


class PairShortRecommendation(BaseModel):
    """Market-neutral pair: short the pick + equal-dollar long hedge (SPY).

    Monetizes the drop model's RELATIVE alpha (picks underperform the market
    by ~0.54%/10d) without fighting market drift — bear_monetization sweep
    2026-07-07: +0.47%/10d per dollar deployed, win 58%, vs naked short
    -0.14% and credit spreads -4..-7.5%.
    """
    ticker: str
    strategy: str = "pair_short"
    direction: str = "short"
    score: float
    directional_signal: float
    volatility_signal: float
    sentiment_signal: float
    entry_price: float  # short-leg entry (the pick)
    stop_loss: float    # on the short leg
    target_price: float
    shares: int         # short-leg shares
    hedge_symbol: str
    hedge_shares: float # long hedge shares (fractional allowed when enabled)
    hedge_entry: float
    position_size: float = Field(description="Total capital: short margin + hedge notional")
    max_loss: float = Field(description="Short-leg stop distance in dollars (hedge gain ignored, conservative)")
    risk_type: str = "defined"  # hedged pair; stop on short leg bounds practical loss


class OptionsRecommendation(BaseModel):
    """An options recommendation (long puts or calls)."""
    ticker: str
    strategy: str = "options"
    direction: str = "short"  # "short" for puts, "long" for calls
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
    risk_type: str = "defined"  # Long options have max loss = premium paid


class PositionSizer:
    """Calculate position sizes within the $1,000 budget constraint."""

    def __init__(self, max_position: float | None = None):
        settings = get_settings()
        self.max_position = max_position or settings.effective_per_trade_cap
        self._drop_base_rate = settings.drop_base_rate
        self._rise_base_rate = settings.directional_base_rate
        self._spread_directional_lift = settings.spread_directional_lift
        self._spread_min_score = settings.spread_min_score
        self._bull_spread_structure = settings.bull_spread_structure

    def _make_spread_builder(self):
        from src.models.options_strategies import SpreadBuilder
        return SpreadBuilder(
            max_position=self.max_position,
            drop_base_rate=self._drop_base_rate,
            rise_base_rate=self._rise_base_rate,
            directional_lift=self._spread_directional_lift,
            min_score=self._spread_min_score,
            bull_structure=self._bull_spread_structure,
        )

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

    def size_pair_short(
        self,
        score: EnsembleScore,
        current_price: float,
        hedge_price: float,
        hedge_symbol: str = "SPY",
        stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
        target_pct: float = DEFAULT_TARGET_PCT,
    ) -> PairShortRecommendation | None:
        """Size a market-neutral pair: short the pick, long equal dollars of hedge.

        Capital = short margin (1.5x notional) + hedge notional, so
        short_notional * 2.5 <= effective budget. The hedge leg is ALWAYS
        fractional (independent of enable_fractional_shares): a whole-share
        SPY hedge at ~$600/share leaves a $1k short only ~60% hedged, and
        fractional is safe here — the hedge is a simple market DAY buy, which
        is exactly what Alpaca's fractional support covers.
        """
        if current_price <= 0 or hedge_price <= 0:
            return None

        confidence_scale = min(score.score * 2, 1.0)
        effective_max = self.max_position * confidence_scale

        # short_notional * 1.5 (margin) + short_notional (hedge) <= effective_max
        max_short_notional = effective_max / 2.5
        shares = int(max_short_notional / current_price)
        if shares < 1:
            return None
        short_notional = shares * current_price

        hedge_shares = round(short_notional / hedge_price, 4)
        if hedge_shares <= 0:
            return None
        hedge_notional = hedge_shares * hedge_price

        stop_loss_price = current_price * (1 + stop_loss_pct)
        target_price = current_price * (1 - target_pct)
        max_loss = shares * (stop_loss_price - current_price)

        return PairShortRecommendation(
            ticker=score.ticker,
            score=score.score,
            directional_signal=score.directional_signal,
            volatility_signal=score.volatility_signal,
            sentiment_signal=score.sentiment_signal,
            entry_price=round(current_price, 2),
            stop_loss=round(stop_loss_price, 2),
            target_price=round(target_price, 2),
            shares=shares,
            hedge_symbol=hedge_symbol,
            hedge_shares=hedge_shares,
            hedge_entry=round(hedge_price, 2),
            position_size=round(short_notional * 1.5 + hedge_notional, 2),
            max_loss=round(max_loss, 2),
        )

    def size_long(
        self,
        score: EnsembleScore,
        current_price: float,
        stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
        target_pct: float = DEFAULT_TARGET_PCT,
    ) -> LongRecommendation | None:
        """Size a long-stock position.

        Long stock: no margin multiplier. Stop below entry, target above.
        Max loss = shares * (entry - stop).
        """
        if current_price <= 0:
            return None

        confidence_scale = min(score.score * 2, 1.0)
        effective_max = self.max_position * confidence_scale

        max_shares = int(effective_max / current_price)

        if max_shares < 1:
            return None

        position_value = max_shares * current_price
        stop_loss_price = current_price * (1 - stop_loss_pct)
        target_price = current_price * (1 + target_pct)
        max_loss = max_shares * (current_price - stop_loss_price)

        return LongRecommendation(
            ticker=score.ticker,
            score=score.score,
            directional_signal=score.directional_signal,
            volatility_signal=score.volatility_signal,
            sentiment_signal=score.sentiment_signal,
            entry_price=round(current_price, 2),
            stop_loss=round(stop_loss_price, 2),
            target_price=round(target_price, 2),
            shares=max_shares,
            position_size=round(position_value, 2),
            max_loss=round(max_loss, 2),
        )

    def size_options(
        self,
        score: EnsembleScore,
        current_price: float,
        premium_per_share: float | None = None,
        strike_offset_pct: float = 0.05,
        expiry_days: int = 30,
        option_type: str = "put",
        chain_data: list[dict] | None = None,
    ) -> OptionsRecommendation | None:
        """Size a long-options position (put or call).

        Each contract = 100 shares. Total cost = premium * 100 * contracts <= max_position.
        Max loss on a long option = total premium paid.

        option_type="put"  -> bearish (strike below current, direction="short")
        option_type="call" -> bullish (strike above current, direction="long")

        When chain_data is provided, premium and strike are pulled from the
        actual chain (nearest contract by strike for the target option_type).
        Falls back to 3%-of-price BS estimate + integer-snap when chain is
        absent or has no matching contract — same behavior as before.
        """
        if option_type not in ("put", "call"):
            raise ValueError(f"option_type must be 'put' or 'call', got {option_type!r}")

        if current_price <= 0:
            return None

        if option_type == "call":
            target = current_price * (1 + strike_offset_pct)
            direction = "long"
        else:
            target = current_price * (1 - strike_offset_pct)
            direction = "short"

        chain_premium, chain_strike = _lookup_chain_premium(
            chain_data, target, option_type,
        )
        if chain_premium is not None and chain_strike is not None:
            premium_per_share = chain_premium
            strike = chain_strike
        else:
            if premium_per_share is None:
                premium_per_share = current_price * 0.03
            strike = _snap_strike(target)

        cost_per_contract = premium_per_share * 100

        if cost_per_contract <= 0:
            return None

        confidence_scale = min(score.score * 2, 1.0)
        effective_max = self.max_position * confidence_scale

        max_contracts = int(effective_max / cost_per_contract)

        if max_contracts < 1:
            return None

        total_cost = max_contracts * cost_per_contract

        return OptionsRecommendation(
            ticker=score.ticker,
            direction=direction,
            score=score.score,
            directional_signal=score.directional_signal,
            volatility_signal=score.volatility_signal,
            sentiment_signal=score.sentiment_signal,
            entry_price=round(current_price, 2),
            contracts=max_contracts,
            premium_per_contract=round(cost_per_contract, 2),
            position_size=round(total_cost, 2),
            max_loss=round(total_cost, 2),
            strike=strike,
            expiry_days=expiry_days,
            option_type=option_type,
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
        builder = self._make_spread_builder()
        return builder.suggest_spread(
            score, current_price, implied_vol, expiry_days, chain_data=chain_data,
        )

    def size_bull_spread(
        self,
        score: EnsembleScore,
        current_price: float,
        implied_vol: float = 0.30,
        expiry_days: int = 30,
        chain_data: list[dict] | None = None,
    ):
        """Size a bullish options spread position using SpreadBuilder.

        Returns a SpreadRecommendation with direction="long" or None.
        """
        builder = self._make_spread_builder()
        return builder.suggest_bull_spread(
            score, current_price, implied_vol, expiry_days, chain_data=chain_data,
        )
