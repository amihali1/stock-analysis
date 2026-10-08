"""Tests for TradingSafetyRails."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, TradingLog, PaperTrade
from src.services.safety_rails import TradingSafetyRails
from src.services.order_mapper import AlpacaOrderParams


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _make_order(**kwargs):
    defaults = {
        "ticker": "AAPL", "qty": 3, "side": "sell",
        "order_type": "limit", "limit_price": 150.0,
        "strategy": "short", "dry_run": False,
    }
    defaults.update(kwargs)
    return AlpacaOrderParams(**defaults)


def _make_settings(**overrides):
    s = MagicMock()
    s.trading_mode = overrides.get("trading_mode", "paper")
    s.max_daily_loss = overrides.get("max_daily_loss", 200.0)
    s.max_open_positions = overrides.get("max_open_positions", 5)
    s.per_direction_position_reserve = overrides.get("per_direction_position_reserve", 0.0)
    s.max_position_size = overrides.get("max_position_size", 1000.0)
    s.effective_per_trade_cap = overrides.get("effective_per_trade_cap", s.max_position_size)
    s.max_daily_orders = overrides.get("max_daily_orders", 20)
    s.allowed_hours_only = overrides.get("allowed_hours_only", True)
    s.blocked_tickers = overrides.get("blocked_tickers", [])
    return s


class TestModeCheck:
    def test_disabled_blocks_all(self):
        db = _make_db()
        with patch("src.services.safety_rails.get_settings", return_value=_make_settings(trading_mode="disabled")):
            rails = TradingSafetyRails(db)
            ok, reason = rails.check_order(_make_order())
            assert ok is False
            assert "disabled" in reason

    def test_paper_allows(self):
        db = _make_db()
        with patch("src.services.safety_rails.get_settings", return_value=_make_settings(trading_mode="paper")):
            rails = TradingSafetyRails(db)
            ok, _ = rails.check_order(_make_order())
            assert ok is True

    def test_live_allows(self):
        db = _make_db()
        with patch("src.services.safety_rails.get_settings", return_value=_make_settings(trading_mode="live")):
            rails = TradingSafetyRails(db)
            ok, _ = rails.check_order(_make_order())
            assert ok is True


class TestMarketHours:
    def test_blocks_when_closed(self):
        db = _make_db()
        with patch("src.services.safety_rails.get_settings", return_value=_make_settings()):
            rails = TradingSafetyRails(db)
            ok, reason = rails.check_order(_make_order(), market_open=False)
            assert ok is False
            assert "closed" in reason.lower()

    def test_allows_when_hours_check_disabled(self):
        db = _make_db()
        with patch("src.services.safety_rails.get_settings", return_value=_make_settings(allowed_hours_only=False)):
            rails = TradingSafetyRails(db)
            ok, _ = rails.check_order(_make_order(), market_open=False)
            assert ok is True


class TestBlockedTicker:
    def test_blocks_ticker(self):
        db = _make_db()
        with patch("src.services.safety_rails.get_settings", return_value=_make_settings(blocked_tickers=["TQQQ", "SQQQ"])):
            rails = TradingSafetyRails(db)
            ok, reason = rails.check_order(_make_order(ticker="TQQQ"))
            assert ok is False
            assert "blocked" in reason.lower()

    def test_allows_unblocked_ticker(self):
        db = _make_db()
        with patch("src.services.safety_rails.get_settings", return_value=_make_settings(blocked_tickers=["TQQQ"])):
            rails = TradingSafetyRails(db)
            ok, _ = rails.check_order(_make_order(ticker="AAPL"))
            assert ok is True


class TestPositionLimit:
    def test_blocks_at_limit(self):
        db = _make_db()
        # Add 5 open paper trades
        from src.db.models import Stock
        db.add(Stock(ticker="AAPL"))
        db.commit()
        for i in range(5):
            db.add(PaperTrade(ticker="AAPL", strategy="short", status="open", entry_price=150))
        db.commit()

        with patch("src.services.safety_rails.get_settings", return_value=_make_settings(max_open_positions=5)):
            rails = TradingSafetyRails(db)
            ok, reason = rails.check_order(_make_order())
            assert ok is False
            assert "position limit" in reason.lower()

    def test_allows_under_limit(self):
        db = _make_db()
        with patch("src.services.safety_rails.get_settings", return_value=_make_settings(max_open_positions=5)):
            rails = TradingSafetyRails(db)
            ok, _ = rails.check_order(_make_order())
            assert ok is True


class TestDirectionSlotReserve:
    """Per-direction slot reserve on the count cap (mirror of the capital reserve)."""

    def _seed(self, db, longs=0, shorts=0):
        from src.db.models import Stock
        db.add(Stock(ticker="AAPL"))
        for _ in range(longs):
            db.add(PaperTrade(ticker="AAPL", direction="long", strategy="long", status="open", entry_price=150))
        for _ in range(shorts):
            db.add(PaperTrade(ticker="AAPL", direction="short", strategy="pair_short", status="open", entry_price=150))
        db.commit()

    def _settings(self):
        # 10-slot cap, 0.30 reserve => 3 slots held for the peer, bull ceiling 7.
        return _make_settings(max_open_positions=10, per_direction_position_reserve=0.30)

    def test_bull_blocked_at_ceiling_when_bear_pending(self):
        db = _make_db()
        self._seed(db, longs=7)  # ceiling = 10 - 3 = 7
        with patch("src.services.safety_rails.get_settings", return_value=self._settings()):
            rails = TradingSafetyRails(db)
            ok, reason = rails.check_order(_make_order(), direction="long", peer_pending=True)
            assert ok is False
            assert "slot reserve" in reason.lower()

    def test_bull_allowed_when_no_bear_pending(self):
        db = _make_db()
        self._seed(db, longs=7)
        with patch("src.services.safety_rails.get_settings", return_value=self._settings()):
            rails = TradingSafetyRails(db)
            ok, _ = rails.check_order(_make_order(), direction="long", peer_pending=False)
            assert ok is True  # one-sided day: reserved slots not idled

    def test_reserve_zero_disables(self):
        db = _make_db()
        self._seed(db, longs=7)
        settings = _make_settings(max_open_positions=10, per_direction_position_reserve=0.0)
        with patch("src.services.safety_rails.get_settings", return_value=settings):
            rails = TradingSafetyRails(db)
            ok, _ = rails.check_order(_make_order(), direction="long", peer_pending=True)
            assert ok is True

    def test_bear_not_blocked_by_own_reserve(self):
        db = _make_db()
        self._seed(db, longs=7)  # bulls hold 7, no open bears
        with patch("src.services.safety_rails.get_settings", return_value=self._settings()):
            rails = TradingSafetyRails(db)
            ok, _ = rails.check_order(_make_order(), direction="short", peer_pending=True)
            assert ok is True  # bear can claim its reserved slots

    def test_ceiling_relaxes_once_peer_floor_filled(self):
        db = _make_db()
        self._seed(db, longs=4, shorts=3)  # bear floor (3) met => bulls may use rest
        with patch("src.services.safety_rails.get_settings", return_value=self._settings()):
            rails = TradingSafetyRails(db)
            ok, _ = rails.check_order(_make_order(), direction="long", peer_pending=True)
            assert ok is True

    def test_hard_cap_still_blocks(self):
        db = _make_db()
        self._seed(db, longs=10)  # at hard cap
        with patch("src.services.safety_rails.get_settings", return_value=self._settings()):
            rails = TradingSafetyRails(db)
            ok, reason = rails.check_order(_make_order(), direction="short", peer_pending=True)
            assert ok is False
            assert "position limit" in reason.lower()


class TestDailyOrderLimit:
    def test_blocks_at_limit(self):
        db = _make_db()
        for _ in range(20):
            db.add(TradingLog(ticker="AAPL", action="submit", created_at=datetime.utcnow()))
        db.commit()

        with patch("src.services.safety_rails.get_settings", return_value=_make_settings(max_daily_orders=20)):
            rails = TradingSafetyRails(db)
            ok, reason = rails.check_order(_make_order())
            assert ok is False
            assert "order limit" in reason.lower()


class TestPositionSize:
    def test_blocks_oversized(self):
        db = _make_db()
        with patch("src.services.safety_rails.get_settings", return_value=_make_settings(max_position_size=1000)):
            rails = TradingSafetyRails(db)
            order = _make_order(qty=100, limit_price=100.0)  # 100 * 100 * 1.5 = 15000
            ok, reason = rails.check_order(order)
            assert ok is False
            assert "exceeds" in reason.lower()

    def test_allows_within_limit(self):
        db = _make_db()
        with patch("src.services.safety_rails.get_settings", return_value=_make_settings(max_position_size=1000)):
            rails = TradingSafetyRails(db)
            order = _make_order(qty=3, limit_price=150.0)  # 3 * 150 * 1.5 = 675
            ok, _ = rails.check_order(order)
            assert ok is True


class TestLogging:
    def test_blocked_orders_logged(self):
        db = _make_db()
        with patch("src.services.safety_rails.get_settings", return_value=_make_settings(trading_mode="disabled")):
            rails = TradingSafetyRails(db)
            rails.check_order(_make_order())

        logs = db.query(TradingLog).all()
        assert len(logs) == 1
        assert logs[0].action == "block"
        assert logs[0].passed_safety == 0

    def test_submission_logged(self):
        db = _make_db()
        with patch("src.services.safety_rails.get_settings", return_value=_make_settings()):
            rails = TradingSafetyRails(db)
            rails.log_submission(_make_order(), "order-123")

        logs = db.query(TradingLog).all()
        assert len(logs) == 1
        assert logs[0].action == "submit"
        assert logs[0].order_id == "order-123"


class TestConcentration:
    """Per-ticker and per-sector open-position caps (2026-10-08)."""

    def _seed(self, db, rows):
        from src.db.models import Stock
        for ticker, sector, n in rows:
            db.add(Stock(ticker=ticker, sector=sector))
            for _ in range(n):
                db.add(PaperTrade(ticker=ticker, direction="long", strategy="bull_spread",
                                  status="open", entry_price=100))
        db.commit()

    def _settings(self, **kw):
        s = _make_settings(max_open_positions=10, **kw)
        s.max_open_per_ticker = kw.get("max_open_per_ticker", 2)
        s.max_sector_fraction = kw.get("max_sector_fraction", 0.30)
        return s

    def _check(self, db, settings, ticker, underlying=None):
        with patch("src.services.safety_rails.get_settings", return_value=settings), \
             patch("src.services.trading_settings.get_settings", return_value=settings):
            rails = TradingSafetyRails(db)
            return rails.check_order(_make_order(ticker=ticker), direction="long", underlying=underlying)

    def test_ticker_cap_blocks_third_position(self):
        db = _make_db()
        self._seed(db, [("LMT", "Industrials", 2)])
        ok, reason = self._check(db, self._settings(), "LMT")
        assert ok is False and "Ticker concentration: 2/2" in reason

    def test_ticker_cap_uses_underlying_for_option_orders(self):
        db = _make_db()
        self._seed(db, [("LMT", "Industrials", 2)])
        ok, reason = self._check(db, self._settings(), "LMT261016C00565000", underlying="LMT")
        assert ok is False and "LMT" in reason

    def test_sector_cap_blocks_at_fraction_of_slots(self):
        db = _make_db()
        # 10 slots x 0.30 = 3 per sector
        self._seed(db, [("LMT", "Industrials", 1), ("NOC", "Industrials", 1), ("HON", "Industrials", 1),
                        ("GE", "Industrials", 0)])
        ok, reason = self._check(db, self._settings(), "GE")
        assert ok is False and "Sector concentration: 3/3 open in Industrials" in reason

    def test_other_sector_allowed(self):
        db = _make_db()
        self._seed(db, [("LMT", "Industrials", 1), ("NOC", "Industrials", 2), ("AAPL", "Technology", 0)])
        ok, _ = self._check(db, self._settings(), "AAPL")
        assert ok is True

    def test_no_sector_not_capped(self):
        db = _make_db()
        self._seed(db, [("XLE", None, 1), ("XLF", None, 1), ("XLK", None, 1), ("XLV", None, 0)])
        ok, _ = self._check(db, self._settings(), "XLV")
        assert ok is True

    def test_zero_disables(self):
        db = _make_db()
        self._seed(db, [("LMT", "Industrials", 3)])
        ok, _ = self._check(db, self._settings(max_open_per_ticker=0, max_sector_fraction=0.0), "LMT")
        assert ok is True
