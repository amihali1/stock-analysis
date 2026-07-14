"""Tests for the paper-vs-backtest validator."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, PaperTrade, Stock
from src.services.paper_validation import (
    DIVERGENCE_THRESHOLD,
    PaperValidator,
    _compute_paper_metrics,
    _relative_diff,
    format_report,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.add(Stock(ticker="AAPL"))
    s.add(Stock(ticker="MSFT"))
    s.commit()
    yield s
    s.close()


def _trade(pnl: float, position_size: float = 1000.0, days_ago: int = 1, **kwargs):
    closed_at = datetime.utcnow() - timedelta(days=days_ago)
    opened_at = closed_at - timedelta(days=5)
    return PaperTrade(
        ticker=kwargs.get("ticker", "AAPL"),
        strategy="short",
        status="closed",
        entry_price=100.0,
        position_size=position_size,
        pnl=pnl,
        opened_at=opened_at,
        closed_at=closed_at,
    )


class TestPaperMetrics:
    def test_empty_trades(self):
        out = _compute_paper_metrics([])
        assert out["num_trades"] == 0
        assert out["win_rate"] == 0.0

    def test_skips_open_trades(self):
        open_trade = PaperTrade(
            ticker="AAPL", strategy="short", status="open",
            entry_price=100.0, pnl=None,
        )
        out = _compute_paper_metrics([open_trade])
        assert out["num_trades"] == 0

    def test_basic_metrics(self):
        trades = [_trade(50), _trade(-30), _trade(100), _trade(-20)]
        out = _compute_paper_metrics(trades)
        assert out["num_trades"] == 4
        assert out["win_rate"] == 0.5
        assert out["total_pnl"] == 100.0
        assert out["avg_pnl"] == 25.0

    def test_max_drawdown_from_equity_curve(self):
        # +100 then -200 then +50 → equity = 100, -100, -50; peak=100, max_dd=200
        trades = [
            _trade(100, days_ago=10),
            _trade(-200, days_ago=5),
            _trade(50, days_ago=1),
        ]
        out = _compute_paper_metrics(trades)
        assert out["max_drawdown"] == 200.0


class TestRelativeDiff:
    def test_zero_zero(self):
        assert _relative_diff(0, 0) == 0

    def test_small_values(self):
        assert _relative_diff(0.5, 0.5) == 0
        assert _relative_diff(0.5, 0.4) == pytest.approx(0.2)

    def test_large_values(self):
        assert _relative_diff(1000, 800) == pytest.approx(0.2)


class StubBacktester:
    """Stand-in that returns canned metrics so tests don't need price data."""

    hold_days = 5

    def __init__(self, **metrics):
        self.metrics = metrics

    def run(self, strategy=None, start_date=None, end_date=None, **kwargs):  # noqa: D401
        from src.models.backtester import BacktestResult
        result = BacktestResult(strategy="combined", start_date=start_date, end_date=end_date)
        result.metrics = {
            "num_trades": self.metrics.get("num_trades", 4),
            "win_rate": self.metrics.get("win_rate", 0.5),
            "avg_pnl": self.metrics.get("avg_pnl", 25.0),
            "total_pnl": self.metrics.get("total_pnl", 100.0),
            "sharpe_ratio": self.metrics.get("sharpe_ratio", 1.0),
            "max_drawdown": self.metrics.get("max_drawdown", 100.0),
        }
        return result


class TestValidator:
    def test_in_sync_no_divergence(self, db):
        # 8 trades with PnL pattern (+50, -30, +100, -20) ×2 → win_rate=0.5,
        # avg_pnl=25, max_drawdown=30, sharpe ~ 3.35 (annualized at hold_days=5).
        for i in range(2):
            db.add(_trade(50, days_ago=20 - i * 8))
            db.add(_trade(-30, days_ago=18 - i * 8))
            db.add(_trade(100, days_ago=16 - i * 8))
            db.add(_trade(-20, days_ago=14 - i * 8))
        db.commit()

        v = PaperValidator(db, backtester=StubBacktester(
            num_trades=8, win_rate=0.5, avg_pnl=25.0, sharpe_ratio=3.4, max_drawdown=30.0,
        ))
        report = v.validate(date.today() - timedelta(days=30), date.today())
        assert report["paper"]["num_trades"] == 8
        assert report["divergences"] == []
        assert report["ok"] is True

    def test_flags_winrate_divergence(self, db):
        for _ in range(5):
            db.add(_trade(50))  # all winners → win_rate 1.0
        db.commit()

        v = PaperValidator(db, backtester=StubBacktester(win_rate=0.5))
        report = v.validate(date.today() - timedelta(days=30), date.today())
        flagged = {d["metric"] for d in report["divergences"]}
        assert "win_rate" in flagged
        assert report["ok"] is False

    def test_window_filter(self, db):
        # Trade closed 50 days ago — should be excluded from a 7-day window
        db.add(_trade(50, days_ago=50))
        db.commit()

        v = PaperValidator(db, backtester=StubBacktester(num_trades=0, avg_pnl=0, win_rate=0))
        report = v.validate(date.today() - timedelta(days=7), date.today())
        assert report["paper"]["num_trades"] == 0

    def test_rejects_invalid_window(self, db):
        v = PaperValidator(db, backtester=StubBacktester())
        with pytest.raises(ValueError):
            v.validate(date(2026, 4, 10), date(2026, 4, 1))

    def test_format_report_renders(self, db):
        db.add(_trade(50))
        db.commit()
        v = PaperValidator(db, backtester=StubBacktester())
        out = format_report(v.validate(date.today() - timedelta(days=7), date.today()))
        assert "Paper-vs-Backtest" in out
        assert "win_rate" in out


def test_threshold_constant():
    # Sanity: threshold matches the >10% spec
    assert DIVERGENCE_THRESHOLD == pytest.approx(0.10)


class TestSpyBenchmark:
    """SPY buy-and-hold benchmark line (audit 2026-07-14): ~90% of retail
    algos underperform buy-and-hold — the report must say which side we're on."""

    def _seed_spy(self, db, start_close: float, end_close: float, start: date, end: date):
        from src.db.models import PriceHistory
        db.add(Stock(ticker="SPY"))
        db.add(PriceHistory(ticker="SPY", date=start, close=start_close))
        db.add(PriceHistory(ticker="SPY", date=end, close=end_close))
        db.commit()

    def test_benchmark_beats_spy(self, db):
        start, end = date(2026, 6, 1), date(2026, 6, 30)
        self._seed_spy(db, 700.0, 707.0, start, end)  # SPY +1%
        # One closed trade in-window: +$50 on $1000 deployed = +5%
        t = _trade(50.0)
        t.closed_at = datetime(2026, 6, 15)
        t.opened_at = datetime(2026, 6, 10)
        db.add(t)
        db.commit()

        v = PaperValidator(db, backtester=StubBacktester())
        report = v.validate(start, end)
        bench = report["benchmark"]
        assert bench["spy_return_pct"] == pytest.approx(0.01)
        assert bench["paper_return_on_deployed"] == pytest.approx(0.05)
        assert bench["deployed_capital"] == 1000.0
        assert bench["beats_spy"] is True
        assert "BEATS SPY" in format_report(report)

    def test_benchmark_trails_spy(self, db):
        start, end = date(2026, 6, 1), date(2026, 6, 30)
        self._seed_spy(db, 700.0, 721.0, start, end)  # SPY +3%
        t = _trade(-20.0)
        t.closed_at = datetime(2026, 6, 15)
        t.opened_at = datetime(2026, 6, 10)
        db.add(t)
        db.commit()

        report = PaperValidator(db, backtester=StubBacktester()).validate(start, end)
        assert report["benchmark"]["beats_spy"] is False
        assert "TRAILS SPY" in format_report(report)

    def test_benchmark_none_without_spy_data(self, db):
        start, end = date(2026, 6, 1), date(2026, 6, 30)
        report = PaperValidator(db, backtester=StubBacktester()).validate(start, end)
        bench = report["benchmark"]
        assert bench["spy_return_pct"] is None
        assert bench["beats_spy"] is None
