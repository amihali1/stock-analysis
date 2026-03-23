"""Options spread strategies: bull put spreads, bear call spreads, iron condors."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta

from pydantic import BaseModel, Field

from src.models.ensemble import EnsembleScore

logger = logging.getLogger(__name__)


class SpreadLeg(BaseModel):
    """A single leg of an options spread."""
    option_type: str  # "call" or "put"
    action: str  # "buy" or "sell"
    strike: float
    premium: float  # Per-share premium
    contracts: int


class SpreadRecommendation(BaseModel):
    """A defined-risk options spread recommendation."""
    ticker: str
    strategy_name: str  # "bear_call_spread", "bull_put_spread", "iron_condor"
    score: float
    directional_signal: float
    volatility_signal: float
    sentiment_signal: float
    current_price: float
    legs: list[SpreadLeg]
    max_profit: float
    max_loss: float
    breakeven: float | list[float]
    risk_reward_ratio: float
    contracts: int
    net_credit: float  # Positive = credit spread, negative = debit spread
    expiry_days: int
    delta_exposure: float  # Net delta of the position
    theta_exposure: float  # Net theta (positive = time decay helps)
    vega_exposure: float  # Net vega
    earnings_warning: bool = False  # True if expiry crosses earnings


class SpreadBuilder:
    """Build defined-risk options spread strategies from ensemble signals."""

    def __init__(self, max_position: float = 5000.0):
        self.max_position = max_position

    def suggest_spread(
        self,
        score: EnsembleScore,
        current_price: float,
        implied_vol: float = 0.30,
        expiry_days: int = 30,
        earnings_date: date | None = None,
    ) -> SpreadRecommendation | None:
        """Suggest the best spread strategy based on signals.

        - High directional + low vol → bear call spread (credit)
        - High directional + high vol → bear put debit spread
        - High vol + neutral direction → iron condor (credit)
        """
        if current_price <= 0:
            return None

        # Check earnings proximity
        earnings_warning = False
        if earnings_date:
            days_to_earnings = (earnings_date - date.today()).days
            if 0 < days_to_earnings < expiry_days:
                earnings_warning = True

        # Choose strategy based on signals
        if score.directional_signal > 0.6 and score.volatility_signal < 0.5:
            return self._bear_call_spread(score, current_price, implied_vol, expiry_days, earnings_warning)
        elif score.directional_signal > 0.6 and score.volatility_signal >= 0.5:
            return self._bear_put_spread(score, current_price, implied_vol, expiry_days, earnings_warning)
        elif score.volatility_signal > 0.6 and score.directional_signal < 0.4:
            return self._iron_condor(score, current_price, implied_vol, expiry_days, earnings_warning)
        elif score.score >= 0.5:
            return self._bear_call_spread(score, current_price, implied_vol, expiry_days, earnings_warning)

        return None

    def _bear_call_spread(
        self, score: EnsembleScore, price: float, iv: float, expiry_days: int, earnings_warning: bool,
    ) -> SpreadRecommendation | None:
        """Bear call spread: sell call near ATM, buy call further OTM. Credit spread."""
        t = expiry_days / 365.0

        # Strikes: sell call slightly OTM, buy call further OTM
        sell_strike = round(price * 1.02, 2)  # 2% OTM
        buy_strike = round(price * 1.07, 2)  # 7% OTM
        spread_width = buy_strike - sell_strike

        # Estimate premiums using simplified Black-Scholes approximation
        sell_premium = self._estimate_premium(price, sell_strike, iv, t, "call")
        buy_premium = self._estimate_premium(price, buy_strike, iv, t, "call")

        net_credit_per_share = sell_premium - buy_premium
        if net_credit_per_share <= 0:
            return None

        # Position sizing: max_loss = (spread_width - net_credit) * 100 * contracts
        max_loss_per_contract = (spread_width - net_credit_per_share) * 100
        if max_loss_per_contract <= 0:
            return None

        confidence_scale = min(score.score * 2, 1.0)
        effective_max = self.max_position * confidence_scale
        contracts = max(1, int(effective_max / max_loss_per_contract))

        net_credit = net_credit_per_share * 100 * contracts
        max_loss = max_loss_per_contract * contracts
        max_profit = net_credit

        # Greeks (simplified)
        delta = self._estimate_delta(price, sell_strike, iv, t, "call") - self._estimate_delta(price, buy_strike, iv, t, "call")
        theta = 0.01 * contracts  # Positive theta (time decay helps credit spreads)
        vega = -0.05 * contracts  # Short vega (benefits from vol decrease)

        return SpreadRecommendation(
            ticker=score.ticker,
            strategy_name="bear_call_spread",
            score=score.score,
            directional_signal=score.directional_signal,
            volatility_signal=score.volatility_signal,
            sentiment_signal=score.sentiment_signal,
            current_price=price,
            legs=[
                SpreadLeg(option_type="call", action="sell", strike=sell_strike, premium=round(sell_premium, 2), contracts=contracts),
                SpreadLeg(option_type="call", action="buy", strike=buy_strike, premium=round(buy_premium, 2), contracts=contracts),
            ],
            max_profit=round(max_profit, 2),
            max_loss=round(max_loss, 2),
            breakeven=round(sell_strike + net_credit_per_share, 2),
            risk_reward_ratio=round(max_profit / max_loss, 4) if max_loss > 0 else 0,
            contracts=contracts,
            net_credit=round(net_credit, 2),
            expiry_days=expiry_days,
            delta_exposure=round(-delta * contracts, 4),
            theta_exposure=round(theta, 4),
            vega_exposure=round(vega, 4),
            earnings_warning=earnings_warning,
        )

    def _bear_put_spread(
        self, score: EnsembleScore, price: float, iv: float, expiry_days: int, earnings_warning: bool,
    ) -> SpreadRecommendation | None:
        """Bear put debit spread: buy put ATM, sell put further OTM. Debit spread."""
        t = expiry_days / 365.0

        buy_strike = round(price * 0.98, 2)  # Near ATM
        sell_strike = round(price * 0.93, 2)  # 7% OTM
        spread_width = buy_strike - sell_strike

        buy_premium = self._estimate_premium(price, buy_strike, iv, t, "put")
        sell_premium = self._estimate_premium(price, sell_strike, iv, t, "put")

        net_debit_per_share = buy_premium - sell_premium
        if net_debit_per_share <= 0:
            return None

        cost_per_contract = net_debit_per_share * 100
        max_profit_per_contract = (spread_width - net_debit_per_share) * 100

        if cost_per_contract <= 0 or max_profit_per_contract <= 0:
            return None

        confidence_scale = min(score.score * 2, 1.0)
        effective_max = self.max_position * confidence_scale
        contracts = max(1, int(effective_max / cost_per_contract))

        net_debit = cost_per_contract * contracts
        max_profit = max_profit_per_contract * contracts
        max_loss = net_debit

        delta = self._estimate_delta(price, buy_strike, iv, t, "put") - self._estimate_delta(price, sell_strike, iv, t, "put")
        theta = -0.01 * contracts  # Negative theta (time decay hurts debit spreads)
        vega = 0.03 * contracts  # Long vega (benefits from vol increase)

        return SpreadRecommendation(
            ticker=score.ticker,
            strategy_name="bull_put_spread",
            score=score.score,
            directional_signal=score.directional_signal,
            volatility_signal=score.volatility_signal,
            sentiment_signal=score.sentiment_signal,
            current_price=price,
            legs=[
                SpreadLeg(option_type="put", action="buy", strike=buy_strike, premium=round(buy_premium, 2), contracts=contracts),
                SpreadLeg(option_type="put", action="sell", strike=sell_strike, premium=round(sell_premium, 2), contracts=contracts),
            ],
            max_profit=round(max_profit, 2),
            max_loss=round(max_loss, 2),
            breakeven=round(buy_strike - net_debit_per_share, 2),
            risk_reward_ratio=round(max_profit / max_loss, 4) if max_loss > 0 else 0,
            contracts=contracts,
            net_credit=round(-net_debit, 2),  # Negative = debit
            expiry_days=expiry_days,
            delta_exposure=round(delta * contracts, 4),
            theta_exposure=round(theta, 4),
            vega_exposure=round(vega, 4),
            earnings_warning=earnings_warning,
        )

    def _iron_condor(
        self, score: EnsembleScore, price: float, iv: float, expiry_days: int, earnings_warning: bool,
    ) -> SpreadRecommendation | None:
        """Iron condor: sell OTM put + OTM call, buy further OTM put + call. Credit spread."""
        t = expiry_days / 365.0

        # Put side (bull put spread)
        sell_put_strike = round(price * 0.95, 2)
        buy_put_strike = round(price * 0.90, 2)

        # Call side (bear call spread)
        sell_call_strike = round(price * 1.05, 2)
        buy_call_strike = round(price * 1.10, 2)

        sell_put_premium = self._estimate_premium(price, sell_put_strike, iv, t, "put")
        buy_put_premium = self._estimate_premium(price, buy_put_strike, iv, t, "put")
        sell_call_premium = self._estimate_premium(price, sell_call_strike, iv, t, "call")
        buy_call_premium = self._estimate_premium(price, buy_call_strike, iv, t, "call")

        net_credit_per_share = (sell_put_premium - buy_put_premium) + (sell_call_premium - buy_call_premium)
        if net_credit_per_share <= 0:
            return None

        put_width = sell_put_strike - buy_put_strike
        call_width = buy_call_strike - sell_call_strike
        max_spread_width = max(put_width, call_width)
        max_loss_per_contract = (max_spread_width - net_credit_per_share) * 100

        if max_loss_per_contract <= 0:
            return None

        confidence_scale = min(score.score * 2, 1.0)
        effective_max = self.max_position * confidence_scale
        contracts = max(1, int(effective_max / max_loss_per_contract))

        net_credit = net_credit_per_share * 100 * contracts
        max_loss = max_loss_per_contract * contracts

        return SpreadRecommendation(
            ticker=score.ticker,
            strategy_name="iron_condor",
            score=score.score,
            directional_signal=score.directional_signal,
            volatility_signal=score.volatility_signal,
            sentiment_signal=score.sentiment_signal,
            current_price=price,
            legs=[
                SpreadLeg(option_type="put", action="buy", strike=buy_put_strike, premium=round(buy_put_premium, 2), contracts=contracts),
                SpreadLeg(option_type="put", action="sell", strike=sell_put_strike, premium=round(sell_put_premium, 2), contracts=contracts),
                SpreadLeg(option_type="call", action="sell", strike=sell_call_strike, premium=round(sell_call_premium, 2), contracts=contracts),
                SpreadLeg(option_type="call", action="buy", strike=buy_call_strike, premium=round(buy_call_premium, 2), contracts=contracts),
            ],
            max_profit=round(net_credit, 2),
            max_loss=round(max_loss, 2),
            breakeven=[
                round(sell_put_strike - net_credit_per_share, 2),
                round(sell_call_strike + net_credit_per_share, 2),
            ],
            risk_reward_ratio=round(net_credit / max_loss, 4) if max_loss > 0 else 0,
            contracts=contracts,
            net_credit=round(net_credit, 2),
            expiry_days=expiry_days,
            delta_exposure=0.0,  # Iron condors are delta-neutral
            theta_exposure=round(0.02 * contracts, 4),
            vega_exposure=round(-0.08 * contracts, 4),
            earnings_warning=earnings_warning,
        )

    def _estimate_premium(self, S: float, K: float, sigma: float, t: float, option_type: str) -> float:
        """Simplified Black-Scholes premium estimate (no risk-free rate for simplicity)."""
        if t <= 0 or sigma <= 0:
            return max(0, S - K) if option_type == "call" else max(0, K - S)

        d1 = (math.log(S / K) + 0.5 * sigma**2 * t) / (sigma * math.sqrt(t))
        d2 = d1 - sigma * math.sqrt(t)

        if option_type == "call":
            return S * self._norm_cdf(d1) - K * self._norm_cdf(d2)
        else:
            return K * self._norm_cdf(-d2) - S * self._norm_cdf(-d1)

    def _estimate_delta(self, S: float, K: float, sigma: float, t: float, option_type: str) -> float:
        """Estimate option delta."""
        if t <= 0 or sigma <= 0:
            if option_type == "call":
                return 1.0 if S > K else 0.0
            else:
                return -1.0 if S < K else 0.0

        d1 = (math.log(S / K) + 0.5 * sigma**2 * t) / (sigma * math.sqrt(t))

        if option_type == "call":
            return self._norm_cdf(d1)
        else:
            return self._norm_cdf(d1) - 1.0

    @staticmethod
    def _norm_cdf(x: float) -> float:
        """Standard normal CDF approximation."""
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))
