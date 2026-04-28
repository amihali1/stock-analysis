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
