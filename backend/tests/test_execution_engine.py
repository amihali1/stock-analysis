"""Tests for ExecutionEngine."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Alert, Base, PaperTrade, Recommendation, Stock, TradingLog
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


class TestDedup:
    def test_skips_if_already_submitted_today(self):
        db = _make_db()
        rec = _make_rec(db)
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca)
                first = engine.execute_recommendations()
                second = engine.execute_recommendations()

        assert first[0]["status"] == "submitted"
        assert second[0]["status"] == "duplicate"
        assert second[0]["order_id"] == "test-order-001"
        assert alpaca.submit_bracket_order.call_count == 1

    def test_different_strategy_same_ticker_not_deduped(self):
        db = _make_db()
        _make_rec(db, ticker="AAPL", strategy="short")
        # Pre-existing submit for AAPL options should not block AAPL short
        db.add(TradingLog(
            ticker="AAPL", action="submit", strategy="options",
            order_id="prev-001", passed_safety=1,
        ))
        db.commit()
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca)
                results = engine.execute_recommendations()

        assert results[0]["status"] == "submitted"


class TestPaperTradePersist:
    def test_paper_trade_row_created_on_submit(self):
        db = _make_db()
        rec = _make_rec(db)
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca)
                engine.execute_recommendations()

        trades = db.query(PaperTrade).all()
        assert len(trades) == 1
        assert trades[0].ticker == rec.ticker
        assert trades[0].strategy == rec.strategy
        assert trades[0].status == "open"
        assert trades[0].score == rec.score


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


class TestMonitorFractionalExits:
    """Fractional longs ship with no broker bracket. monitor_fractional_exits
    must poll non-integer-qty positions, compare to the originating rec's
    stop/target, and close on breach."""

    def _rec(self, db, ticker="AAPL", stop=142.0, target=165.0):
        db.add(Stock(ticker=ticker))
        db.commit()
        rec = Recommendation(
            ticker=ticker, date=date.today(), strategy="long",
            score=0.85, entry_price=150.0, stop_loss=stop,
            target_price=target, position_size=250.0, max_loss=20.0,
            direction="long",
        )
        db.add(rec)
        db.commit()
        return rec

    def _position(self, ticker="AAPL", qty=0.625, current_price=150.0, side="long"):
        return {
            "ticker": ticker, "qty": qty, "side": side,
            "avg_entry_price": 150.0, "current_price": current_price,
            "market_value": qty * current_price, "unrealized_pl": 0.0,
        }

    def test_skips_integer_positions(self):
        db = _make_db()
        self._rec(db)
        alpaca = _mock_alpaca()
        alpaca.get_positions.return_value = [self._position(qty=1.0, current_price=130.0)]

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            engine = ExecutionEngine(db, alpaca=alpaca)
            results = engine.monitor_fractional_exits()

        assert results == []
        alpaca.close_position.assert_not_called()

    def test_closes_when_stop_breached(self):
        db = _make_db()
        self._rec(db, stop=142.0, target=165.0)
        alpaca = _mock_alpaca()
        alpaca.get_positions.return_value = [self._position(qty=0.625, current_price=140.0)]

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            engine = ExecutionEngine(db, alpaca=alpaca)
            results = engine.monitor_fractional_exits()

        assert len(results) == 1
        assert results[0]["status"] == "closed"
        assert "stop hit" in results[0]["reason"]
        alpaca.close_position.assert_called_once_with("AAPL")

    def test_closes_when_target_breached(self):
        db = _make_db()
        self._rec(db, stop=142.0, target=165.0)
        alpaca = _mock_alpaca()
        alpaca.get_positions.return_value = [self._position(qty=0.625, current_price=166.0)]

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            engine = ExecutionEngine(db, alpaca=alpaca)
            results = engine.monitor_fractional_exits()

        assert len(results) == 1
        assert results[0]["status"] == "closed"
        assert "target hit" in results[0]["reason"]

    def test_in_band_no_action(self):
        db = _make_db()
        self._rec(db, stop=142.0, target=165.0)
        alpaca = _mock_alpaca()
        alpaca.get_positions.return_value = [self._position(qty=0.625, current_price=150.0)]

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            engine = ExecutionEngine(db, alpaca=alpaca)
            results = engine.monitor_fractional_exits()

        assert len(results) == 1
        assert results[0]["status"] == "in_band"
        alpaca.close_position.assert_not_called()

    def test_no_rec_skipped(self):
        db = _make_db()
        # Position exists but no Recommendation row for ticker
        alpaca = _mock_alpaca()
        alpaca.get_positions.return_value = [self._position(qty=0.625, current_price=140.0)]

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            engine = ExecutionEngine(db, alpaca=alpaca)
            results = engine.monitor_fractional_exits()

        assert len(results) == 1
        assert results[0]["status"] == "skipped"
        assert results[0]["reason"] == "no_rec"
        alpaca.close_position.assert_not_called()

    def test_logs_and_alerts_on_close(self):
        db = _make_db()
        self._rec(db, stop=142.0, target=165.0)
        alpaca = _mock_alpaca()
        alpaca.get_positions.return_value = [self._position(qty=0.625, current_price=140.0)]

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            engine = ExecutionEngine(db, alpaca=alpaca)
            engine.monitor_fractional_exits()

        logs = db.query(TradingLog).filter_by(action="fractional_exit").all()
        assert len(logs) == 1
        assert logs[0].ticker == "AAPL"

        alerts = db.query(Alert).all()
        assert len(alerts) == 1
        assert "FRACTIONAL_EXIT" in alerts[0].message


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
