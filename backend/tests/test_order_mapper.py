"""Tests for OrderMapper."""

import json
from datetime import date

import pytest

from src.services.order_mapper import OrderMapper, build_occ_symbol


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
            expiry=date(2025, 4, 18),
        )
        assert result is not None
        assert result.side == "buy"
        assert result.strategy == "options"
        assert result.qty == 3
        assert result.occ_symbol == "AAPL250418P00145000"

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
            contracts=3, strike=145.0, option_type="put",
            expiry=date(2025, 4, 18), buying_power=10.0,
        )
        assert result is None

    def test_options_missing_expiry_returns_none(self):
        """Without expiry the mapper can't build OCC — must reject so we never
        ship an equity-shaped order to Alpaca for an option rec."""
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="options", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=2000.0,
            contracts=3, strike=145.0, option_type="put",
        )
        assert result is None

    def test_options_missing_strike_returns_none(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="options", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=2000.0,
            contracts=3, strike=None, option_type="put",
            expiry=date(2025, 4, 18),
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

    def test_long_unaffordable_without_fractional_returns_none(self):
        """Default (fractional off): when entry > per-trade cap, no order.
        Locks in the legacy whole-share-only behavior."""
        mapper = OrderMapper(max_position=250)
        mapper.enable_fractional = False
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="long", entry_price=400.0,
            stop_loss=380.0, target_price=440.0, position_size=250.0,
        )
        assert result is None

    def test_long_fractional_emits_market_order_no_bracket(self):
        """With fractional on, a $400 stock at $250 cap sizes ~0.625 shares.
        Alpaca only supports fractional as market without brackets — stop/target
        must be stripped to avoid silent API rejection."""
        mapper = OrderMapper(max_position=250)
        mapper.enable_fractional = True
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="long", entry_price=400.0,
            stop_loss=380.0, target_price=440.0, position_size=250.0,
        )
        assert result is not None
        assert 0 < result.qty < 1
        assert result.order_type == "market"
        assert result.limit_price is None
        assert result.stop_loss_price is None
        assert result.take_profit_price is None
        assert result.is_bracket is False
        assert result.qty * 400.0 <= 250.0

    def test_long_fractional_below_one_dollar_returns_none(self):
        """Alpaca enforces a $1 notional minimum on fractional orders."""
        mapper = OrderMapper(max_position=0.5)
        mapper.enable_fractional = True
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="long", entry_price=400.0,
            stop_loss=None, target_price=None, position_size=0.5,
        )
        assert result is None

    def test_long_whole_shares_unchanged_when_fractional_on(self):
        """Fractional setting should NOT change behavior for normal whole-share
        sizing — bracket order with limit price must still be emitted."""
        mapper = OrderMapper(max_position=1000)
        mapper.enable_fractional = True
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="long", entry_price=150.0,
            stop_loss=142.5, target_price=165.0, position_size=1000.0,
        )
        assert result is not None
        assert result.qty >= 1
        assert isinstance(result.qty, int)
        assert result.order_type == "limit"
        assert result.is_bracket is True


class TestCallOptionsMapping:
    def test_basic_call_options(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="call_options", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=500.0,
            contracts=2, strike=157.5, option_type="call",
            expiry=date(2025, 4, 18),
        )
        assert result is not None
        assert result.side == "buy"
        assert result.strategy == "call_options"
        assert result.qty == 2
        assert result.occ_symbol == "AAPL250418C00157500"

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
            contracts=2, strike=157.5, option_type="call",
            expiry=date(2025, 4, 18), buying_power=5.0,
        )
        assert result is None

    def test_call_options_missing_expiry_returns_none(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="call_options", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=500.0,
            contracts=2, strike=157.5, option_type="call",
        )
        assert result is None


class TestBuildOccSymbol:
    """OCC option symbol format: ROOT + YYMMDD + C/P + 8-digit strike*1000."""

    def test_put_strike_whole_number(self):
        assert build_occ_symbol("AAPL", date(2025, 4, 18), "put", 150.0) == "AAPL250418P00150000"

    def test_call_strike_with_decimal(self):
        assert build_occ_symbol("AAPL", date(2025, 4, 18), "call", 157.5) == "AAPL250418C00157500"

    def test_ticker_uppercased(self):
        assert build_occ_symbol("aapl", date(2025, 4, 18), "p", 150.0) == "AAPL250418P00150000"

    def test_option_type_short_form(self):
        assert build_occ_symbol("AAPL", date(2025, 4, 18), "C", 150.0) == "AAPL250418C00150000"

    def test_high_strike_pads_correctly(self):
        # NVDA at $1200 strike — still fits 8 digits ($99,999.999 max)
        assert build_occ_symbol("NVDA", date(2026, 1, 16), "call", 1200.0) == "NVDA260116C01200000"

    def test_low_strike_pads_correctly(self):
        assert build_occ_symbol("F", date(2025, 6, 20), "put", 12.5) == "F250620P00012500"

    def test_fractional_strike_rounds(self):
        # Sub-cent strike rounds — 100.005 -> 100005 milli-dollars
        assert build_occ_symbol("AAPL", date(2025, 4, 18), "call", 100.005) == "AAPL250418C00100005"

    def test_invalid_option_type_raises(self):
        with pytest.raises(ValueError):
            build_occ_symbol("AAPL", date(2025, 4, 18), "straddle", 150.0)

    def test_zero_strike_raises(self):
        with pytest.raises(ValueError):
            build_occ_symbol("AAPL", date(2025, 4, 18), "put", 0)

    def test_empty_ticker_raises(self):
        with pytest.raises(ValueError):
            build_occ_symbol("", date(2025, 4, 18), "put", 150.0)


_BULL_LEGS = json.dumps([
    {"option_type": "call", "action": "buy", "strike": 150.0, "premium": 3.0, "contracts": 2},
    {"option_type": "call", "action": "sell", "strike": 155.0, "premium": 1.0, "contracts": 2},
])
_BEAR_LEGS = json.dumps([
    {"option_type": "put", "action": "buy", "strike": 145.0, "premium": 2.5, "contracts": 2},
    {"option_type": "put", "action": "sell", "strike": 140.0, "premium": 1.0, "contracts": 2},
])
_EXPIRY = date(2025, 4, 18)


class TestBullSpreadMapping:
    def test_basic_bull_spread(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2, expiry=_EXPIRY, legs_json=_BULL_LEGS,
        )
        assert result is not None
        assert result.strategy == "bull_spread"
        assert result.qty == 2
        # Alpaca options limit_price is PER SHARE. position_size=400, contracts=2
        # → per_contract=200 dollars → per_share=2.00 dollars (÷100 multiplier).
        assert result.limit_price == 2.0
        # Per-leg OCC symbols built. Leg shape: occ_symbol, side, ratio_qty.
        assert result.legs is not None
        assert len(result.legs) == 2
        assert result.legs[0]["occ_symbol"] == "AAPL250418C00150000"
        assert result.legs[0]["side"] == "buy"
        assert result.legs[0]["ratio_qty"] == 1
        assert result.legs[1]["occ_symbol"] == "AAPL250418C00155000"
        assert result.legs[1]["side"] == "sell"

    def test_bull_spread_no_contracts(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=0, expiry=_EXPIRY, legs_json=_BULL_LEGS,
        )
        assert result is None

    def test_bull_spread_caps_at_max_position(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=5000.0,
            contracts=4, expiry=_EXPIRY, legs_json=_BULL_LEGS,
        )
        assert result is not None
        # Total dollar cost = qty (spreads) * limit_price (per share) * 100 (multiplier)
        assert result.qty * result.limit_price * 100 <= 1000

    def test_bull_spread_insufficient_buying_power(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2, expiry=_EXPIRY, legs_json=_BULL_LEGS,
            buying_power=10.0,
        )
        assert result is None

    def test_bull_spread_missing_expiry(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2, legs_json=_BULL_LEGS,
        )
        assert result is None

    def test_bull_spread_missing_legs(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2, expiry=_EXPIRY,
        )
        assert result is None

    def test_bull_spread_malformed_legs_json(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2, expiry=_EXPIRY, legs_json="not-json",
        )
        assert result is None

    def test_bull_spread_limit_price_is_per_share_not_per_contract(self):
        """Regression: 2026-06-09 INTC submit was rejected for $92,000 cost_basis
        vs $20,856 buying power. Root cause: limit_price was passed as
        dollars-per-contract (e.g. $230) when Alpaca expects dollars-per-share
        (e.g. $2.30). Alpaca then computed cost = limit_price × 100 × qty,
        inflating by 100x. Guard against regression.
        """
        mapper = OrderMapper(max_position=10000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=920.0,
            contracts=4, expiry=_EXPIRY, legs_json=_BULL_LEGS,
        )
        assert result is not None
        # Per-share = 920 / 4 / 100 = 2.30. NOT 230.
        assert result.limit_price == 2.30
        # Alpaca-equivalent cost = limit * multiplier * qty should match
        # position_size, not be 100x over. approx: 2.3 * 100 * 4 is
        # 919.999... in binary floating point.
        assert result.limit_price * 100 * result.qty == pytest.approx(920.0)

    def test_marketable_limit_walks_toward_natural(self):
        # Buy leg quoted 2.90/3.10 (mid 3.00), sell leg 0.90/1.10 (mid 1.00).
        # Net mid debit = 2.00; natural = 3.10 - 0.90 = 2.20. With the default
        # fraction 0.35 the limit lands at 2.00 + 0.35 * 0.20 = 2.07.
        legs = json.dumps([
            {"option_type": "call", "action": "buy", "strike": 150.0,
             "premium": 3.0, "contracts": 2, "bid": 2.90, "ask": 3.10},
            {"option_type": "call", "action": "sell", "strike": 155.0,
             "premium": 1.0, "contracts": 2, "bid": 0.90, "ask": 1.10},
        ])
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2, expiry=_EXPIRY, legs_json=legs,
        )
        assert result is not None
        assert result.limit_price == pytest.approx(2.07)

    def test_marketable_limit_caps_at_max_position(self):
        # Marketable price would be 5.07/share x 100 x 3 = $1,521 — above the
        # $1,000 per-trade cap, so the limit clamps to 1000 / 300 = 3.33.
        legs = json.dumps([
            {"option_type": "call", "action": "buy", "strike": 150.0,
             "premium": 6.0, "contracts": 3, "bid": 5.90, "ask": 6.10},
            {"option_type": "call", "action": "sell", "strike": 155.0,
             "premium": 1.0, "contracts": 3, "bid": 0.90, "ask": 1.10},
        ])
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=1000.0,
            contracts=3, expiry=_EXPIRY, legs_json=legs,
        )
        assert result is not None
        assert result.limit_price * 100 * result.qty <= 1000
        assert result.limit_price == pytest.approx(1000 / 300, abs=0.01)

    def test_credit_spread_priced_as_negative_limit(self):
        # Bull put credit: sell 150P (mid 3.00), buy 145P (mid 1.00) →
        # credit mid 2.00; natural credit = 2.90 - 1.10 = 1.80. Marketable =
        # 2.00 - 0.35 * 0.20 = 1.93, expressed as limit_price -1.93 (Alpaca
        # MLEG minimum-credit convention).
        legs = json.dumps([
            {"option_type": "put", "action": "sell", "strike": 150.0,
             "premium": 3.0, "contracts": 2, "bid": 2.90, "ask": 3.10},
            {"option_type": "put", "action": "buy", "strike": 145.0,
             "premium": 1.0, "contracts": 2, "bid": 0.90, "ask": 1.10},
        ])
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2, expiry=_EXPIRY, legs_json=legs,
        )
        assert result is not None
        assert result.limit_price == pytest.approx(-1.93)
        # Negative limit is valid for MLEG credit orders
        ok, msg = mapper.validate_order(result)
        assert ok, msg

    def test_credit_spread_collateral_exceeds_cap_dropped(self):
        # Width 10, credit ~1.93 → collateral ~$807/contract; 2 contracts =
        # $1,614 > $1,000 per-trade max → order dropped rather than emitted.
        legs = json.dumps([
            {"option_type": "put", "action": "sell", "strike": 150.0,
             "premium": 3.0, "contracts": 2, "bid": 2.90, "ask": 3.10},
            {"option_type": "put", "action": "buy", "strike": 140.0,
             "premium": 1.0, "contracts": 2, "bid": 0.90, "ask": 1.10},
        ])
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2, expiry=_EXPIRY, legs_json=legs,
        )
        assert result is None

    def test_credit_spread_insufficient_buying_power(self):
        legs = json.dumps([
            {"option_type": "put", "action": "sell", "strike": 150.0,
             "premium": 3.0, "contracts": 1, "bid": 2.90, "ask": 3.10},
            {"option_type": "put", "action": "buy", "strike": 145.0,
             "premium": 1.0, "contracts": 1, "bid": 0.90, "ask": 1.10},
        ])
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=1, expiry=_EXPIRY, legs_json=legs,
            buying_power=100.0,  # collateral ~$307 > $100
        )
        assert result is None

    def test_credit_spread_without_quotes_dropped(self):
        """Regression: 2026-07-15 pre-market chain quotes were bid=0/ask=0,
        the credit pricing path bailed, and the order fell through to the
        cost-derived POSITIVE limit — MRNA 66/62 put credit filled at a $0.20
        debit instead of collecting the ~$1.96 credit. A credit-intent spread
        without usable quotes must be dropped, never repriced as a debit.
        """
        legs = json.dumps([
            {"option_type": "put", "action": "sell", "strike": 150.0,
             "premium": 3.0, "contracts": 2},
            {"option_type": "put", "action": "buy", "strike": 145.0,
             "premium": 1.0, "contracts": 2},
        ])
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2, expiry=_EXPIRY, legs_json=legs,
        )
        assert result is None

    def test_credit_spread_with_null_quotes_dropped(self):
        # The exact prod shape from 2026-07-15: put-credit legs written with
        # bid: null / ask: null because the 07:30 ET chain had zero quotes.
        legs = json.dumps([
            {"option_type": "put", "action": "sell", "strike": 66.0,
             "premium": 6.66, "contracts": 4, "bid": None, "ask": None},
            {"option_type": "put", "action": "buy", "strike": 62.0,
             "premium": 4.7, "contracts": 4, "bid": None, "ask": None},
        ])
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="MRNA", strategy="bull_spread", entry_price=67.44,
            stop_loss=None, target_price=None, position_size=80.0,
            contracts=4, expiry=_EXPIRY, legs_json=legs,
        )
        assert result is None

    def test_credit_spread_quotes_pricing_debit_dropped(self):
        # Credit-intent legs whose live quotes net out to a debit (market
        # moved): credit pricing returns None and the order is dropped rather
        # than silently flipped to a debit trade.
        legs = json.dumps([
            {"option_type": "put", "action": "sell", "strike": 150.0,
             "premium": 3.0, "contracts": 2, "bid": 0.90, "ask": 1.10},
            {"option_type": "put", "action": "buy", "strike": 145.0,
             "premium": 1.0, "contracts": 2, "bid": 2.90, "ask": 3.10},
        ])
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2, expiry=_EXPIRY, legs_json=legs,
        )
        assert result is None

    def test_debit_spread_without_quotes_keeps_legacy_path(self):
        # Debit-intent spreads still fall back to the cost-derived positive
        # limit when quotes are missing — a too-low debit limit just doesn't
        # fill; it cannot give money away the way a credit spread can.
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2, expiry=_EXPIRY, legs_json=_BULL_LEGS,
        )
        assert result is not None
        assert result.limit_price == 2.0

    def test_validate_order_rejects_negative_limit_without_legs(self):
        from src.services.order_mapper import AlpacaOrderParams
        mapper = OrderMapper(max_position=1000)
        params = AlpacaOrderParams(
            ticker="AAPL", qty=1, side="buy", order_type="limit",
            limit_price=-1.5, strategy="long",
        )
        ok, msg = mapper.validate_order(params)
        assert not ok

    def test_marketable_limit_requires_two_sided_quotes(self):
        # One leg missing its ask → fall back to cost-derived mid pricing.
        legs = json.dumps([
            {"option_type": "call", "action": "buy", "strike": 150.0,
             "premium": 3.0, "contracts": 2, "bid": 2.90, "ask": None},
            {"option_type": "call", "action": "sell", "strike": 155.0,
             "premium": 1.0, "contracts": 2, "bid": 0.90, "ask": 1.10},
        ])
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2, expiry=_EXPIRY, legs_json=legs,
        )
        assert result is not None
        assert result.limit_price == 2.0

    def test_bull_spread_single_leg_rejected(self):
        single_leg = json.dumps([{"option_type": "call", "action": "buy", "strike": 150.0}])
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="bull_spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2, expiry=_EXPIRY, legs_json=single_leg,
        )
        assert result is None


class TestBearSpreadMapping:
    def test_basic_spread(self):
        mapper = OrderMapper(max_position=1000)
        result = mapper.recommendation_to_order(
            ticker="AAPL", strategy="spread", entry_price=150.0,
            stop_loss=None, target_price=None, position_size=400.0,
            contracts=2, expiry=_EXPIRY, legs_json=_BEAR_LEGS,
        )
        assert result is not None
        assert result.strategy == "spread"
        assert result.qty == 2
        assert result.legs is not None
        assert result.legs[0]["occ_symbol"] == "AAPL250418P00145000"
        assert result.legs[1]["occ_symbol"] == "AAPL250418P00140000"


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
