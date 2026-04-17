"""Tests for ensemble scorer and position sizer."""

import pytest

from src.models.ensemble import Ensemble, SignalInputs, EnsembleScore
from src.models.position_sizer import PositionSizer, ShortRecommendation, OptionsRecommendation


@pytest.fixture
def ensemble():
    return Ensemble()


@pytest.fixture
def sizer():
    return PositionSizer(max_position=1000.0)


def make_inputs(**overrides) -> SignalInputs:
    defaults = {
        "ticker": "AAPL",
        "directional_prob": 0.7,
        "directional_confidence": 0.8,
        "predicted_vol": 0.35,
        "sentiment_score": -0.5,
        "sentiment_confidence": 0.8,
        "current_price": 150.0,
    }
    defaults.update(overrides)
    return SignalInputs(**defaults)


class TestEnsemble:
    def test_bearish_signals_produce_high_score(self, ensemble):
        inputs = make_inputs(directional_prob=0.9, sentiment_score=-0.8, predicted_vol=0.5)
        result = ensemble.score(inputs)
        assert result.score > 0.6

    def test_bullish_signals_produce_low_score(self, ensemble):
        inputs = make_inputs(directional_prob=0.1, sentiment_score=0.8, predicted_vol=0.1)
        result = ensemble.score(inputs)
        assert result.score < 0.3

    def test_neutral_signals(self, ensemble):
        inputs = make_inputs(directional_prob=0.5, sentiment_score=0.0, predicted_vol=0.3)
        result = ensemble.score(inputs)
        assert 0.2 < result.score < 0.6

    def test_custom_weights(self):
        ensemble = Ensemble(weight_directional=1.0, weight_volatility=0.0, weight_sentiment=0.0)
        inputs = make_inputs(directional_prob=0.8)
        result = ensemble.score(inputs)
        assert abs(result.score - 0.8) < 0.01

    def test_output_has_all_signals(self, ensemble):
        result = ensemble.score(make_inputs())
        assert result.ticker == "AAPL"
        assert 0 <= result.directional_signal <= 1
        assert 0 <= result.volatility_signal <= 1
        assert 0 <= result.sentiment_signal <= 1

    def test_meets_confidence_when_all_signals_high(self):
        ensemble = Ensemble(min_confidence=0.5)
        inputs = make_inputs(directional_prob=0.9, sentiment_score=-0.8, predicted_vol=0.8, sentiment_confidence=0.9)
        result = ensemble.score(inputs)
        assert result.meets_confidence is True

    def test_fails_confidence_when_directional_low(self):
        ensemble = Ensemble(min_confidence=0.75)
        inputs = make_inputs(directional_prob=0.3, sentiment_score=-0.8, predicted_vol=0.8, sentiment_confidence=0.9)
        result = ensemble.score(inputs)
        assert result.meets_confidence is False

    def test_fails_confidence_when_sentiment_low(self):
        ensemble = Ensemble(min_confidence=0.75)
        inputs = make_inputs(directional_prob=0.9, sentiment_score=0.2, predicted_vol=0.8, sentiment_confidence=0.9)
        result = ensemble.score(inputs)
        assert result.meets_confidence is False

    def test_fails_confidence_when_volatility_low(self):
        ensemble = Ensemble(min_confidence=0.75)
        inputs = make_inputs(directional_prob=0.9, sentiment_score=-0.8, predicted_vol=0.1, sentiment_confidence=0.9)
        result = ensemble.score(inputs)
        assert result.meets_confidence is False

    def test_confidence_at_exact_threshold(self):
        ensemble = Ensemble(min_confidence=0.75)
        inputs = make_inputs(directional_prob=0.75, sentiment_score=-0.5, predicted_vol=0.75, sentiment_confidence=1.0)
        result = ensemble.score(inputs)
        assert result.meets_confidence is True


class TestPositionSizerShort:
    def test_basic_short(self, sizer):
        score = EnsembleScore(ticker="AAPL", score=0.8, directional_signal=0.8, volatility_signal=0.3, sentiment_signal=0.7)
        rec = sizer.size_short(score, current_price=150.0)
        assert rec is not None
        assert rec.strategy == "short"
        assert rec.position_size <= 1000.0
        assert rec.shares >= 1
        assert rec.stop_loss > rec.entry_price  # Stop-loss above entry for short
        assert rec.target_price < rec.entry_price  # Target below entry for short
        assert rec.max_loss > 0

    def test_max_position_respected(self, sizer):
        score = EnsembleScore(ticker="AAPL", score=1.0, directional_signal=1.0, volatility_signal=1.0, sentiment_signal=1.0)
        rec = sizer.size_short(score, current_price=100.0)
        assert rec.position_size <= 1000.0

    def test_expensive_stock(self, sizer):
        score = EnsembleScore(ticker="BRK", score=0.8, directional_signal=0.8, volatility_signal=0.3, sentiment_signal=0.7)
        # Stock too expensive for even 1 share margin
        rec = sizer.size_short(score, current_price=50000.0)
        assert rec is None

    def test_low_confidence_smaller_position(self, sizer):
        high_score = EnsembleScore(ticker="X", score=0.9, directional_signal=0.9, volatility_signal=0.5, sentiment_signal=0.8)
        low_score = EnsembleScore(ticker="X", score=0.3, directional_signal=0.3, volatility_signal=0.2, sentiment_signal=0.3)

        rec_high = sizer.size_short(high_score, current_price=50.0)
        rec_low = sizer.size_short(low_score, current_price=50.0)

        assert rec_high.shares > rec_low.shares

    def test_zero_price(self, sizer):
        score = EnsembleScore(ticker="X", score=0.8, directional_signal=0.8, volatility_signal=0.3, sentiment_signal=0.7)
        assert sizer.size_short(score, current_price=0) is None


class TestPositionSizerOptions:
    def test_basic_options(self, sizer):
        score = EnsembleScore(ticker="AAPL", score=0.8, directional_signal=0.8, volatility_signal=0.5, sentiment_signal=0.7)
        rec = sizer.size_options(score, current_price=150.0)
        assert rec is not None
        assert rec.strategy == "options"
        assert rec.option_type == "put"
        assert rec.position_size <= 1000.0
        assert rec.contracts >= 1
        assert rec.max_loss == rec.position_size  # Max loss = premium paid

    def test_max_position_respected(self, sizer):
        score = EnsembleScore(ticker="AAPL", score=1.0, directional_signal=1.0, volatility_signal=1.0, sentiment_signal=1.0)
        rec = sizer.size_options(score, current_price=100.0, premium_per_share=5.0)
        # 5 * 100 = $500/contract, max 2 contracts = $1000
        assert rec.position_size <= 1000.0

    def test_expensive_premium(self, sizer):
        score = EnsembleScore(ticker="X", score=0.8, directional_signal=0.8, volatility_signal=0.5, sentiment_signal=0.7)
        # Premium too expensive for even 1 contract
        rec = sizer.size_options(score, current_price=100.0, premium_per_share=100.0)
        assert rec is None  # $10,000/contract > $1,000 budget

    def test_strike_below_current_price(self, sizer):
        score = EnsembleScore(ticker="AAPL", score=0.8, directional_signal=0.8, volatility_signal=0.5, sentiment_signal=0.7)
        rec = sizer.size_options(score, current_price=200.0, strike_offset_pct=0.05)
        assert rec.strike == 190.0  # 200 * 0.95


class TestRiskType:
    """Verify risk_type labels on each strategy."""

    def test_short_is_undefined_risk(self):
        sizer = PositionSizer(max_position=1000.0)
        score = EnsembleScore(ticker="AAPL", score=0.8, directional_signal=0.8, volatility_signal=0.3, sentiment_signal=0.7)
        rec = sizer.size_short(score, current_price=50.0)
        assert rec is not None
        assert rec.risk_type == "undefined"

    def test_options_is_defined_risk(self):
        sizer = PositionSizer(max_position=1000.0)
        score = EnsembleScore(ticker="AAPL", score=0.8, directional_signal=0.8, volatility_signal=0.5, sentiment_signal=0.7)
        rec = sizer.size_options(score, current_price=50.0)
        assert rec is not None
        assert rec.risk_type == "defined"

    def test_spread_is_defined_risk(self):
        sizer = PositionSizer(max_position=1000.0)
        score = EnsembleScore(ticker="AAPL", score=0.7, directional_signal=0.8, volatility_signal=0.3, sentiment_signal=0.6)
        rec = sizer.size_spread(score, current_price=150.0)
        assert rec is not None
        assert rec.risk_type == "defined"

    def test_defined_risk_preferred_over_naked(self):
        """When spread is available, it should be preferred over naked short."""
        sizer = PositionSizer(max_position=1000.0)
        score = EnsembleScore(ticker="AAPL", score=0.7, directional_signal=0.8, volatility_signal=0.3, sentiment_signal=0.6)

        spread = sizer.size_spread(score, current_price=150.0)
        short = sizer.size_short(score, current_price=150.0)

        # Both should be available
        assert spread is not None
        assert short is not None
        # Spread is defined-risk, short is not
        assert spread.risk_type == "defined"
        assert short.risk_type == "undefined"
