"""Tests for the backtesting engine."""

from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

from src.models.backtester import Backtester, Trade, BacktestResult


def _make_price_data(tickers, start_date, num_days, base_price=100.0):
    """Generate synthetic price data for testing."""
    rows = []
    for ticker in tickers:
        price = base_price
        for i in range(num_days):
            d = start_date + timedelta(days=i)
            # Skip weekends
            if d.weekday() >= 5:
                continue
            change = np.random.normal(0, 0.02)
            price *= (1 + change)
            rows.append({
                "ticker": ticker,
                "date": d,
                "open": round(price * 0.99, 2),
                "high": round(price * 1.02, 2),
                "low": round(price * 0.98, 2),
                "close": round(price, 2),
                "volume": 1_000_000,
            })
    return pd.DataFrame(rows)


def _make_indicator_data(price_data):
    """Generate synthetic indicator data matching price dates."""
    rows = []
    for _, row in price_data.iterrows():
        rows.append({
            "ticker": row["ticker"],
            "date": row["date"],
            "rsi_14": np.random.uniform(30, 70),
            "macd": np.random.normal(0, 1),
            "macd_signal": np.random.normal(0, 1),
            "macd_histogram": np.random.normal(0, 0.5),
            "bb_percent_b": np.random.uniform(0, 1),
            "bb_upper": row["close"] * 1.05,
            "bb_lower": row["close"] * 0.95,
            "sma_50": row["close"] * 0.99,
            "sma_200": row["close"] * 0.98,
            "sma_crossover": 0,
            "volume_zscore": np.random.normal(0, 1),
        })
    return pd.DataFrame(rows)


@pytest.fixture
def sample_data():
    start = date(2024, 1, 2)
    tickers = ["AAPL", "MSFT", "GOOGL"]
    prices = _make_price_data(tickers, start, 60)
    indicators = _make_indicator_data(prices)
    sentiments = pd.DataFrame(columns=["ticker", "date", "sentiment", "confidence"])
    return prices, indicators, sentiments


class TestBacktester:
    def test_init(self):
        bt = Backtester(max_position=3000, hold_days=10, score_threshold=0.6)
        assert bt.max_position == 3000
        assert bt.hold_days == 10
        assert bt.score_threshold == 0.6

    @patch.object(Backtester, "_load_data")
    def test_run_basic(self, mock_load, sample_data):
        """Test basic backtest execution with synthetic data."""
        mock_load.return_value = sample_data
        bt = Backtester(max_position=1000, score_threshold=0.3)
        result = bt.run(strategy="short")

        assert isinstance(result, BacktestResult)
        assert result.strategy == "short"
        assert result.start_date is not None
        assert result.end_date is not None
        assert "total_pnl" in result.metrics
        assert "win_rate" in result.metrics
        assert "sharpe_ratio" in result.metrics
        assert "max_drawdown" in result.metrics
        assert "profit_factor" in result.metrics

    @patch.object(Backtester, "_load_data")
    def test_run_options(self, mock_load, sample_data):
        """Test options strategy backtest."""
        mock_load.return_value = sample_data
        bt = Backtester(max_position=1000, score_threshold=0.3)
        result = bt.run(strategy="options")

        assert result.strategy == "options"
        for trade in result.trades:
            assert trade.strategy == "options"

    @patch.object(Backtester, "_load_data")
    def test_run_combined(self, mock_load, sample_data):
        """Test combined strategy backtest."""
        mock_load.return_value = sample_data
        bt = Backtester(max_position=1000, score_threshold=0.3)
        result = bt.run(strategy="combined")

        assert result.strategy == "combined"

    @patch.object(Backtester, "_load_data")
    def test_empty_data_raises(self, mock_load):
        """Test that empty price data raises ValueError."""
        empty_prices = pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close", "volume"])
        empty_ind = pd.DataFrame(columns=["ticker", "date"])
        empty_sent = pd.DataFrame(columns=["ticker", "date", "sentiment", "confidence"])
        mock_load.return_value = (empty_prices, empty_ind, empty_sent)

        bt = Backtester()
        with pytest.raises(ValueError, match="No price data"):
            bt.run()

    def test_compute_metrics_empty(self):
        bt = Backtester()
        metrics = bt._compute_metrics([], [])
        assert metrics["total_pnl"] == 0.0
        assert metrics["num_trades"] == 0
        assert metrics["win_rate"] == 0.0

    def test_compute_metrics_with_trades(self):
        trades = [
            Trade("AAPL", "short", date(2024, 1, 2), date(2024, 1, 7), 150.0, 145.0,
                  1000, 3, 157.5, 135.0, 750, 0.7, pnl=50.0, return_pct=0.033),
            Trade("MSFT", "short", date(2024, 1, 2), date(2024, 1, 7), 300.0, 310.0,
                  1000, 1, 315.0, 270.0, 750, 0.6, pnl=-50.0, return_pct=-0.033),
            Trade("GOOGL", "short", date(2024, 1, 2), date(2024, 1, 7), 130.0, 120.0,
                  1000, 4, 136.5, 117.0, 780, 0.8, pnl=120.0, return_pct=0.077),
        ]
        daily = [
            {"date": "2024-01-02", "total_equity": 0},
            {"date": "2024-01-03", "total_equity": 50},
            {"date": "2024-01-04", "total_equity": -20},
            {"date": "2024-01-05", "total_equity": 120},
        ]

        bt = Backtester()
        metrics = bt._compute_metrics(trades, daily)

        assert metrics["num_trades"] == 3
        assert metrics["total_pnl"] == 120.0
        assert metrics["total_winners"] == 2
        assert metrics["total_losers"] == 1
        assert metrics["win_rate"] == round(2 / 3, 4)
        assert metrics["best_trade"] == 120.0
        assert metrics["worst_trade"] == -50.0
        assert metrics["max_drawdown"] == 70.0  # peak 50, trough -20

    def test_to_dict(self):
        result = BacktestResult(
            strategy="short",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 3, 29),
            trades=[
                Trade("AAPL", "short", date(2024, 1, 2), date(2024, 1, 7), 150.0, 145.0,
                      1000, 3, 157.5, 135.0, 750, 0.7, pnl=50.0, return_pct=0.033),
            ],
            metrics={"total_pnl": 50.0, "win_rate": 1.0},
        )
        d = result.to_dict()
        assert d["strategy"] == "short"
        assert d["num_trades"] == 1
        assert len(d["trades"]) == 1
        assert d["trades"][0]["ticker"] == "AAPL"

    def test_close_position_short(self, sample_data):
        prices, _, _ = sample_data
        bt = Backtester()
        first_date = prices["date"].min()
        later_date = prices[prices["date"] > first_date]["date"].iloc[3]

        pos = {
            "ticker": "AAPL",
            "strategy": "short",
            "entry_date": first_date,
            "exit_date": later_date,
            "entry_price": 100.0,
            "shares": 10,
            "stop_loss": 105.0,
            "target_price": 90.0,
            "position_size": 1500.0,
            "max_loss": 50.0,
            "score": 0.7,
        }

        trade = bt._close_position(pos, prices, later_date)
        assert trade is not None
        assert trade.ticker == "AAPL"
        assert trade.strategy == "short"
        # P&L = shares * (entry - exit)
        expected_pnl = 10 * (100.0 - trade.exit_price)
        assert trade.pnl == round(expected_pnl, 2)

    def test_close_position_options(self, sample_data):
        prices, _, _ = sample_data
        bt = Backtester()
        first_date = prices["date"].min()
        later_date = prices[prices["date"] > first_date]["date"].iloc[3]

        pos = {
            "ticker": "AAPL",
            "strategy": "options",
            "entry_date": first_date,
            "exit_date": later_date,
            "entry_price": 100.0,
            "contracts": 2,
            "stop_loss": 105.0,
            "target_price": 95.0,
            "position_size": 600.0,  # premium paid
            "max_loss": 600.0,
            "score": 0.7,
        }

        trade = bt._close_position(pos, prices, later_date)
        assert trade is not None
        assert trade.strategy == "options"

    @patch.object(Backtester, "_load_data")
    def test_max_concurrent_respected(self, mock_load, sample_data):
        """Ensure we don't exceed max concurrent positions."""
        mock_load.return_value = sample_data
        bt = Backtester(max_position=1000, score_threshold=0.0, max_concurrent_positions=2)
        result = bt.run(strategy="short")

        # Check daily equity: open_positions should never exceed max
        for day in result.daily_equity:
            assert day["open_positions"] <= 2

    @patch.object(Backtester, "_load_data")
    def test_daily_equity_tracking(self, mock_load, sample_data):
        """Ensure daily equity is tracked for every trading day."""
        mock_load.return_value = sample_data
        bt = Backtester(max_position=1000, score_threshold=0.3)
        result = bt.run(strategy="short")

        assert len(result.daily_equity) > 0
        for day in result.daily_equity:
            assert "date" in day
            assert "cumulative_pnl" in day
            assert "total_equity" in day


class TestTradeStopLossTarget:
    def test_short_stop_loss(self):
        """Verify stop-loss triggers correctly for short positions."""
        bt = Backtester()
        # Price data where high exceeds stop loss
        prices = pd.DataFrame([
            {"ticker": "AAPL", "date": date(2024, 1, 3), "open": 102, "high": 106, "low": 101, "close": 105, "volume": 1e6},
        ])
        pos = {
            "ticker": "AAPL", "strategy": "short",
            "entry_date": date(2024, 1, 2), "exit_date": date(2024, 1, 9),
            "entry_price": 100.0, "shares": 10,
            "stop_loss": 105.0, "target_price": 90.0,
            "position_size": 1500, "max_loss": 50, "score": 0.7,
        }

        closed, still_open = bt._check_positions([pos], prices, date(2024, 1, 3))
        assert len(closed) == 1
        assert closed[0].hit_stop is True
        assert closed[0].exit_price == 105.0  # Closed at stop price

    def test_short_target_hit(self):
        """Verify target triggers correctly for short positions."""
        bt = Backtester()
        prices = pd.DataFrame([
            {"ticker": "AAPL", "date": date(2024, 1, 3), "open": 92, "high": 93, "low": 89, "close": 90, "volume": 1e6},
        ])
        pos = {
            "ticker": "AAPL", "strategy": "short",
            "entry_date": date(2024, 1, 2), "exit_date": date(2024, 1, 9),
            "entry_price": 100.0, "shares": 10,
            "stop_loss": 105.0, "target_price": 90.0,
            "position_size": 1500, "max_loss": 50, "score": 0.7,
        }

        closed, still_open = bt._check_positions([pos], prices, date(2024, 1, 3))
        assert len(closed) == 1
        assert closed[0].hit_target is True
        assert closed[0].exit_price == 90.0  # Closed at target price
