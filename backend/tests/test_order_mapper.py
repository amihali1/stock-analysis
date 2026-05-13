"""Tests for OrderMapper."""

from src.services.order_mapper import OrderMapper


class TestShortMapping:
    def test_basic_short(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="short", entry_price=150.0,
            stop_loss=157.5, target_price=135.0, position_size=1000.0,
        )
        assert result is not None
        assert result.ticker == "AAPL"
        assert result.side == "sell"
        assert result.is_bracket is True
        assert result.stop_loss_price == 157.5
        assert result.take_profit_price == 135.0
        assert result.qty > 0

    def test_short_respects_max_position(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="short", entry_price=150.0,
            stop_loss=157.5, target_price=135.0, position_size=10000.0,
        )
        assert result is not None
        assert result.qty * result.limit_price * 1.5 <= 1000

    def test_short_insufficient_buying_power(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="short", entry_price=150.0,
            stop_loss=157.5, target_price=135.0, position_size=1000.0,
            buying_power=100.0,
        )
        assert result is None

    def test_short_zero_price(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="short", entry_price=0,
            stop_loss=10, target_price=0, position_size=1000.0,
        )
        assert result is None

    def test_dry_run_flag(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="short", entry_price=150.0,
            stop_loss=157.5, target_price=135.0, position_size=1000.0,
            dry_run=True,
        )
        assert result is not None
        assert result.dry_run is True


class TestOptionsMapping:
    def test_basic_options(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="options", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=2000.0,
            contracts=3, strike=145.0, option_type="put",
        )
        assert result is not None
        assert result.side == "buy"
        assert result.strategy == "options"
        assert result.qty == 3

    def test_options_no_contracts(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="options", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=2000.0,
            contracts=0,
        )
        assert result is None

    def test_options_insufficient_buying_power(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="options", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=2000.0,
            contracts=3, buying_power=10.0,
        )
        assert result is None


class TestLongMapping:
    def test_basic_long(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="long", entry_price=150.0,
            stop_loss=142.5, target_price=165.0, position_size=1000.0,
        )
        assert result is not None
        assert result.ticker == "AAPL"
        assert result.side == "buy"
        assert result.is_bracket is True
        assert result.stop_loss_price == 142.5
        assert result.take_profit_price == 165.0
        assert result.qty > 0
        assert result.strategy == "long"

    def test_long_respects_max_position(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="long", entry_price=150.0,
            stop_loss=142.5, target_price=165.0, position_size=10000.0,
        )
        assert result is not None
        assert result.qty * result.limit_price <= 1000

    def test_long_insufficient_buying_power(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="long", entry_price=150.0,
            stop_loss=142.5, target_price=165.0, position_size=1000.0,
            buying_power=100.0,
        )
        assert result is None

    def test_long_zero_price(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="long", entry_price=0,
            stop_loss=10, target_price=20, position_size=1000.0,
        )
        assert result is None

    def test_long_no_bracket_when_no_stop(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="long", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=1000.0,
        )
        assert result is not None
        assert result.is_bracket is False


class TestCallOptionsMapping:
    def test_basic_call_options(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="call_options", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=500.0,
            contracts=2, strike=157.5, option_type="call",
        )
        assert result is not None
        assert result.side == "buy"
        assert result.strategy == "call_options"
        assert result.qty == 2

    def test_call_options_no_contracts(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="call_options", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=500.0,
            contracts=0,
        )
        assert result is None

    def test_call_options_insufficient_buying_power(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="call_options", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=500.0,
            contracts=2, buying_power=5.0,
        )
        assert result is None


class TestBullSpreadMapping:
    def test_basic_bull_spread(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2,
        )
        assert result is not None
        assert result.strategy == "bull_spread"
        assert result.qty == 2
        # per-contract limit = position_size / contracts
        assert result.limit_price == 200.0

    def test_bull_spread_no_contracts(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=0,
        )
        assert result is None

    def test_bull_spread_caps_at_max_position(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=5000.0,
            contracts=4,
        )
        assert result is not None
        assert result.qty * result.limit_price <= 1000

    def test_bull_spread_insufficient_buying_power(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2, buying_power=10.0,
        )
        assert result is None


class TestBearSpreadMapping:
    def test_basic_spread(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2,
        )
        assert result is not None
        assert result.strategy == "spread"
        assert result.qty == 2


class TestValidation:
    def test_valid_order(self):
        mapper = OrderMapper(max_position=1000)
        order = mapper.recommendation_to_order(
            ticker="AAPL", strategy="short", entry_price=150.0,
            stop_loss=157.5, target_price=135.0, position_size=1000.0,
        )
        ok, reason = mapper.validate_order(order)
        assert ok is True

    def test_valid_long(self):
        mapper = OrderMapper(max_position=1000)
        order = mapper.recommendation_to_order(
            ticker="AAPL", strategy="long", entry_price=150.0,
            stop_loss=142.5, target_price=165.0, position_size=1000.0,
        )
        ok, _ = mapper.validate_order(order)
        assert ok is True

    def test_unknown_strategy(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="unknown", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=1000.0,
        )
        assert result is None
