"""legs_json parsing for API responses.

legs_json carries two shapes: option legs (spread/bull_spread) and stock legs
(pair_short). Each parser must return None for the other shape rather than
erroring, since both run against every row.
"""

import json

from src.api.leg_parsing import parse_option_legs, parse_stock_legs

_OPTION_LEGS = json.dumps([
    {"option_type": "call", "action": "buy", "strike": 445.0,
     "premium": 25.58, "contracts": 1, "bid": 25.0, "ask": 26.1},
    {"option_type": "call", "action": "sell", "strike": 465.0,
     "premium": 15.2, "contracts": 1},
])
_STOCK_LEGS = json.dumps([
    {"leg": "short", "ticker": "RIVN", "qty": 15, "entry": 17.48},
    {"leg": "hedge", "ticker": "SPY", "qty": 0.3473, "entry": 754.95},
])


class TestParseOptionLegs:
    def test_parses_option_shape(self):
        legs = parse_option_legs(_OPTION_LEGS)
        assert len(legs) == 2
        assert legs[0].action == "buy"
        assert legs[0].strike == 445.0
        assert legs[1].premium == 15.2

    def test_stock_shape_returns_none(self):
        assert parse_option_legs(_STOCK_LEGS) is None

    def test_none_and_bad_json(self):
        assert parse_option_legs(None) is None
        assert parse_option_legs("") is None
        assert parse_option_legs("not-json") is None
        assert parse_option_legs(json.dumps({"not": "a list"})) is None

    def test_malformed_leg_skipped(self):
        payload = json.dumps([
            {"option_type": "call", "action": "buy", "strike": "bad"},
            {"option_type": "put", "action": "sell", "strike": 100.0},
        ])
        legs = parse_option_legs(payload)
        assert len(legs) == 1
        assert legs[0].option_type == "put"


class TestParseStockLegs:
    def test_parses_pair_shape(self):
        legs = parse_stock_legs(_STOCK_LEGS)
        assert len(legs) == 2
        assert legs[0].leg == "short"
        assert legs[0].ticker == "RIVN"
        assert legs[1].qty == 0.3473

    def test_option_shape_returns_none(self):
        assert parse_stock_legs(_OPTION_LEGS) is None

    def test_none_and_bad_json(self):
        assert parse_stock_legs(None) is None
        assert parse_stock_legs("not-json") is None
