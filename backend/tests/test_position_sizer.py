"""Tests for ensemble scorer and position sizer."""

import pytest

from src.models.ensemble import Ensemble, SignalInputs, EnsembleScore
from src.models.position_sizer import PositionSizer, ShortRecommendation, OptionsRecommendation, LongRecommendation


@pytest.fixture
def ensemble():
    return Ensemble()


@pytest.fixture
def sizer():
    return PositionSizer(max_position=1000.0)


def make_inputs(**overrides) -> SignalInputs:
    defaults = {
        "ticker": "AAPL",
        "drop_prob": 0.7,
        "rise_prob": 0.0,
        "predicted_vol": 0.35,
        "sentiment_score": -0.5,
        "sentiment_confidence": 0.8,
        "current_price": 150.0,
    }
    defaults.update(overrides)
    return SignalInputs(**defaults)


def _bear(scores):
    """Return the bearish EnsembleScore from a list returned by Ensemble.score()."""
    return next(s for s in scores if s.direction == "drop")


class TestEnsemble:
    def test_bearish_signals_produce_high_score(self, ensemble):
        inputs = make_inputs(drop_prob=0.9, sentiment_score=-0.8, predicted_vol=0.5)
        result = _bear(ensemble.score(inputs))
        assert result.score > 0.6

    def test_bullish_signals_produce_low_score(self, ensemble):
        inputs = make_inputs(drop_prob=0.1, sentiment_score=0.8, predicted_vol=0.1)
        result = _bear(ensemble.score(inputs))
        assert result.score < 0.3

    def test_neutral_signals(self, ensemble):
        inputs = make_inputs(drop_prob=0.5, sentiment_score=0.0, predicted_vol=0.3)
        result = _bear(ensemble.score(inputs))
        assert 0.2 < result.score < 0.6

    def test_custom_weights(self):
        ensemble = Ensemble(weight_directional=1.0, weight_volatility=0.0, weight_sentiment=0.0)
        inputs = make_inputs(drop_prob=0.8)
        result = _bear(ensemble.score(inputs))
        assert abs(result.score - 0.8) < 0.01

    def test_output_has_all_signals(self, ensemble):
        result = _bear(ensemble.score(make_inputs()))
        assert result.ticker == "AAPL"
        assert 0 <= result.directional_signal <= 1
        assert 0 <= result.volatility_signal <= 1
        assert 0 <= result.sentiment_signal <= 1

    def test_returns_both_directions(self, ensemble):
        scores = ensemble.score(make_inputs(drop_prob=0.7, rise_prob=0.2))
        assert len(scores) == 2
        directions = {s.direction for s in scores}
        assert directions == {"drop", "rise"}

    def test_bullish_branch_rewards_positive_sentiment(self, ensemble):
        # Same rise_prob, positive vs negative sentiment — bull score should rise with +sent.
        pos = next(s for s in ensemble.score(make_inputs(rise_prob=0.7, sentiment_score=0.8))
                   if s.direction == "rise")
        neg = next(s for s in ensemble.score(make_inputs(rise_prob=0.7, sentiment_score=-0.8))
                   if s.direction == "rise")
        assert pos.score > neg.score

    def test_bearish_branch_rewards_negative_sentiment(self, ensemble):
        # Same drop_prob, mirror polarity for the bear branch.
        neg = _bear(ensemble.score(make_inputs(drop_prob=0.7, sentiment_score=-0.8)))
        pos = _bear(ensemble.score(make_inputs(drop_prob=0.7, sentiment_score=0.8)))
        assert neg.score > pos.score

    def test_meets_confidence_when_sentiment_clears(self):
        # Post-2026-05-12: directional_lift component dropped. Only sentiment floor applies.
        ensemble = Ensemble(min_sentiment_confidence=0.40)
        inputs = make_inputs(drop_prob=0.30, sentiment_confidence=0.85)
        result = _bear(ensemble.score(inputs))
        assert result.meets_confidence is True

    def test_low_dir_prob_no_longer_fails_meets_confidence(self):
        # Under sigmoid calibration, dir_prob clusters tightly around base rate.
        # Absolute thresholds on dir_prob are noise — gate is sentiment-only.
        ensemble = Ensemble(min_sentiment_confidence=0.40)
        inputs = make_inputs(drop_prob=0.10, sentiment_confidence=0.85)
        result = _bear(ensemble.score(inputs))
        assert result.meets_confidence is True

    def test_fails_confidence_when_sentiment_confidence_low(self):
        ensemble = Ensemble(min_sentiment_confidence=0.40)
        inputs = make_inputs(drop_prob=0.30, sentiment_confidence=0.20)
        result = _bear(ensemble.score(inputs))
        assert result.meets_confidence is False

    def test_sentiment_at_exact_threshold(self):
        ensemble = Ensemble(min_sentiment_confidence=0.40)
        inputs = make_inputs(drop_prob=0.30, sentiment_confidence=0.40)
        result = _bear(ensemble.score(inputs))
        assert result.meets_confidence is True

    def test_vol_does_not_affect_meets_confidence(self):
        ensemble = Ensemble(min_sentiment_confidence=0.40)
        low_vol = make_inputs(drop_prob=0.30, sentiment_confidence=0.85, predicted_vol=0.05)
        high_vol = make_inputs(drop_prob=0.30, sentiment_confidence=0.85, predicted_vol=0.9)
        assert _bear(ensemble.score(low_vol)).meets_confidence is True
        assert _bear(ensemble.score(high_vol)).meets_confidence is True

    def test_legacy_min_confidence_kwarg_maps_to_sentiment_floor(self):
        # Back-compat: passing min_confidence sets the sentiment floor only.
        ensemble = Ensemble(min_confidence=0.90)
        # sentiment_confidence below the legacy 0.90 floor → fails
        inputs = make_inputs(drop_prob=0.30, sentiment_confidence=0.85)
        assert _bear(ensemble.score(inputs)).meets_confidence is False
        # sentiment_confidence above 0.90 → passes
        inputs2 = make_inputs(drop_prob=0.30, sentiment_confidence=0.95)
        assert _bear(ensemble.score(inputs2)).meets_confidence is True


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

    def test_chain_data_overrides_bs_estimate(self, sizer):
        """When chain_data is provided, premium and strike come from chain."""
        score = EnsembleScore(
            ticker="AAPL", score=0.8, directional_signal=0.8,
            volatility_signal=0.5, sentiment_signal=0.7,
        )
        chain = [
            {"strike": 187.5, "option_type": "put", "bid": 2.50, "ask": 2.70, "last": 2.60},
            {"strike": 190.0, "option_type": "put", "bid": 3.00, "ask": 3.20, "last": 3.10},
            {"strike": 192.5, "option_type": "put", "bid": 3.60, "ask": 3.80, "last": 3.70},
            {"strike": 190.0, "option_type": "call", "bid": 1.10, "ask": 1.30, "last": 1.20},
        ]
        rec = sizer.size_options(
            score, current_price=200.0, strike_offset_pct=0.05,
            chain_data=chain,
        )
        assert rec is not None
        # Target = 190.0; nearest put = 190.0; premium = (3.00+3.20)/2 = 3.10
        assert rec.strike == 190.0
        assert rec.premium_per_contract == 310.0  # 3.10 * 100

    def test_chain_data_nearest_strike(self, sizer):
        """Chain lookup snaps to nearest actual chain strike, not BS-snap."""
        score = EnsembleScore(
            ticker="AAPL", score=0.8, directional_signal=0.8,
            volatility_signal=0.5, sentiment_signal=0.7,
        )
        # Target = 200 * 0.95 = 190.0. Chain has 187.5 and 192.5 only.
        chain = [
            {"strike": 187.5, "option_type": "put", "bid": 2.50, "ask": 2.70, "last": 0},
            {"strike": 192.5, "option_type": "put", "bid": 3.60, "ask": 3.80, "last": 0},
        ]
        rec = sizer.size_options(
            score, current_price=200.0, strike_offset_pct=0.05, chain_data=chain,
        )
        assert rec is not None
        # 190 is equidistant; min() picks first in tie. Either 187.5 or 192.5 acceptable.
        assert rec.strike in (187.5, 192.5)

    def test_chain_data_call_picks_call_contract(self, sizer):
        score = EnsembleScore(
            ticker="AAPL", score=0.8, directional_signal=0.8,
            volatility_signal=0.5, sentiment_signal=0.7,
        )
        chain = [
            {"strike": 210.0, "option_type": "put", "bid": 12.0, "ask": 12.2, "last": 12.1},
            {"strike": 210.0, "option_type": "call", "bid": 1.40, "ask": 1.60, "last": 1.50},
        ]
        rec = sizer.size_options(
            score, current_price=200.0, strike_offset_pct=0.05,
            option_type="call", chain_data=chain,
        )
        assert rec is not None
        assert rec.option_type == "call"
        assert rec.strike == 210.0
        assert rec.premium_per_contract == 150.0  # mid of call

    def test_chain_data_empty_falls_back_to_bs(self, sizer):
        score = EnsembleScore(
            ticker="AAPL", score=0.8, directional_signal=0.8,
            volatility_signal=0.5, sentiment_signal=0.7,
        )
        rec = sizer.size_options(
            score, current_price=200.0, strike_offset_pct=0.05, chain_data=[],
        )
        assert rec is not None
        assert rec.strike == 190.0  # _snap_strike fallback

    def test_chain_data_no_matching_option_type_falls_back(self, sizer):
        score = EnsembleScore(
            ticker="AAPL", score=0.8, directional_signal=0.8,
            volatility_signal=0.5, sentiment_signal=0.7,
        )
        # Chain has only calls; size_options default is put → fallback.
        chain = [
            {"strike": 190.0, "option_type": "call", "bid": 1.0, "ask": 1.2, "last": 1.1},
        ]
        rec = sizer.size_options(
            score, current_price=200.0, strike_offset_pct=0.05, chain_data=chain,
        )
        assert rec is not None
        assert rec.strike == 190.0


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


class TestPositionSizerLong:
    def test_basic_long(self, sizer):
        score = EnsembleScore(ticker="AAPL", score=0.8, directional_signal=0.8, volatility_signal=0.3, sentiment_signal=0.7)
        rec = sizer.size_long(score, current_price=50.0)
        assert rec is not None
        assert rec.strategy == "long"
        assert rec.direction == "long"
        assert rec.position_size <= 1000.0
        assert rec.shares >= 1
        assert rec.stop_loss < rec.entry_price  # Stop below entry for long
        assert rec.target_price > rec.entry_price  # Target above entry for long
        assert rec.max_loss > 0
        assert rec.risk_type == "defined"

    def test_long_no_margin_multiplier(self, sizer):
        """Long shares = max_position / price (no 1.5x like shorts)."""
        score = EnsembleScore(ticker="X", score=1.0, directional_signal=1.0, volatility_signal=0.5, sentiment_signal=0.7)
        long_rec = sizer.size_long(score, current_price=100.0)
        short_rec = sizer.size_short(score, current_price=100.0)
        # Long gets ~10 shares at $1000/$100, short gets ~6 (1000 / (100*1.5))
        assert long_rec.shares > short_rec.shares

    def test_long_zero_price(self, sizer):
        score = EnsembleScore(ticker="X", score=0.8, directional_signal=0.8, volatility_signal=0.3, sentiment_signal=0.7)
        assert sizer.size_long(score, current_price=0) is None

    def test_long_expensive_stock(self, sizer):
        score = EnsembleScore(ticker="BRK", score=0.8, directional_signal=0.8, volatility_signal=0.3, sentiment_signal=0.7)
        rec = sizer.size_long(score, current_price=50000.0)
        assert rec is None


class TestPositionSizerCallOptions:
    def test_call_strike_above_current(self, sizer):
        score = EnsembleScore(ticker="AAPL", score=0.8, directional_signal=0.8, volatility_signal=0.5, sentiment_signal=0.7)
        rec = sizer.size_options(score, current_price=200.0, strike_offset_pct=0.05, option_type="call")
        assert rec is not None
        assert rec.strike == 210.0  # 200 * 1.05
        assert rec.option_type == "call"
        assert rec.direction == "long"

    def test_put_strike_below_current(self, sizer):
        score = EnsembleScore(ticker="AAPL", score=0.8, directional_signal=0.8, volatility_signal=0.5, sentiment_signal=0.7)
        rec = sizer.size_options(score, current_price=200.0, strike_offset_pct=0.05, option_type="put")
        assert rec.strike == 190.0
        assert rec.option_type == "put"
        assert rec.direction == "short"

    def test_invalid_option_type(self, sizer):
        score = EnsembleScore(ticker="AAPL", score=0.8, directional_signal=0.8, volatility_signal=0.5, sentiment_signal=0.7)
        with pytest.raises(ValueError):
            sizer.size_options(score, current_price=100.0, option_type="straddle")


class TestSizeBullSpread:
    def test_basic_bull_spread(self, sizer):
        score = EnsembleScore(ticker="AAPL", score=0.7, directional_signal=0.8, volatility_signal=0.3, sentiment_signal=0.6)
        rec = sizer.size_bull_spread(score, current_price=150.0)
        assert rec is not None
        assert rec.direction == "long"
        assert rec.strategy_name in ("bull_call_debit_spread", "bull_put_credit_spread")
        assert rec.risk_type == "defined"
        assert rec.max_loss <= 1000.0

    def test_low_vol_picks_bull_put_credit(self, sizer):
        score = EnsembleScore(ticker="X", score=0.7, directional_signal=0.8, volatility_signal=0.2, sentiment_signal=0.6)
        rec = sizer.size_bull_spread(score, current_price=100.0)
        assert rec is not None
        assert rec.strategy_name == "bull_put_credit_spread"
        # Credit spread: net_credit positive
        assert rec.net_credit > 0

    def test_high_vol_picks_bull_call_debit(self, sizer):
        score = EnsembleScore(ticker="X", score=0.7, directional_signal=0.8, volatility_signal=0.7, sentiment_signal=0.6)
        rec = sizer.size_bull_spread(score, current_price=100.0)
        assert rec is not None
        assert rec.strategy_name == "bull_call_debit_spread"
        # Debit spread: net_credit negative
        assert rec.net_credit < 0

    def test_low_score_returns_none(self, sizer):
        """Sub-floor directional AND sub-floor score → no bull spread.
        Post-sigmoid calibration rise_prob clusters at base 0.175 (range
        ~0.18-0.27); the lift floor (base × 1.3 = 0.228) plus the calibrated
        score floor (default 0.30) are what now suppress weak picks."""
        score = EnsembleScore(
            ticker="X", score=0.20, directional_signal=0.18,
            volatility_signal=0.3, sentiment_signal=0.5,
        )
        assert sizer.size_bull_spread(score, current_price=100.0) is None

    def test_bull_put_credit_max_loss_within_budget(self, sizer):
        """Credit spread max_loss (buying-power reduction) must stay within max_position."""
        score = EnsembleScore(ticker="X", score=0.7, directional_signal=0.8, volatility_signal=0.2, sentiment_signal=0.6)
        rec = sizer.size_bull_spread(score, current_price=100.0)
        assert rec is not None
        assert rec.strategy_name == "bull_put_credit_spread"
        assert rec.max_loss <= 1000.0

    def test_earnings_within_expiry_sets_warning(self):
        """SpreadBuilder.suggest_bull_spread flags earnings that fall before expiry."""
        from datetime import date, timedelta
        from src.models.options_strategies import SpreadBuilder

        builder = SpreadBuilder(max_position=1000.0)
        score = EnsembleScore(ticker="X", score=0.7, directional_signal=0.8, volatility_signal=0.3, sentiment_signal=0.6)
        earnings = date.today() + timedelta(days=15)
        rec = builder.suggest_bull_spread(score, current_price=100.0, expiry_days=30, earnings_date=earnings)
        assert rec is not None
        assert rec.earnings_warning is True

    def test_earnings_after_expiry_no_warning(self):
        """Earnings beyond the expiry window does not trigger the warning."""
        from datetime import date, timedelta
        from src.models.options_strategies import SpreadBuilder

        builder = SpreadBuilder(max_position=1000.0)
        score = EnsembleScore(ticker="X", score=0.7, directional_signal=0.8, volatility_signal=0.3, sentiment_signal=0.6)
        earnings = date.today() + timedelta(days=45)
        rec = builder.suggest_bull_spread(score, current_price=100.0, expiry_days=30, earnings_date=earnings)
        assert rec is not None
        assert rec.earnings_warning is False


class TestDirectionFields:
    """Verify direction tagging on all recommendation types."""

    def test_short_direction(self, sizer):
        score = EnsembleScore(ticker="X", score=0.8, directional_signal=0.8, volatility_signal=0.3, sentiment_signal=0.7)
        rec = sizer.size_short(score, current_price=50.0)
        assert rec.direction == "short"

    def test_bear_spread_direction(self, sizer):
        score = EnsembleScore(ticker="X", score=0.7, directional_signal=0.8, volatility_signal=0.3, sentiment_signal=0.6)
        rec = sizer.size_spread(score, current_price=150.0)
        assert rec is not None
        assert rec.direction == "short"
