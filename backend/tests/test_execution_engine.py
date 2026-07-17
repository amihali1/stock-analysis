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
    client.submit_spread_order.return_value = {"order_id": "test-mleg-001"}
    client.get_option_quotes.return_value = {}
    client.get_positions.return_value = []
    return client


def _settings(**overrides):
    s = MagicMock()
    s.alpaca_trading_enabled = overrides.get("alpaca_trading_enabled", True)
    s.auto_execute_enabled = overrides.get("auto_execute_enabled", True)
    s.min_score_threshold = overrides.get("min_score_threshold", 0.7)
    s.min_score_threshold_bear = overrides.get("min_score_threshold_bear", 0.30)
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

    def test_env_kill_switch_blocks_all_submission(self):
        """ALPACA_TRADING_ENABLED=false must block orders even when the DB
        auto_execute_enabled runtime toggle is on (two-key design). Regression:
        the env flag was dead code until 2026-07-02 — read by nothing."""
        db = _make_db()
        _make_rec(db)
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings(alpaca_trading_enabled=False)):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca)
                results = engine.execute_recommendations()

        assert len(results) == 1
        assert results[0]["status"] == "blocked"
        assert "ALPACA_TRADING_ENABLED" in results[0]["reason"]
        alpaca.submit_bracket_order.assert_not_called()
        alpaca.submit_order.assert_not_called()
        # Block is auditable in the trading log
        log = db.query(TradingLog).filter_by(action="block").one()
        assert "kill-switch" in log.reason

    def test_kill_switch_does_not_gate_position_close(self):
        """Closing existing positions must stay possible with the switch off."""
        db = _make_db()
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings(alpaca_trading_enabled=False)):
            engine = ExecutionEngine(db, alpaca=alpaca)
            result = engine.close_position("AAPL")

        assert result.get("status") == "closing"
        alpaca.close_position.assert_called_once_with("AAPL")

    def test_blocks_below_threshold(self):
        # _make_rec defaults direction="short", so the bear floor applies
        # (per-direction floors since 2026-07-10).
        db = _make_db()
        _make_rec(db, score=0.5)
        alpaca = _mock_alpaca()

        cfg = _settings(min_score_threshold=0.7, min_score_threshold_bear=0.7)
        with patch("src.services.execution_engine.get_settings", return_value=cfg):
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


class TestPerDirectionScoreFloor:
    """2026-07-10: direction-blind exec floor excluded every pair_short rec
    (bear composites 0.31-0.35 vs the 0.45 bull floor — structural base-rate
    gap, not a conviction gap). Bear recs get their own lower floor."""

    def test_bear_rec_passes_bear_floor_below_bull_floor(self):
        import json
        db = _make_db()
        db.add(Stock(ticker="SNAP"))
        db.commit()
        db.add(Recommendation(
            ticker="SNAP", date=date.today(), strategy="pair_short",
            direction="short", score=0.33, entry_price=10.0,
            position_size=900.0, max_loss=20.0,
            legs_json=json.dumps([
                {"leg": "short", "ticker": "SNAP", "qty": 30, "entry": 10.0},
                {"leg": "hedge", "ticker": "SPY", "qty": 0.4, "entry": 750.0},
            ]),
        ))
        db.commit()
        alpaca = _mock_alpaca()
        alpaca.submit_order.side_effect = [
            {"order_id": "s1"}, {"order_id": "h1"},
        ]
        cfg = _settings(min_score_threshold=0.45, min_score_threshold_bear=0.30)

        with patch("src.services.execution_engine.get_settings", return_value=cfg):
            with patch("src.services.safety_rails.get_settings", return_value=cfg):
                results = ExecutionEngine(db, alpaca=alpaca).execute_recommendations()

        assert len(results) == 1
        assert results[0]["status"] == "submitted"

    def test_bull_rec_below_bull_floor_still_filtered(self):
        db = _make_db()
        db.add(Stock(ticker="AAPL"))
        db.commit()
        db.add(Recommendation(
            ticker="AAPL", date=date.today(), strategy="long",
            direction="long", score=0.40, entry_price=150.0,  # bull, below 0.45
            position_size=900.0,
        ))
        db.commit()
        alpaca = _mock_alpaca()
        cfg = _settings(min_score_threshold=0.45, min_score_threshold_bear=0.30)

        with patch("src.services.execution_engine.get_settings", return_value=cfg):
            results = ExecutionEngine(db, alpaca=alpaca).execute_recommendations()

        assert results == []

    def test_bear_rec_below_bear_floor_filtered(self):
        db = _make_db()
        db.add(Stock(ticker="SNAP"))
        db.commit()
        db.add(Recommendation(
            ticker="SNAP", date=date.today(), strategy="pair_short",
            direction="short", score=0.25, entry_price=10.0, position_size=900.0,
        ))
        db.commit()
        alpaca = _mock_alpaca()
        cfg = _settings(min_score_threshold=0.45, min_score_threshold_bear=0.30)

        with patch("src.services.execution_engine.get_settings", return_value=cfg):
            results = ExecutionEngine(db, alpaca=alpaca).execute_recommendations()

        assert results == []


class TestPairShortExecution:
    def _make_pair_rec(self, db):
        import json
        db.add(Stock(ticker="NVDA"))
        db.commit()
        rec = Recommendation(
            ticker="NVDA", date=date.today(), strategy="pair_short",
            direction="short", score=0.85, entry_price=100.0, stop_loss=105.0,
            target_price=90.0, position_size=2500.0, max_loss=50.0,
            legs_json=json.dumps([
                {"leg": "short", "ticker": "NVDA", "qty": 10, "entry": 100.0},
                {"leg": "hedge", "ticker": "SPY", "qty": 2, "entry": 500.0},
            ]),
        )
        db.add(rec)
        db.commit()
        return rec

    def test_pair_submits_both_legs(self):
        db = _make_db()
        self._make_pair_rec(db)
        alpaca = _mock_alpaca()
        alpaca.submit_order.side_effect = [
            {"order_id": "short-leg-1"}, {"order_id": "hedge-leg-1"},
        ]

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca)
                results = engine.execute_recommendations()

        assert results[0]["status"] == "submitted"
        assert results[0]["order_id"] == "short-leg-1"
        assert results[0]["hedge_order_id"] == "hedge-leg-1"
        calls = alpaca.submit_order.call_args_list
        assert calls[0].kwargs["ticker"] == "NVDA" and calls[0].kwargs["side"] == "sell"
        assert calls[1].kwargs["ticker"] == "SPY" and calls[1].kwargs["side"] == "buy"
        # PaperTrade persisted with legs
        pt = db.query(PaperTrade).one()
        assert pt.strategy == "pair_short"
        assert "hedge" in pt.legs_json

    def test_pair_hedge_failure_unwinds_short(self):
        """Never leave a naked short: hedge leg fails -> short bought back."""
        db = _make_db()
        self._make_pair_rec(db)
        alpaca = _mock_alpaca()
        alpaca.submit_order.side_effect = [
            {"order_id": "short-leg-1"},          # short sell ok
            RuntimeError("hedge rejected"),        # hedge buy fails
            {"order_id": "unwind-1"},              # short buyback
        ]

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca)
                results = engine.execute_recommendations()

        assert results[0]["status"] == "error"
        calls = alpaca.submit_order.call_args_list
        assert len(calls) == 3
        assert calls[2].kwargs["ticker"] == "NVDA" and calls[2].kwargs["side"] == "buy"
        assert db.query(PaperTrade).count() == 0

    def test_pair_malformed_legs_skipped(self):
        db = _make_db()
        db.add(Stock(ticker="NVDA"))
        db.commit()
        db.add(Recommendation(
            ticker="NVDA", date=date.today(), strategy="pair_short",
            direction="short", score=0.85, entry_price=100.0,
            position_size=2500.0, legs_json="not json",
        ))
        db.commit()
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca)
                results = engine.execute_recommendations()

        assert results[0]["status"] == "skipped"
        alpaca.submit_order.assert_not_called()


class TestSpreadLiveQuoteRefresh:
    """2026-07-15: recs are generated 07:30 ET pre-market when chain quotes
    are bid=0/ask=0, so legs_json quotes are dead by design. The engine must
    re-quote legs from Alpaca at exec time; credit spreads with no usable
    live quotes are dropped instead of falling into a giveaway debit limit."""

    _EXPIRY = date(2026, 8, 14)

    def _make_credit_spread_rec(self, db):
        import json
        db.add(Stock(ticker="MRNA"))
        db.commit()
        rec = Recommendation(
            ticker="MRNA", date=date.today(), strategy="bull_spread",
            direction="long", score=0.85, entry_price=67.44,
            position_size=80.0, max_loss=80.0, contracts=1,
            expiry=self._EXPIRY,
            legs_json=json.dumps([
                {"option_type": "put", "action": "sell", "strike": 66.0,
                 "premium": 6.66, "contracts": 1, "bid": None, "ask": None},
                {"option_type": "put", "action": "buy", "strike": 62.0,
                 "premium": 4.7, "contracts": 1, "bid": None, "ask": None},
            ]),
        )
        db.add(rec)
        db.commit()
        return rec

    def test_live_quotes_price_credit_mleg(self):
        db = _make_db()
        self._make_credit_spread_rec(db)
        alpaca = _mock_alpaca()
        alpaca.get_option_quotes.return_value = {
            "MRNA260814P00066000": {"bid": 2.90, "ask": 3.10},
            "MRNA260814P00062000": {"bid": 0.90, "ask": 1.10},
        }

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca, mapper=OrderMapper(max_position=1000))
                results = engine.execute_recommendations()

        assert results[0]["status"] == "submitted"
        alpaca.get_option_quotes.assert_called_once_with(
            ["MRNA260814P00066000", "MRNA260814P00062000"]
        )
        # Credit mid 2.00, natural 1.80 → marketable 1.93, negative-limit MLEG
        kwargs = alpaca.submit_spread_order.call_args.kwargs
        assert kwargs["limit_price"] == -1.93

    def test_credit_spread_without_live_quotes_skipped(self):
        # Regression: with dead quotes the order used to fall through to the
        # cost-derived positive limit and fill at a debit (MRNA 2026-07-15).
        db = _make_db()
        self._make_credit_spread_rec(db)
        alpaca = _mock_alpaca()
        alpaca.get_option_quotes.return_value = {}

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca, mapper=OrderMapper(max_position=1000))
                results = engine.execute_recommendations()

        assert results[0]["status"] == "skipped"
        alpaca.submit_spread_order.assert_not_called()

    def test_stale_rec_quotes_overwritten_not_trusted(self):
        # Legs written with rec-time quotes must be re-quoted, not reused:
        # only the live quote payload should reach the mapper.
        import json
        db = _make_db()
        db.add(Stock(ticker="MRNA"))
        db.commit()
        db.add(Recommendation(
            ticker="MRNA", date=date.today(), strategy="bull_spread",
            direction="long", score=0.85, entry_price=67.44,
            position_size=80.0, max_loss=80.0, contracts=1,
            expiry=self._EXPIRY,
            legs_json=json.dumps([
                {"option_type": "put", "action": "sell", "strike": 66.0,
                 "premium": 6.66, "contracts": 1, "bid": 9.0, "ask": 9.2},
                {"option_type": "put", "action": "buy", "strike": 62.0,
                 "premium": 4.7, "contracts": 1, "bid": 0.10, "ask": 0.20},
            ]),
        ))
        db.commit()
        alpaca = _mock_alpaca()
        alpaca.get_option_quotes.return_value = {
            "MRNA260814P00066000": {"bid": 2.90, "ask": 3.10},
            "MRNA260814P00062000": {"bid": 0.90, "ask": 1.10},
        }

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca, mapper=OrderMapper(max_position=1000))
                results = engine.execute_recommendations()

        assert results[0]["status"] == "submitted"
        # Priced from live quotes (credit 1.93), not the stale rec-time ones
        # (which would imply a ~8.8 credit and a different limit).
        kwargs = alpaca.submit_spread_order.call_args.kwargs
        assert kwargs["limit_price"] == -1.93


class TestOccPositionConflict:
    """2026-07-17: MRNA MLEG rejected 40310000 — the new spread's sell leg
    (8x 62P) shared an OCC symbol the book already held long (4x 62P from a
    prior spread). Alpaca nets per contract, so overlapping legs are treated
    as (partially) closing the existing position. Orders with any leg
    colliding with an open position must be skipped pre-submit."""

    _EXPIRY = date(2026, 8, 14)

    def _make_credit_spread_rec(self, db, sell_strike=62.0, buy_strike=60.0):
        import json
        db.add(Stock(ticker="MRNA"))
        db.commit()
        rec = Recommendation(
            ticker="MRNA", date=date.today(), strategy="bull_spread",
            direction="long", score=0.85, entry_price=65.0,
            position_size=160.0, max_loss=160.0, contracts=1,
            expiry=self._EXPIRY,
            legs_json=json.dumps([
                {"option_type": "put", "action": "sell", "strike": sell_strike,
                 "premium": 5.47, "contracts": 1},
                {"option_type": "put", "action": "buy", "strike": buy_strike,
                 "premium": 4.65, "contracts": 1},
            ]),
        )
        db.add(rec)
        db.commit()
        return rec

    def _quotes(self, sell_strike=62.0, buy_strike=60.0):
        def occ(strike):
            return f"MRNA260814P{int(strike * 1000):08d}"
        return {
            occ(sell_strike): {"bid": 2.90, "ask": 3.10},
            occ(buy_strike): {"bid": 0.90, "ask": 1.10},
        }

    def test_overlapping_leg_skipped(self):
        db = _make_db()
        self._make_credit_spread_rec(db)
        alpaca = _mock_alpaca()
        alpaca.get_option_quotes.return_value = self._quotes()
        alpaca.get_positions.return_value = [
            {"ticker": "MRNA260814P00062000", "qty": 4.0, "side": "long",
             "market_value": 2220.0},
        ]

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca, mapper=OrderMapper(max_position=1000))
                results = engine.execute_recommendations()

        assert results[0]["status"] == "skipped"
        assert "MRNA260814P00062000" in results[0]["reason"]
        alpaca.submit_spread_order.assert_not_called()
        log = db.query(TradingLog).filter_by(action="skip").one()
        assert "overlaps open option position" in log.reason

    def test_no_overlap_submits(self):
        # Same underlying held at OTHER strikes is fine — only exact OCC
        # symbol overlap collides.
        db = _make_db()
        self._make_credit_spread_rec(db)
        alpaca = _mock_alpaca()
        alpaca.get_option_quotes.return_value = self._quotes()
        alpaca.get_positions.return_value = [
            {"ticker": "MRNA260814P00066000", "qty": -4.0, "side": "short",
             "market_value": -3500.0},
        ]

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca, mapper=OrderMapper(max_position=1000))
                results = engine.execute_recommendations()

        assert results[0]["status"] == "submitted"

    def test_positions_fetch_failure_waives_check(self):
        # API blip must not kill every option trade — the order proceeds and
        # either submits cleanly or draws the broker rejection (logged error).
        db = _make_db()
        self._make_credit_spread_rec(db)
        alpaca = _mock_alpaca()
        alpaca.get_option_quotes.return_value = self._quotes()
        alpaca.get_positions.side_effect = RuntimeError("api down")

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca, mapper=OrderMapper(max_position=1000))
                results = engine.execute_recommendations()

        assert results[0]["status"] == "submitted"

    def test_stock_orders_do_not_fetch_positions(self):
        # Equity orders carry no OCC symbols; the overlap check must not add
        # a positions round-trip to them.
        db = _make_db()
        _make_rec(db)
        alpaca = _mock_alpaca()

        with patch("src.services.execution_engine.get_settings", return_value=_settings()):
            with patch("src.services.safety_rails.get_settings", return_value=_settings()):
                engine = ExecutionEngine(db, alpaca=alpaca)
                results = engine.execute_recommendations()

        assert results[0]["status"] == "submitted"
        alpaca.get_positions.assert_not_called()


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
