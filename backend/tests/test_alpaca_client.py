"""Tests for AlpacaClient with mocked Alpaca API."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timezone

from src.services.alpaca_client import AlpacaClient, _underlying_from_occ


def _mock_account(**overrides):
    acct = MagicMock()
    acct.equity = overrides.get("equity", "50000.00")
    acct.buying_power = overrides.get("buying_power", "100000.00")
    acct.cash = overrides.get("cash", "50000.00")
    acct.daytrade_count = overrides.get("daytrade_count", 0)
    acct.pattern_day_trader = overrides.get("pattern_day_trader", False)
    acct.currency = "USD"
    acct.status = MagicMock(value="ACTIVE")
    return acct


def _mock_position(symbol="AAPL", qty="10", side="long", avg_entry="150.00",
                   current="155.00", market_value="1550.00", unrealized_pl="50.00",
                   unrealized_plpc="0.0333", change_today="0.01"):
    pos = MagicMock()
    pos.symbol = symbol
    pos.qty = qty
    pos.side = MagicMock(value=side)
    pos.avg_entry_price = avg_entry
    pos.current_price = current
    pos.market_value = market_value
    pos.unrealized_pl = unrealized_pl
    pos.unrealized_plpc = unrealized_plpc
    pos.change_today = change_today
    return pos


def _mock_order(order_id="abc-123", symbol="AAPL", side="sell", qty="10",
                order_type="market", status="filled", limit_price=None,
                stop_price=None, filled_avg_price="155.00", filled_qty="10",
                legs=None):
    order = MagicMock()
    order.id = order_id
    order.symbol = symbol
    order.side = MagicMock(value=side)
    order.qty = qty
    order.type = MagicMock(value=order_type)
    order.status = MagicMock(value=status)
    order.limit_price = limit_price
    order.stop_price = stop_price
    order.filled_avg_price = filled_avg_price
    order.filled_qty = filled_qty
    order.submitted_at = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    order.filled_at = datetime(2026, 4, 14, 10, 1, tzinfo=timezone.utc)
    order.legs = legs
    return order


def _mock_clock(is_open=True):
    clock = MagicMock()
    clock.is_open = is_open
    clock.next_open = datetime(2026, 4, 15, 13, 30, tzinfo=timezone.utc)
    clock.next_close = datetime(2026, 4, 14, 20, 0, tzinfo=timezone.utc)
    return clock


def _make_client():
    mock_trading = MagicMock()
    return AlpacaClient(client=mock_trading), mock_trading


class TestGetAccount:
    def test_returns_account_info(self):
        client, mock = _make_client()
        mock.get_account.return_value = _mock_account()

        result = client.get_account()

        assert result["equity"] == 50000.0
        assert result["buying_power"] == 100000.0
        assert result["cash"] == 50000.0
        assert result["day_trade_count"] == 0
        assert result["status"] == "ACTIVE"

    def test_low_buying_power(self):
        client, mock = _make_client()
        mock.get_account.return_value = _mock_account(buying_power="100.00")

        result = client.get_account()

        assert result["buying_power"] == 100.0


class TestGetPositions:
    def test_returns_positions(self):
        client, mock = _make_client()
        mock.get_all_positions.return_value = [
            _mock_position("AAPL"),
            _mock_position("MSFT", qty="5", avg_entry="300.00", current="310.00",
                          market_value="1550.00", unrealized_pl="50.00"),
        ]

        result = client.get_positions()

        assert len(result) == 2
        assert result[0]["ticker"] == "AAPL"
        assert result[0]["qty"] == 10.0
        assert result[1]["ticker"] == "MSFT"

    def test_empty_positions(self):
        client, mock = _make_client()
        mock.get_all_positions.return_value = []

        assert client.get_positions() == []


class TestSubmitOrder:
    def test_market_order(self):
        client, mock = _make_client()
        mock.submit_order.return_value = _mock_order()

        result = client.submit_order("AAPL", 10, "sell")

        assert result["order_id"] == "abc-123"
        assert result["ticker"] == "AAPL"
        assert result["status"] == "filled"
        mock.submit_order.assert_called_once()

    def test_limit_order(self):
        client, mock = _make_client()
        mock.submit_order.return_value = _mock_order(status="new", limit_price="150.00")

        result = client.submit_order("AAPL", 10, "sell", order_type="limit", limit_price=150.0)

        assert result["status"] == "new"
        mock.submit_order.assert_called_once()


class TestBracketOrder:
    def test_bracket_with_stop_and_target(self):
        client, mock = _make_client()
        mock.submit_order.return_value = _mock_order()

        result = client.submit_bracket_order(
            "AAPL", 10, "sell",
            stop_loss_price=160.0,
            take_profit_price=140.0,
        )

        assert result["order_id"] == "abc-123"
        mock.submit_order.assert_called_once()

    def test_bracket_with_limit(self):
        client, mock = _make_client()
        mock.submit_order.return_value = _mock_order(status="new")

        result = client.submit_bracket_order(
            "AAPL", 10, "sell",
            limit_price=155.0,
            stop_loss_price=160.0,
            take_profit_price=140.0,
        )

        assert result["status"] == "new"


class TestOrderManagement:
    def test_get_order(self):
        client, mock = _make_client()
        mock.get_order_by_id.return_value = _mock_order()

        result = client.get_order("abc-123")

        assert result["order_id"] == "abc-123"

    def test_get_orders_list(self):
        client, mock = _make_client()
        mock.get_orders.return_value = [_mock_order(), _mock_order(order_id="def-456", symbol="MSFT")]

        result = client.get_orders()

        assert len(result) == 2

    def test_cancel_order(self):
        client, mock = _make_client()

        result = client.cancel_order("abc-123")

        assert result["status"] == "cancel_requested"
        mock.cancel_order_by_id.assert_called_once_with("abc-123")

    def test_cancel_all(self):
        client, mock = _make_client()
        mock.cancel_orders.return_value = [MagicMock(), MagicMock()]

        result = client.cancel_all_orders()

        assert result["canceled"] == 2


class TestUnderlyingFromOcc:
    def test_call_option(self):
        assert _underlying_from_occ("INTC260626C00122000") == "INTC"

    def test_put_option(self):
        assert _underlying_from_occ("AAPL250117P00150000") == "AAPL"

    def test_six_char_underlying(self):
        assert _underlying_from_occ("BRKB250117C00400000") == "BRKB"

    def test_non_occ_returns_none(self):
        assert _underlying_from_occ("AAPL") is None
        assert _underlying_from_occ("") is None
        assert _underlying_from_occ(None) is None


class TestMlegOrderParentTicker:
    def test_derives_underlying_from_first_leg(self):
        leg1 = MagicMock()
        leg1.id = "leg-1"
        leg1.symbol = "INTC260626C00122000"
        leg1.side = MagicMock(value="sell")
        leg1.qty = "2"
        leg1.status = MagicMock(value="filled")
        leg1.filled_avg_price = "11.45"
        leg1.filled_qty = "2"

        leg2 = MagicMock()
        leg2.id = "leg-2"
        leg2.symbol = "INTC260626C00128000"
        leg2.side = MagicMock(value="buy")
        leg2.qty = "2"
        leg2.status = MagicMock(value="filled")
        leg2.filled_avg_price = "10.20"
        leg2.filled_qty = "2"

        parent = _mock_order(symbol=None, side=None, legs=[leg1, leg2])
        parent.side = None
        client, mock = _make_client()
        mock.get_order_by_id.return_value = parent

        result = client.get_order("mleg-parent")
        assert result["ticker"] == "INTC"
        assert result["legs"][0]["symbol"] == "INTC260626C00122000"


class TestMarketClock:
    def test_market_open(self):
        client, mock = _make_client()
        mock.get_clock.return_value = _mock_clock(is_open=True)

        assert client.is_market_open() is True

    def test_market_closed(self):
        client, mock = _make_client()
        mock.get_clock.return_value = _mock_clock(is_open=False)

        assert client.is_market_open() is False

    def test_get_clock_info(self):
        client, mock = _make_client()
        mock.get_clock.return_value = _mock_clock()

        result = client.get_clock()

        assert result["is_open"] is True
        assert result["next_open"] is not None
        assert result["next_close"] is not None


class TestConnection:
    def test_connection_success(self):
        client, mock = _make_client()
        mock.get_account.return_value = _mock_account()

        with patch("src.services.alpaca_client.get_settings") as mock_settings:
            mock_settings.return_value.alpaca_base_url = "https://paper-api.alpaca.markets"
            result = client.test_connection()

        assert result["connected"] is True
        assert result["paper"] is True

    def test_connection_failure(self):
        client, mock = _make_client()
        mock.get_account.side_effect = Exception("Connection refused")

        with patch("src.services.alpaca_client.get_settings") as mock_settings:
            mock_settings.return_value.alpaca_base_url = "https://paper-api.alpaca.markets"
            result = client.test_connection()

        assert result["connected"] is False
        assert "Connection refused" in result["error"]



class TestSubmitSpreadOrder:
    _BULL_LEGS = [
        {"occ_symbol": "AAPL250418C00150000", "side": "buy", "ratio_qty": 1},
        {"occ_symbol": "AAPL250418C00155000", "side": "sell", "ratio_qty": 1},
    ]

    def test_basic_spread_submit(self):
        from alpaca.trading.enums import OrderClass
        from alpaca.trading.requests import LimitOrderRequest
        client, mock = _make_client()
        mock.submit_order.return_value = _mock_order(symbol="AAPL250418C00150000")

        result = client.submit_spread_order(
            legs=self._BULL_LEGS, qty=2, limit_price=2.0,
        )

        assert result["order_id"] == "abc-123"
        submitted = mock.submit_order.call_args.args[0]
        assert isinstance(submitted, LimitOrderRequest)
        assert submitted.order_class == OrderClass.MLEG
        assert submitted.qty == 2
        assert submitted.limit_price == 2.0
        assert len(submitted.legs) == 2
        assert submitted.legs[0].symbol == "AAPL250418C00150000"
        assert submitted.legs[1].symbol == "AAPL250418C00155000"

    def test_market_spread_when_no_limit(self):
        from alpaca.trading.requests import MarketOrderRequest
        client, mock = _make_client()
        mock.submit_order.return_value = _mock_order()

        client.submit_spread_order(legs=self._BULL_LEGS, qty=1)

        submitted = mock.submit_order.call_args.args[0]
        assert isinstance(submitted, MarketOrderRequest)

    def test_single_leg_rejected(self):
        client, _ = _make_client()
        with pytest.raises(ValueError, match="2\+ legs"):
            client.submit_spread_order(legs=[self._BULL_LEGS[0]], qty=1)

    def test_empty_legs_rejected(self):
        client, _ = _make_client()
        with pytest.raises(ValueError, match="2\+ legs"):
            client.submit_spread_order(legs=[], qty=1)

    def test_malformed_leg_rejected(self):
        client, _ = _make_client()
        bad_legs = [
            {"occ_symbol": "AAPL250418C00150000", "side": "buy", "ratio_qty": 1},
            {"occ_symbol": "AAPL250418C00155000", "side": "hold", "ratio_qty": 1},
        ]
        with pytest.raises(ValueError, match=r"side must be buy\|sell"):
            client.submit_spread_order(legs=bad_legs, qty=1)
