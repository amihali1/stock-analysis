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
    strategy_name: str  # "bear_call_spread", "bear_put_debit_spread", "bull_call_debit_spread", "bull_put_credit_spread", "iron_condor"
    direction: str = "short"  # "short" for bearish, "long" for bullish, "neutral" for iron condor
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
    uses_real_data: bool = False  # True if real chain data was used
    risk_type: str = "defined"  # Spreads are always defined-risk


class SpreadBuilder:
    """Build defined-risk options spread strategies from ensemble signals."""

    def __init__(
        self,
        max_position: float = 1000.0,
        drop_base_rate: float = 0.05,
        rise_base_rate: float = 0.175,
        directional_lift: float = 1.3,
        min_score: float = 0.30,
    ):
        # Direction-aware lift gates replaced the legacy absolute
        # `directional_signal > 0.6` threshold (unreachable under sigmoid
        # calibration). See config.spread_* for rationale.
        self.max_position = max_position
        self.drop_base_rate = drop_base_rate
        self.rise_base_rate = rise_base_rate
        self.directional_lift = directional_lift
        self.min_score = min_score

    @property
    def _drop_lift_floor(self) -> float:
        return self.drop_base_rate * self.directional_lift

    @property
    def _rise_lift_floor(self) -> float:
        return self.rise_base_rate * self.directional_lift

    def suggest_spread(
        self,
        score: EnsembleScore,
        current_price: float,
        implied_vol: float = 0.30,
        expiry_days: int = 30,
        earnings_date: date | None = None,
        chain_data: list[dict] | None = None,
    ) -> SpreadRecommendation | None:
        """Suggest the best spread strategy based on signals.

        Args:
            chain_data: Optional real options chain data (list of dicts with
                strike, option_type, bid, ask, last, implied_vol keys).
                Falls back to Black-Scholes estimates when None or empty.

        - High directional + low vol -> bear call spread (credit)
        - High directional + high vol -> bear put debit spread
        - High vol + neutral direction -> iron condor (credit)
        """
        if current_price <= 0:
            return None

        # Check earnings proximity
        earnings_warning = False
        if earnings_date:
            days_to_earnings = (earnings_date - date.today()).days
            if 0 < days_to_earnings < expiry_days:
                earnings_warning = True

        # Bear-spread routing uses drop-side calibrated probability scale.
        # `_drop_lift_floor` defaults to ~0.065 (drop base 0.05 × lift 1.3).
        # Iron condor stays gated on "low directional + high vol" — direction
        # is below the lift floor (genuinely no edge), not below 0.4 absolute.
        high_dir = score.directional_signal > self._drop_lift_floor
        low_dir = score.directional_signal < self._drop_lift_floor
        high_vol = score.volatility_signal >= 0.5
        very_high_vol = score.volatility_signal > 0.6

        if high_dir and not high_vol:
            return self._bear_call_spread(score, current_price, implied_vol, expiry_days, earnings_warning, chain_data)
        elif high_dir and high_vol:
            return self._bear_put_spread(score, current_price, implied_vol, expiry_days, earnings_warning, chain_data)
        elif very_high_vol and low_dir:
            return self._iron_condor(score, current_price, implied_vol, expiry_days, earnings_warning, chain_data)
        elif score.score >= self.min_score:
            return self._bear_call_spread(score, current_price, implied_vol, expiry_days, earnings_warning, chain_data)

        return None

    def _get_premium(
        self, chain_data: list[dict] | None, target_strike: float,
        option_type: str, price: float, iv: float, t: float,
    ) -> tuple[float, float, bool]:
        """Get premium and actual strike, using real data if available.

        Returns (premium, actual_strike, used_real_data).
        """
        if chain_data:
            candidates = [
                r for r in chain_data
                if r["option_type"] == option_type
            ]
            if candidates:
                nearest = min(candidates, key=lambda r: abs(r["strike"] - target_strike))
                # Use midpoint of bid/ask for fair value
                bid = nearest.get("bid", 0) or 0
                ask = nearest.get("ask", 0) or 0
                if bid > 0 and ask > 0:
                    premium = (bid + ask) / 2.0
                elif nearest.get("last", 0) > 0:
                    premium = nearest["last"]
                else:
                    # Real data exists but no usable prices — fall back to BS
                    return self._estimate_premium(price, target_strike, iv, t, option_type), target_strike, False
                return premium, nearest["strike"], True

        # Fallback to BS estimate
        return self._estimate_premium(price, target_strike, iv, t, option_type), target_strike, False

    def _get_iv_from_chain(
        self, chain_data: list[dict] | None, target_strike: float, option_type: str, default_iv: float,
    ) -> float:
        """Get implied vol from chain data if available."""
        if not chain_data:
            return default_iv
        candidates = [r for r in chain_data if r["option_type"] == option_type]
        if not candidates:
            return default_iv
        nearest = min(candidates, key=lambda r: abs(r["strike"] - target_strike))
        iv = nearest.get("implied_vol", 0) or 0
        return iv if iv > 0 else default_iv

    def _bear_call_spread(
        self, score: EnsembleScore, price: float, iv: float, expiry_days: int,
        earnings_warning: bool, chain_data: list[dict] | None,
    ) -> SpreadRecommendation | None:
        """Bear call spread: sell call near ATM, buy call further OTM. Credit spread."""
        t = expiry_days / 365.0

        # Target strikes: sell call slightly OTM, buy call further OTM
        target_sell_strike = round(price * 1.02, 2)
        target_buy_strike = round(price * 1.07, 2)

        sell_premium, sell_strike, sell_real = self._get_premium(chain_data, target_sell_strike, "call", price, iv, t)
        buy_premium, buy_strike, buy_real = self._get_premium(chain_data, target_buy_strike, "call", price, iv, t)
        uses_real = sell_real and buy_real

        spread_width = buy_strike - sell_strike
        if spread_width <= 0:
            return None

        net_credit_per_share = sell_premium - buy_premium
        if net_credit_per_share <= 0:
            return None

        max_loss_per_contract = (spread_width - net_credit_per_share) * 100
        if max_loss_per_contract <= 0:
            return None

        confidence_scale = min(score.score * 2, 1.0)
        effective_max = self.max_position * confidence_scale
        # Floor at 1 contract is rejected when single-contract max_loss exceeds
        # the per-trade cap — emitting a busted recommendation just wastes a
        # top-K slot and gets blocked downstream by safety_rails. Drop instead.
        contracts = int(effective_max / max_loss_per_contract)
        if contracts < 1:
            return None

        net_credit = net_credit_per_share * 100 * contracts
        max_loss = max_loss_per_contract * contracts
        max_profit = net_credit

        # Greeks
        sell_iv = self._get_iv_from_chain(chain_data, sell_strike, "call", iv)
        buy_iv = self._get_iv_from_chain(chain_data, buy_strike, "call", iv)
        delta = self._estimate_delta(price, sell_strike, sell_iv, t, "call") - self._estimate_delta(price, buy_strike, buy_iv, t, "call")
        theta = 0.01 * contracts
        vega = -0.05 * contracts

        return SpreadRecommendation(
            ticker=score.ticker,
            strategy_name="bear_call_spread",
            direction="short",
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
            uses_real_data=uses_real,
        )

    def _bear_put_spread(
        self, score: EnsembleScore, price: float, iv: float, expiry_days: int,
        earnings_warning: bool, chain_data: list[dict] | None,
    ) -> SpreadRecommendation | None:
        """Bear put debit spread: buy put ATM, sell put further OTM. Debit spread."""
        t = expiry_days / 365.0

        target_buy_strike = round(price * 0.98, 2)
        target_sell_strike = round(price * 0.93, 2)

        buy_premium, buy_strike, buy_real = self._get_premium(chain_data, target_buy_strike, "put", price, iv, t)
        sell_premium, sell_strike, sell_real = self._get_premium(chain_data, target_sell_strike, "put", price, iv, t)
        uses_real = buy_real and sell_real

        spread_width = buy_strike - sell_strike
        if spread_width <= 0:
            return None

        net_debit_per_share = buy_premium - sell_premium
        if net_debit_per_share <= 0:
            return None

        cost_per_contract = net_debit_per_share * 100
        max_profit_per_contract = (spread_width - net_debit_per_share) * 100

        if cost_per_contract <= 0 or max_profit_per_contract <= 0:
            return None

        confidence_scale = min(score.score * 2, 1.0)
        effective_max = self.max_position * confidence_scale
        contracts = int(effective_max / cost_per_contract)
        if contracts < 1:
            return None

        net_debit = cost_per_contract * contracts
        max_profit = max_profit_per_contract * contracts
        max_loss = net_debit

        buy_iv = self._get_iv_from_chain(chain_data, buy_strike, "put", iv)
        sell_iv = self._get_iv_from_chain(chain_data, sell_strike, "put", iv)
        delta = self._estimate_delta(price, buy_strike, buy_iv, t, "put") - self._estimate_delta(price, sell_strike, sell_iv, t, "put")
        theta = -0.01 * contracts
        vega = 0.03 * contracts

        return SpreadRecommendation(
            ticker=score.ticker,
            strategy_name="bull_put_spread",
            direction="short",
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
            net_credit=round(-net_debit, 2),
            expiry_days=expiry_days,
            delta_exposure=round(delta * contracts, 4),
            theta_exposure=round(theta, 4),
            vega_exposure=round(vega, 4),
            earnings_warning=earnings_warning,
            uses_real_data=uses_real,
        )

    def _iron_condor(
        self, score: EnsembleScore, price: float, iv: float, expiry_days: int,
        earnings_warning: bool, chain_data: list[dict] | None,
    ) -> SpreadRecommendation | None:
        """Iron condor: sell OTM put + OTM call, buy further OTM put + call. Credit spread."""
        t = expiry_days / 365.0

        # Target strikes
        target_sell_put = round(price * 0.95, 2)
        target_buy_put = round(price * 0.90, 2)
        target_sell_call = round(price * 1.05, 2)
        target_buy_call = round(price * 1.10, 2)

        sell_put_premium, sell_put_strike, sp_real = self._get_premium(chain_data, target_sell_put, "put", price, iv, t)
        buy_put_premium, buy_put_strike, bp_real = self._get_premium(chain_data, target_buy_put, "put", price, iv, t)
        sell_call_premium, sell_call_strike, sc_real = self._get_premium(chain_data, target_sell_call, "call", price, iv, t)
        buy_call_premium, buy_call_strike, bc_real = self._get_premium(chain_data, target_buy_call, "call", price, iv, t)
        uses_real = sp_real and bp_real and sc_real and bc_real

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
        contracts = int(effective_max / max_loss_per_contract)
        if contracts < 1:
            return None

        net_credit = net_credit_per_share * 100 * contracts
        max_loss = max_loss_per_contract * contracts

        return SpreadRecommendation(
            ticker=score.ticker,
            strategy_name="iron_condor",
            direction="neutral",
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
            delta_exposure=0.0,
            theta_exposure=round(0.02 * contracts, 4),
            vega_exposure=round(-0.08 * contracts, 4),
            earnings_warning=earnings_warning,
            uses_real_data=uses_real,
        )

    def suggest_bull_spread(
        self,
        score: EnsembleScore,
        current_price: float,
        implied_vol: float = 0.30,
        expiry_days: int = 30,
        earnings_date: date | None = None,
        chain_data: list[dict] | None = None,
    ) -> SpreadRecommendation | None:
        """Suggest the best bullish spread strategy based on signals.

        - High directional + low vol -> bull put credit spread
        - High directional + high vol -> bull call debit spread
        - Default for moderate confidence -> bull call debit spread
        """
        if current_price <= 0:
            return None

        earnings_warning = False
        if earnings_date:
            days_to_earnings = (earnings_date - date.today()).days
            if 0 < days_to_earnings < expiry_days:
                earnings_warning = True

        # Bull-spread routing uses rise-side calibrated probability scale.
        # `_rise_lift_floor` defaults to ~0.228 (rise base 0.175 × lift 1.3).
        high_dir = score.directional_signal > self._rise_lift_floor
        high_vol = score.volatility_signal >= 0.5

        if high_dir and not high_vol:
            return self._bull_put_credit_spread(score, current_price, implied_vol, expiry_days, earnings_warning, chain_data)
        elif high_dir and high_vol:
            return self._bull_call_debit_spread(score, current_price, implied_vol, expiry_days, earnings_warning, chain_data)
        elif score.score >= self.min_score:
            return self._bull_call_debit_spread(score, current_price, implied_vol, expiry_days, earnings_warning, chain_data)

        return None

    def _bull_call_debit_spread(
        self, score: EnsembleScore, price: float, iv: float, expiry_days: int,
        earnings_warning: bool, chain_data: list[dict] | None,
    ) -> SpreadRecommendation | None:
        """Bull call debit spread: buy lower-strike call (near ATM), sell higher-strike call OTM."""
        t = expiry_days / 365.0

        target_buy_strike = round(price * 1.02, 2)
        target_sell_strike = round(price * 1.07, 2)

        buy_premium, buy_strike, buy_real = self._get_premium(chain_data, target_buy_strike, "call", price, iv, t)
        sell_premium, sell_strike, sell_real = self._get_premium(chain_data, target_sell_strike, "call", price, iv, t)
        uses_real = buy_real and sell_real

        spread_width = sell_strike - buy_strike
        if spread_width <= 0:
            return None

        net_debit_per_share = buy_premium - sell_premium
        if net_debit_per_share <= 0:
            return None

        cost_per_contract = net_debit_per_share * 100
        max_profit_per_contract = (spread_width - net_debit_per_share) * 100

        if cost_per_contract <= 0 or max_profit_per_contract <= 0:
            return None

        confidence_scale = min(score.score * 2, 1.0)
        effective_max = self.max_position * confidence_scale
        contracts = int(effective_max / cost_per_contract)
        if contracts < 1:
            return None

        net_debit = cost_per_contract * contracts
        max_profit = max_profit_per_contract * contracts
        max_loss = net_debit

        buy_iv = self._get_iv_from_chain(chain_data, buy_strike, "call", iv)
        sell_iv = self._get_iv_from_chain(chain_data, sell_strike, "call", iv)
        delta = self._estimate_delta(price, buy_strike, buy_iv, t, "call") - self._estimate_delta(price, sell_strike, sell_iv, t, "call")
        theta = -0.01 * contracts
        vega = 0.03 * contracts

        return SpreadRecommendation(
            ticker=score.ticker,
            strategy_name="bull_call_debit_spread",
            direction="long",
            score=score.score,
            directional_signal=score.directional_signal,
            volatility_signal=score.volatility_signal,
            sentiment_signal=score.sentiment_signal,
            current_price=price,
            legs=[
                SpreadLeg(option_type="call", action="buy", strike=buy_strike, premium=round(buy_premium, 2), contracts=contracts),
                SpreadLeg(option_type="call", action="sell", strike=sell_strike, premium=round(sell_premium, 2), contracts=contracts),
            ],
            max_profit=round(max_profit, 2),
            max_loss=round(max_loss, 2),
            breakeven=round(buy_strike + net_debit_per_share, 2),
            risk_reward_ratio=round(max_profit / max_loss, 4) if max_loss > 0 else 0,
            contracts=contracts,
            net_credit=round(-net_debit, 2),
            expiry_days=expiry_days,
            delta_exposure=round(delta * contracts, 4),
            theta_exposure=round(theta, 4),
            vega_exposure=round(vega, 4),
            earnings_warning=earnings_warning,
            uses_real_data=uses_real,
        )

    def _bull_put_credit_spread(
        self, score: EnsembleScore, price: float, iv: float, expiry_days: int,
        earnings_warning: bool, chain_data: list[dict] | None,
    ) -> SpreadRecommendation | None:
        """Bull put credit spread: sell higher-strike put (near ATM), buy lower-strike put OTM."""
        t = expiry_days / 365.0

        target_sell_strike = round(price * 0.98, 2)
        target_buy_strike = round(price * 0.93, 2)

        sell_premium, sell_strike, sell_real = self._get_premium(chain_data, target_sell_strike, "put", price, iv, t)
        buy_premium, buy_strike, buy_real = self._get_premium(chain_data, target_buy_strike, "put", price, iv, t)
        uses_real = sell_real and buy_real

        spread_width = sell_strike - buy_strike
        if spread_width <= 0:
            return None

        net_credit_per_share = sell_premium - buy_premium
        if net_credit_per_share <= 0:
            return None

        max_loss_per_contract = (spread_width - net_credit_per_share) * 100
        if max_loss_per_contract <= 0:
            return None

        confidence_scale = min(score.score * 2, 1.0)
        effective_max = self.max_position * confidence_scale
        contracts = int(effective_max / max_loss_per_contract)
        if contracts < 1:
            return None

        net_credit = net_credit_per_share * 100 * contracts
        max_loss = max_loss_per_contract * contracts
        max_profit = net_credit

        sell_iv = self._get_iv_from_chain(chain_data, sell_strike, "put", iv)
        buy_iv = self._get_iv_from_chain(chain_data, buy_strike, "put", iv)
        # Bull put: net delta is positive (selling put has positive delta, buying lower put has less negative delta)
        delta = -self._estimate_delta(price, sell_strike, sell_iv, t, "put") + self._estimate_delta(price, buy_strike, buy_iv, t, "put")
        theta = 0.01 * contracts
        vega = -0.05 * contracts

        return SpreadRecommendation(
            ticker=score.ticker,
            strategy_name="bull_put_credit_spread",
            direction="long",
            score=score.score,
            directional_signal=score.directional_signal,
            volatility_signal=score.volatility_signal,
            sentiment_signal=score.sentiment_signal,
            current_price=price,
            legs=[
                SpreadLeg(option_type="put", action="sell", strike=sell_strike, premium=round(sell_premium, 2), contracts=contracts),
                SpreadLeg(option_type="put", action="buy", strike=buy_strike, premium=round(buy_premium, 2), contracts=contracts),
            ],
            max_profit=round(max_profit, 2),
            max_loss=round(max_loss, 2),
            breakeven=round(sell_strike - net_credit_per_share, 2),
            risk_reward_ratio=round(max_profit / max_loss, 4) if max_loss > 0 else 0,
            contracts=contracts,
            net_credit=round(net_credit, 2),
            expiry_days=expiry_days,
            delta_exposure=round(delta * contracts, 4),
            theta_exposure=round(theta, 4),
            vega_exposure=round(vega, 4),
            earnings_warning=earnings_warning,
            uses_real_data=uses_real,
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
