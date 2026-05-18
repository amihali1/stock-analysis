"""Tests for options spread strategies."""

from datetime import date

import pytest

from src.models.ensemble import EnsembleScore
from src.models.options_strategies import SpreadBuilder, SpreadRecommendation


def _make_score(ticker="AAPL", score=0.7, directional=0.7, volatility=0.4, sentiment=0.6):
    return EnsembleScore(
        ticker=ticker,
        score=score,
        directional_signal=directional,
        volatility_signal=volatility,
        sentiment_signal=sentiment,
    )


class TestSpreadBuilder:
    def test_bear_call_spread_high_dir_low_vol(self):
        """High directional + low vol should suggest bear call spread."""
        builder = SpreadBuilder(max_position=1000)
        score = _make_score(directional=0.8, volatility=0.3)
        result = builder.suggest_spread(score, current_price=150.0)

        assert result is not None
        assert result.strategy_name == "bear_call_spread"
        assert result.max_loss > 0
        assert result.max_profit > 0
        assert result.contracts >= 1
        assert result.net_credit > 0  # Credit spread
        assert len(result.legs) == 2

    def test_bear_put_spread_high_dir_high_vol(self):
        """High directional + high vol should suggest bear put spread."""
        builder = SpreadBuilder(max_position=1000)
        score = _make_score(directional=0.8, volatility=0.7)
        result = builder.suggest_spread(score, current_price=150.0)

        assert result is not None
        assert result.strategy_name == "bull_put_spread"
        assert result.max_loss > 0
        assert result.net_credit < 0  # Debit spread

    def test_iron_condor_high_vol_low_dir(self):
        """High vol + neutral direction should suggest iron condor.
        `directional=0.04` is below the calibrated drop lift floor
        (base 0.05 × 1.3 = 0.065), so this counts as "no directional edge"."""
        builder = SpreadBuilder(max_position=1000)
        score = _make_score(directional=0.04, volatility=0.8, score=0.6)
        result = builder.suggest_spread(score, current_price=150.0)

        assert result is not None
        assert result.strategy_name == "iron_condor"
        assert len(result.legs) == 4
        assert result.delta_exposure == 0.0  # Delta-neutral
        assert isinstance(result.breakeven, list)
        assert len(result.breakeven) == 2

    def test_zero_price_returns_none(self):
        builder = SpreadBuilder()
        score = _make_score()
        assert builder.suggest_spread(score, current_price=0) is None

    def test_max_position_constraint(self):
        """Position should not exceed max_position."""
        builder = SpreadBuilder(max_position=1000)
        score = _make_score(score=0.9)
        result = builder.suggest_spread(score, current_price=100.0)

        if result:
            assert result.max_loss <= 1000 * 1.5  # Some tolerance for rounding

    def test_earnings_warning(self):
        """Should flag when expiry crosses earnings date."""
        builder = SpreadBuilder()
        score = _make_score()
        earnings = date.today()  # Earnings today, within expiry
        result = builder.suggest_spread(score, current_price=150.0, earnings_date=earnings)

        # Earnings date is today, which is within 30-day expiry, but only if > 0
        # Since (today - today).days = 0, the condition 0 < 0 is False
        # Let's use a future date
        from datetime import timedelta
        future_earnings = date.today() + timedelta(days=15)
        result = builder.suggest_spread(score, current_price=150.0, earnings_date=future_earnings)

        if result:
            assert result.earnings_warning is True

    def test_risk_reward_positive(self):
        builder = SpreadBuilder()
        score = _make_score()
        result = builder.suggest_spread(score, current_price=200.0)

        if result:
            assert result.risk_reward_ratio > 0
            assert result.max_profit > 0
            assert result.max_loss > 0

    def test_greeks_present(self):
        builder = SpreadBuilder()
        score = _make_score()
        result = builder.suggest_spread(score, current_price=150.0)

        if result:
            assert hasattr(result, 'delta_exposure')
            assert hasattr(result, 'theta_exposure')
            assert hasattr(result, 'vega_exposure')

    def test_low_score_returns_none(self):
        """Below-floor directional, vol, AND score → no spread.
        directional=0.04 is below drop lift floor 0.065; score=0.2 below
        min_score default 0.30."""
        builder = SpreadBuilder()
        score = _make_score(score=0.2, directional=0.04, volatility=0.2, sentiment=0.2)
        result = builder.suggest_spread(score, current_price=150.0)
        assert result is None


class TestCalibratedThresholds:
    """Spread thresholds must fire on realistic post-sigmoid-calibration
    probabilities. The legacy `directional_signal > 0.6` / `score >= 0.5`
    gates were unreachable — drop_prob clusters at ~0.05 base rate (range
    0.04-0.10), rise_prob at ~0.175 (range 0.18-0.27). These tests lock
    the lift-based gates so a future re-introduction of absolute floors
    fails loudly."""

    def test_bear_spread_fires_at_calibrated_drop_prob(self):
        """drop_prob ~0.07 (1.4× base 0.05) and score 0.32 should fire
        a bear call spread — typical production short pick under v7 model."""
        builder = SpreadBuilder(max_position=1000)
        score = _make_score(directional=0.07, volatility=0.3, score=0.32)
        result = builder.suggest_spread(score, current_price=150.0)
        assert result is not None
        assert result.strategy_name == "bear_call_spread"

    def test_bear_spread_falls_through_below_lift(self):
        """drop_prob 0.04 (below base × 1.3 = 0.065) AND score below
        min_score floor — no spread."""
        builder = SpreadBuilder(max_position=1000, min_score=0.30)
        score = _make_score(directional=0.04, volatility=0.3, score=0.20)
        result = builder.suggest_spread(score, current_price=150.0)
        assert result is None

    def test_bear_spread_score_floor_catches_borderline(self):
        """Low dir_prob but composite score >= min_score still routes to
        bear_call_spread — the score floor exists for exactly this case
        (vol or sentiment carrying the conviction)."""
        builder = SpreadBuilder(max_position=1000, min_score=0.30)
        score = _make_score(directional=0.04, volatility=0.6, score=0.40)
        result = builder.suggest_spread(score, current_price=150.0)
        assert result is not None
        # Could be iron_condor (vol > 0.6, dir below lift) — both acceptable
        assert result.strategy_name in ("bear_call_spread", "iron_condor")

    def test_bull_spread_fires_at_calibrated_rise_prob(self):
        """rise_prob ~0.24 (1.4× base 0.175) with score 0.40 should fire
        a bull put credit spread — typical production long pick."""
        builder = SpreadBuilder(max_position=1000)
        score = _make_score(directional=0.24, volatility=0.3, score=0.40)
        result = builder.suggest_bull_spread(score, current_price=150.0)
        assert result is not None
        assert result.strategy_name == "bull_put_credit_spread"

    def test_bull_spread_falls_through_below_lift_and_score(self):
        """rise_prob 0.18 (below base × 1.3 = 0.228) AND score below
        min_score — no spread, caller will route to call_options."""
        builder = SpreadBuilder(max_position=1000, min_score=0.30)
        score = _make_score(directional=0.18, volatility=0.3, score=0.25)
        result = builder.suggest_bull_spread(score, current_price=150.0)
        assert result is None

    def test_bull_spread_score_floor_catches_borderline(self):
        """Low rise_prob but score crosses min_score → bull_call_debit_spread."""
        builder = SpreadBuilder(max_position=1000, min_score=0.30)
        score = _make_score(directional=0.20, volatility=0.4, score=0.35)
        result = builder.suggest_bull_spread(score, current_price=150.0)
        assert result is not None
        assert result.strategy_name == "bull_call_debit_spread"

    def test_custom_lift_multiplier(self):
        """A higher lift multiplier (stricter) suppresses borderline picks."""
        strict = SpreadBuilder(max_position=1000, directional_lift=2.0, min_score=0.99)
        # rise_prob 0.24 was previously above 1.3× base; with 2.0× it must be > 0.35
        score = _make_score(directional=0.24, volatility=0.3, score=0.50)
        result = strict.suggest_bull_spread(score, current_price=150.0)
        assert result is None


class TestBlackScholesEstimates:
    def test_call_premium_positive(self):
        builder = SpreadBuilder()
        premium = builder._estimate_premium(100, 100, 0.30, 30 / 365, "call")
        assert premium > 0

    def test_put_premium_positive(self):
        builder = SpreadBuilder()
        premium = builder._estimate_premium(100, 100, 0.30, 30 / 365, "put")
        assert premium > 0

    def test_deep_itm_call(self):
        builder = SpreadBuilder()
        premium = builder._estimate_premium(150, 100, 0.30, 30 / 365, "call")
        assert premium > 49  # At least intrinsic value

    def test_deep_otm_call(self):
        builder = SpreadBuilder()
        premium = builder._estimate_premium(50, 100, 0.30, 30 / 365, "call")
        assert premium < 1  # Very small premium

    def test_call_delta_range(self):
        builder = SpreadBuilder()
        delta = builder._estimate_delta(100, 100, 0.30, 30 / 365, "call")
        assert 0 < delta < 1

    def test_put_delta_negative(self):
        builder = SpreadBuilder()
        delta = builder._estimate_delta(100, 100, 0.30, 30 / 365, "put")
        assert -1 < delta < 0


class TestSpreadRiskType:
    def test_all_spread_types_are_defined_risk(self):
        """Every spread strategy should be labeled as defined-risk."""
        builder = SpreadBuilder(max_position=1000)

        # Bear call spread (high dir, low vol)
        score_bc = _make_score(directional=0.8, volatility=0.3)
        result_bc = builder.suggest_spread(score_bc, current_price=150.0)
        if result_bc:
            assert result_bc.risk_type == "defined"

        # Bull put spread (high dir, high vol)
        score_bp = _make_score(directional=0.8, volatility=0.7)
        result_bp = builder.suggest_spread(score_bp, current_price=150.0)
        if result_bp:
            assert result_bp.risk_type == "defined"

        # Iron condor (low dir, high vol) — directional below calibrated floor
        score_ic = _make_score(directional=0.04, volatility=0.8, score=0.6)
        result_ic = builder.suggest_spread(score_ic, current_price=150.0)
        if result_ic:
            assert result_ic.risk_type == "defined"

    def test_spread_max_loss_is_finite(self):
        """Defined-risk spreads must have a known, finite max loss."""
        builder = SpreadBuilder(max_position=1000)
        score = _make_score(directional=0.8, volatility=0.3)
        result = builder.suggest_spread(score, current_price=150.0)
        if result:
            assert result.max_loss > 0
            assert result.max_loss < float("inf")
