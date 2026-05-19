"""Tests for ExecutionEngine."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Alert, Base, Recommendation, Stock, TradingLog
from src.services.execution_engine import ExecutionEngine
from src.services.order_mapper import AlpacaOrderParams, OrderMapper


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _make_rec(db, ticker="AAPL", score=0.85, strategy="short"):
    db.add(Stock(ticker=ticker))
    db.commit()
    rec = Recommendation(
        ticker=ticker, date=date.today(), strategy=strategy,
        score=score, entry_price=150.0, stop_loss=157.5,
        target_price=135.0, position_size=3000.0, max_loss=250.0,
    )
    db.add(rec)
    db.commit()
    return rec


def _mock_alpaca(market_open=True):
    client = MagicMock()
    client.is_market_open.return_value = market_open
    client.get_account.return_value = {
        "equity": 25000.0, "buying_power": 20000.0,
        "cash": 15000.0, "day_trade_count": 0,
    }
    client.submit_bracket_order.return_value = {"order_id": "test-order-001"}
    client.submit_order.return_value = {"order_id": "test-order-002"}
    client.close_position.return_value = {"ticker": "AAPL", "status": "closing", "order_id": "close-001"}
    client.cancel_all_orders.return_value = {"canceled": 2}
    client.close_all_positions.return_value = [{"ticker": "AAPL", "status": "closing"}]
    return client


def _settings(**overrides):
    s = MagicMock()
    s.auto_execute_enabled = overrides.get("auto_execute_enabled", True)
    s.min_score_threshold = overrides.get("min_score_threshold", 0.7)
    s.trading_mode = overrides.get("trading_mode", "paper")
    s.max_daily_loss = overrides.get("max_daily_loss", 200.0)
    s.max_open_positions = overrides.get("max_open_positions", 5)
    s.max_position_size = overrides.get("max_position_size", 1000.0)
    s.effective_per_trade_cap = overrides.get("effective_per_trade_cap", s.max_position_size)
    s.max_daily_orders = overrides.get("max_daily_orders", 20)
    s.allowed_hours_only = overrides.get("allowed_hours_only", False)
    s.blocked_tickers = overrides.get("blocked_tickers", [])
    return s


class TestExecuteRecommendations:
    def test_submits_eligible_recommendation(self):
        db = _make_db()
        rec = _make_rec(db)
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca)
                results = engine.execute_recommendations()

        assert len(results) == 1
        assert results[0]["status"] == "submitted"
        assert results[0]["order_id"] == "test-order-001"

    def test_skips_when_disabled(self):
        db = _make_db()
        _make_rec(db)
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings(auto_execute_enabled=False)):
            engine = ExecutionEngine(db, alpaca=alpaca)
            results = engine.execute_recommendations()

        assert results == []

    def test_blocks_below_threshold(self):
        db = _make_db()
        _make_rec(db, score=0.5)
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings(min_score_threshold=0.7)):
            engine = ExecutionEngine(db, alpaca=alpaca)
            results = engine.execute_recommendations()

        assert results == []

    def test_safety_rail_blocks(self):
        db = _make_db()
        _make_rec(db)
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings(trading_mode="disabled")):
                engine = ExecutionEngine(db, alpaca=alpaca)
                results = engine.execute_recommendations()

        assert len(results) == 1
        assert results[0]["status"] == "blocked"

    def test_logs_submission(self):
        db = _make_db()
        _make_rec(db)
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca)
                engine.execute_recommendations()

        logs = db.query(TradingLog).filter_by(action="submit").all()
        assert len(logs) == 1
        assert logs[0].order_id == "test-order-001"

    def test_creates_alert_on_submission(self):
        db = _make_db()
        _make_rec(db)
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca)
                engine.execute_recommendations()

        alerts = db.query(Alert).all()
        assert len(alerts) == 1
        assert "SUBMITTED" in alerts[0].message


class TestExecuteById:
    def test_executes_single(self):
        db = _make_db()
        rec = _make_rec(db)
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca)
                result = engine.execute_recommendation_by_id(rec.id)

        assert result["status"] == "submitted"

    def test_not_found(self):
        db = _make_db()
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            engine = ExecutionEngine(db, alpaca=alpaca)
            result = engine.execute_recommendation_by_id(999)

        assert result["status"] == "error"
        assert "not found" in result["reason"].lower()


class TestClosePosition:
    def test_close_single(self):
        db = _make_db()
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            engine = ExecutionEngine(db, alpaca=alpaca)
            result = engine.close_position("AAPL")

        assert result["status"] == "closing"
        alpaca.close_position.assert_called_once_with("AAPL")


class TestEmergencyClose:
    def test_closes_all(self):
        db = _make_db()
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            engine = ExecutionEngine(db, alpaca=alpaca)
            result = engine.close_all_positions()

        assert result["canceled_orders"] == 2
        assert result["closing_positions"] == 1
        alpaca.cancel_all_orders.assert_called_once()
        alpaca.close_all_positions.assert_called_once()


class TestExecutionLog:
    def test_returns_log_entries(self):
        db = _make_db()
        db.add(TradingLog(ticker="AAPL", action="submit", strategy="short", order_id="ord-1", passed_safety=1))
        db.add(TradingLog(ticker="TSLA", action="block", strategy="short", reason="disabled", passed_safety=0))
        db.commit()

        alpaca = _mock_alpaca()
        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            engine = ExecutionEngine(db, alpaca=alpaca)
            log = engine.get_execution_log()

        assert len(log) == 2
        assert log[0]["action"] in ("submit", "block")
