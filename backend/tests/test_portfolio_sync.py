"""Tests for PortfolioSync."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import json
from datetime import date

from src.db.models import (
    AlpacaOrder,
    AlpacaPosition,
    Base,
    PaperTrade,
    PriceHistory,
    Stock,
)
from src.services.portfolio_sync import PortfolioSync


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _mock_client(positions=None, orders=None, account=None):
    client = MagicMock()
    client.get_positions.return_value = positions or []
    client.get_orders.return_value = orders or []
    client.get_account.return_value = account or {
        "equity": 25000.0,
        "buying_power": 20000.0,
        "cash": 15000.0,
        "day_trade_count": 0,
        "pattern_day_trader": False,
        "currency": "USD",
        "status": "ACTIVE",
    }
    return client


SAMPLE_POSITIONS = [
    {
        "ticker": "AAPL",
        "qty": 10.0,
        "side": "long",
        "avg_entry_price": 150.0,
        "current_price": 155.0,
        "market_value": 1550.0,
        "unrealized_pl": 50.0,
        "unrealized_plpc": 0.033,
        "change_today": 0.01,
    },
    {
        "ticker": "TSLA",
        "qty": 5.0,
        "side": "short",
        "avg_entry_price": 200.0,
        "current_price": 195.0,
        "market_value": -975.0,
        "unrealized_pl": 25.0,
        "unrealized_plpc": 0.025,
        "change_today": -0.02,
    },
]

SAMPLE_ORDERS = [
    {
        "order_id": "ord-001",
        "ticker": "AAPL",
        "side": "buy",
        "qty": 10.0,
        "type": "limit",
        "status": "filled",
        "limit_price": 150.0,
        "stop_price": None,
        "filled_price": 149.5,
        "filled_qty": 10.0,
        "submitted_at": "2026-04-14T09:30:00",
        "filled_at": "2026-04-14T09:31:00",
    },
    {
        "order_id": "ord-002",
        "ticker": "TSLA",
        "side": "sell",
        "qty": 5.0,
        "type": "market",
        "status": "filled",
        "limit_price": None,
        "stop_price": None,
        "filled_price": 200.0,
        "filled_qty": 5.0,
        "submitted_at": "2026-04-14T10:00:00",
        "filled_at": "2026-04-14T10:00:01",
    },
]


class TestSyncPositions:
    def test_syncs_positions_to_db(self):
        db = _make_db()
        client = _mock_client(positions=SAMPLE_POSITIONS)
        sync = PortfolioSync(db, client=client)

        count = sync.sync_positions()

        assert count == 2
        rows = db.query(AlpacaPosition).all()
        assert len(rows) == 2
        assert {r.ticker for r in rows} == {"AAPL", "TSLA"}

    def test_clears_stale_positions(self):
        db = _make_db()
        # Pre-populate with stale position
        db.add(AlpacaPosition(ticker="GOOG", qty=3, side="long"))
        db.commit()

        client = _mock_client(positions=SAMPLE_POSITIONS[:1])
        sync = PortfolioSync(db, client=client)
        sync.sync_positions()

        rows = db.query(AlpacaPosition).all()
        assert len(rows) == 1
        assert rows[0].ticker == "AAPL"

    def test_empty_positions(self):
        db = _make_db()
        db.add(AlpacaPosition(ticker="GOOG", qty=3, side="long"))
        db.commit()

        client = _mock_client(positions=[])
        sync = PortfolioSync(db, client=client)
        count = sync.sync_positions()

        assert count == 0
        assert db.query(AlpacaPosition).count() == 0


class TestCloseOrphanPaperTrades:
    """Orphan sweep with grace window (2026-07-07 incidents: submit-to-fill
    race closed MU/LRCX 5 min after submission; a transient get_positions
    response of 1/6 positions mass-closed 4 live trades)."""

    T0 = datetime(2026, 7, 7, 14, 0, 0)
    PAST_GRACE = T0 + timedelta(minutes=PortfolioSync.ORPHAN_GRACE_MINUTES)

    def _seed_stock(self, db, ticker):
        db.add(Stock(ticker=ticker))
        db.commit()

    def _sweep(self, db, client, at):
        sync = PortfolioSync(db, client=client)
        sync.sync_positions()
        return sync.close_orphan_paper_trades(now=at)

    def test_first_orphan_sighting_only_stamps_not_closes(self):
        db = _make_db()
        self._seed_stock(db, "GOOG")
        db.add(PaperTrade(ticker="GOOG", strategy="short", status="open", entry_price=100))
        db.commit()

        closed = self._sweep(db, _mock_client(positions=[], orders=[]), self.T0)

        assert closed == 0
        pt = db.query(PaperTrade).filter_by(ticker="GOOG").one()
        assert pt.status == "open"
        assert pt.orphan_seen_at == self.T0

    def test_closes_after_grace_window_persists(self):
        db = _make_db()
        self._seed_stock(db, "GOOG")
        db.add(PaperTrade(ticker="GOOG", strategy="short", status="open", entry_price=100))
        db.commit()

        client = _mock_client(positions=[], orders=[])
        self._sweep(db, client, self.T0)
        closed = self._sweep(db, client, self.PAST_GRACE)

        assert closed == 1
        pt = db.query(PaperTrade).filter_by(ticker="GOOG").one()
        assert pt.status == "closed"
        assert pt.closed_at is not None

    def test_transient_api_blip_does_not_close(self):
        """Position disappears for one sync, reappears — stamp must clear and
        the trade must survive even past the grace window."""
        db = _make_db()
        self._seed_stock(db, "AAPL")
        db.add(PaperTrade(ticker="AAPL", strategy="long", status="open", entry_price=150))
        db.commit()

        self._sweep(db, _mock_client(positions=[], orders=[]), self.T0)  # blip
        self._sweep(db, _mock_client(positions=SAMPLE_POSITIONS[:1]), self.T0 + timedelta(minutes=5))
        closed = self._sweep(db, _mock_client(positions=SAMPLE_POSITIONS[:1]), self.PAST_GRACE)

        assert closed == 0
        pt = db.query(PaperTrade).filter_by(ticker="AAPL").one()
        assert pt.status == "open"
        assert pt.orphan_seen_at is None

    def test_keeps_open_when_alpaca_position_matches(self):
        db = _make_db()
        self._seed_stock(db, "AAPL")
        db.add(PaperTrade(ticker="AAPL", strategy="long", status="open", entry_price=150))
        db.commit()

        client = _mock_client(positions=SAMPLE_POSITIONS[:1])  # AAPL
        self._sweep(db, client, self.T0)
        closed = self._sweep(db, client, self.PAST_GRACE)

        assert closed == 0
        assert db.query(PaperTrade).filter_by(ticker="AAPL").one().status == "open"

    def test_keeps_open_when_in_flight_order_matches(self):
        db = _make_db()
        self._seed_stock(db, "AAPL")
        db.add(PaperTrade(ticker="AAPL", strategy="long", status="open", entry_price=150))
        db.add(AlpacaOrder(
            alpaca_order_id="ord-pending", ticker="AAPL", side="buy",
            qty=10.0, order_type="limit", status="new",
        ))
        db.commit()

        client = _mock_client(positions=[], orders=[])
        self._sweep(db, client, self.T0)
        closed = self._sweep(db, client, self.PAST_GRACE)

        assert closed == 0
        assert db.query(PaperTrade).filter_by(ticker="AAPL").one().status == "open"

    def test_occ_option_symbol_maps_to_underlying(self):
        """PaperTrade.ticker='AAPL' should match an AlpacaPosition with an OCC
        option symbol like 'AAPL260619C00200000' (underlying = AAPL)."""
        db = _make_db()
        self._seed_stock(db, "AAPL")
        db.add(PaperTrade(ticker="AAPL", strategy="call_options", status="open", entry_price=2.5))
        db.commit()

        occ_position = {
            "ticker": "AAPL260619C00200000",
            "qty": 1.0,
            "side": "long",
            "avg_entry_price": 2.5,
            "current_price": 3.0,
            "market_value": 300.0,
            "unrealized_pl": 50.0,
            "unrealized_plpc": 0.2,
            "change_today": 0.05,
        }
        client = _mock_client(positions=[occ_position])
        self._sweep(db, client, self.T0)
        closed = self._sweep(db, client, self.PAST_GRACE)

        assert closed == 0
        assert db.query(PaperTrade).filter_by(ticker="AAPL").one().status == "open"

    def _seed_price(self, db, ticker, close):
        db.add(PriceHistory(ticker=ticker, date=date(2026, 7, 7), close=close))
        db.commit()

    def test_orphan_close_prices_stock_pnl(self):
        """A stock orphan-closed by the sweep must land with real pnl, not
        NULL — priced at the latest underlying close (2026-07-21 gap)."""
        db = _make_db()
        self._seed_stock(db, "GOOG")
        self._seed_price(db, "GOOG", 90.0)  # dropped from entry 100
        db.add(PaperTrade(
            ticker="GOOG", strategy="long", status="open",
            entry_price=100.0, position_size=1000.0,
        ))
        db.add(AlpacaOrder(  # trade really filled -> eligible for pricing
            alpaca_order_id="goog-fill", ticker="GOOG", side="buy",
            qty=10.0, order_type="limit", status="filled",
        ))
        db.commit()

        client = _mock_client(positions=[], orders=[])
        self._sweep(db, client, self.T0)
        closed = self._sweep(db, client, self.PAST_GRACE)

        assert closed == 1
        pt = db.query(PaperTrade).filter_by(ticker="GOOG").one()
        assert pt.status == "closed"
        # 10 shares (1000/100) * (90 - 100) = -100
        assert pt.pnl == -100.0
        assert pt.exit_price == 90.0

    def test_orphan_close_prices_spread_pnl(self):
        """The core 2026-07-21 fix: bull_spread orphan-closed before expiry
        must be priced from legs_json, not left NULL."""
        db = _make_db()
        self._seed_stock(db, "LRCX")
        self._seed_price(db, "LRCX", 400.0)  # both legs OTM -> keep full credit
        legs = [
            {"action": "sell", "strike": 350.0, "option_type": "put",
             "premium": 5.0, "contracts": 1},
            {"action": "buy", "strike": 340.0, "option_type": "put",
             "premium": 3.0, "contracts": 1},
        ]
        db.add(PaperTrade(
            ticker="LRCX", strategy="bull_spread", status="open",
            entry_price=2.0, legs_json=json.dumps(legs), contracts=1,
        ))
        db.add(AlpacaOrder(  # spread legs really filled
            alpaca_order_id="lrcx-fill", ticker="LRCX260807P00350000",
            side="sell", qty=1.0, order_type="limit", status="filled",
        ))
        db.commit()

        client = _mock_client(positions=[], orders=[])
        self._sweep(db, client, self.T0)
        closed = self._sweep(db, client, self.PAST_GRACE)

        assert closed == 1
        pt = db.query(PaperTrade).filter_by(ticker="LRCX").one()
        assert pt.status == "closed"
        # both puts expire worthless (underlying 400 > strikes): keep net
        # credit = (5 - 3) * 100 = +200
        assert pt.pnl == 200.0

    def test_orphan_close_never_filled_stays_null(self):
        """A trade whose order only ever expired/canceled (never a real
        position) must NOT be priced — pnl stays NULL so the non-existent
        P&L never reaches the live-gate (2026-07-22: all 8 orphan-closed
        bull_spreads had expired MLEG orders)."""
        db = _make_db()
        self._seed_stock(db, "AMD")
        self._seed_price(db, "AMD", 560.0)
        legs = [
            {"action": "buy", "strike": 530.0, "option_type": "call",
             "premium": 42.99, "contracts": 1},
            {"action": "sell", "strike": 555.0, "option_type": "call",
             "premium": 35.05, "contracts": 1},
        ]
        db.add(PaperTrade(
            ticker="AMD", strategy="bull_spread", status="open",
            entry_price=7.94, legs_json=json.dumps(legs), contracts=1,
        ))
        db.add(AlpacaOrder(  # order never filled
            alpaca_order_id="amd-expired", ticker="AMD260807C00530000",
            side="buy", qty=1.0, order_type="limit", status="expired",
        ))
        db.commit()

        client = _mock_client(positions=[], orders=[])
        self._sweep(db, client, self.T0)
        closed = self._sweep(db, client, self.PAST_GRACE)

        assert closed == 1
        pt = db.query(PaperTrade).filter_by(ticker="AMD").one()
        assert pt.status == "closed"
        assert pt.pnl is None

    def test_orphan_close_null_pnl_when_no_price(self):
        """No price row -> pnl stays NULL, but the trade still closes (safety
        rail must not depend on pricing being available)."""
        db = _make_db()
        self._seed_stock(db, "GOOG")
        db.add(PaperTrade(
            ticker="GOOG", strategy="long", status="open",
            entry_price=100.0, position_size=1000.0,
        ))
        db.add(AlpacaOrder(  # filled -> eligible, but no price row to mark on
            alpaca_order_id="goog-fill2", ticker="GOOG", side="buy",
            qty=10.0, order_type="limit", status="filled",
        ))
        db.commit()

        client = _mock_client(positions=[], orders=[])
        self._sweep(db, client, self.T0)
        closed = self._sweep(db, client, self.PAST_GRACE)

        assert closed == 1
        pt = db.query(PaperTrade).filter_by(ticker="GOOG").one()
        assert pt.status == "closed"
        assert pt.pnl is None

    def test_filled_order_does_not_keep_orphan_open(self):
        """A 'filled' order is not in-flight — it should not protect an
        orphan PaperTrade from closing after the grace window."""
        db = _make_db()
        self._seed_stock(db, "TSLA")
        db.add(PaperTrade(ticker="TSLA", strategy="short", status="open", entry_price=200))
        db.add(AlpacaOrder(
            alpaca_order_id="ord-filled", ticker="TSLA", side="sell",
            qty=5.0, order_type="market", status="filled",
        ))
        db.commit()

        client = _mock_client(positions=[], orders=[])
        self._sweep(db, client, self.T0)
        closed = self._sweep(db, client, self.PAST_GRACE)

        assert closed == 1
        assert db.query(PaperTrade).filter_by(ticker="TSLA").one().status == "closed"


class TestSyncOrders:
    def test_inserts_new_orders(self):
        db = _make_db()
        client = _mock_client(orders=SAMPLE_ORDERS)
        sync = PortfolioSync(db, client=client)

        count = sync.sync_orders()

        assert count == 2
        rows = db.query(AlpacaOrder).all()
        assert len(rows) == 2

    def test_updates_existing_order(self):
        db = _make_db()
        # Pre-insert an order with pending status
        db.add(AlpacaOrder(
            alpaca_order_id="ord-001", ticker="AAPL", side="buy",
            qty=10.0, order_type="limit", status="pending",
        ))
        db.commit()

        client = _mock_client(orders=SAMPLE_ORDERS[:1])
        sync = PortfolioSync(db, client=client)
        count = sync.sync_orders()

        # Existing order updated, not re-inserted
        assert count == 0
        row = db.query(AlpacaOrder).filter_by(alpaca_order_id="ord-001").first()
        assert row.status == "filled"
        assert row.filled_price == 149.5

    def test_empty_orders(self):
        db = _make_db()
        client = _mock_client(orders=[])
        sync = PortfolioSync(db, client=client)
        count = sync.sync_orders()
        assert count == 0


class TestSyncAccount:
    def test_returns_account_data(self):
        db = _make_db()
        client = _mock_client()
        sync = PortfolioSync(db, client=client)

        result = sync.sync_account()

        assert result["equity"] == 25000.0
        assert result["buying_power"] == 20000.0


class TestPortfolioSummary:
    def test_summary_includes_all_fields(self):
        db = _make_db()
        client = _mock_client(positions=SAMPLE_POSITIONS)
        sync = PortfolioSync(db, client=client)

        summary = sync.get_portfolio_summary()

        assert summary["equity"] == 25000.0
        assert summary["alpaca_positions"] == 2
        assert summary["paper_positions"] == 0
        assert summary["total_positions"] == 2
        assert summary["total_unrealized_pl"] == 75.0
        assert len(summary["positions"]) == 2

    def test_summary_counts_paper_trades(self):
        db = _make_db()
        db.add(Stock(ticker="MSFT"))
        db.commit()
        db.add(PaperTrade(ticker="MSFT", strategy="short", status="open", entry_price=300))
        db.commit()

        client = _mock_client(positions=SAMPLE_POSITIONS[:1])
        sync = PortfolioSync(db, client=client)

        summary = sync.get_portfolio_summary()

        assert summary["alpaca_positions"] == 1
        assert summary["paper_positions"] == 1
        assert summary["total_positions"] == 2


class TestResidueDetection:
    """detect_residue_positions: broker positions no strategy owns (2026-07-14
    DOCU exercise residue — 300 unowned shares saturated the capital cap)."""

    def _sync(self, db):
        import src.services.portfolio_sync as ps
        ps._residue_alerted.clear()
        return PortfolioSync(db, client=_mock_client())

    def _add_position(self, db, ticker, qty=300.0, mkt=14778.0):
        db.add(AlpacaPosition(
            ticker=ticker, qty=qty, side="long", avg_entry_price=49.38,
            current_price=49.99, market_value=mkt, synced_at=datetime.utcnow(),
        ))
        db.commit()

    def _add_open_trade(self, db, ticker, strategy="long"):
        db.add(Stock(ticker=ticker))
        db.add(PaperTrade(ticker=ticker, strategy=strategy, status="open", entry_price=100.0))
        db.commit()

    @patch("src.services.portfolio_sync.httpx")
    def test_unowned_stock_flagged(self, mock_httpx):
        db = _make_db()
        self._add_position(db, "DOCU")
        residue = self._sync(db).detect_residue_positions()
        assert [r["ticker"] for r in residue] == ["DOCU"]
        assert mock_httpx.post.called

    @patch("src.services.portfolio_sync.httpx")
    def test_position_with_open_trade_not_flagged(self, mock_httpx):
        db = _make_db()
        self._add_open_trade(db, "AAPL")
        self._add_position(db, "AAPL", qty=10.0, mkt=1550.0)
        assert self._sync(db).detect_residue_positions() == []

    @patch("src.services.portfolio_sync.httpx")
    def test_occ_option_maps_to_underlying_trade(self, mock_httpx):
        db = _make_db()
        self._add_open_trade(db, "LRCX", strategy="bull_spread")
        self._add_position(db, "LRCX260814C00355000", qty=1.0, mkt=3100.0)
        assert self._sync(db).detect_residue_positions() == []

    @patch("src.services.portfolio_sync.httpx")
    def test_pair_hedge_symbol_not_flagged(self, mock_httpx):
        db = _make_db()
        self._add_open_trade(db, "RIVN", strategy="pair_short")
        self._add_position(db, "SPY", qty=0.35, mkt=264.0)
        assert self._sync(db).detect_residue_positions() == []

    @patch("src.services.portfolio_sync.httpx")
    def test_hedge_symbol_flagged_without_pair_trades(self, mock_httpx):
        # SPY position with no open pair_short = residue
        db = _make_db()
        self._add_position(db, "SPY", qty=0.35, mkt=264.0)
        residue = self._sync(db).detect_residue_positions()
        assert [r["ticker"] for r in residue] == ["SPY"]

    @patch("src.services.portfolio_sync.httpx")
    def test_in_flight_order_marks_owned(self, mock_httpx):
        db = _make_db()
        db.add(AlpacaOrder(alpaca_order_id="o1", ticker="MU", status="new"))
        db.commit()
        self._add_position(db, "MU", qty=1.0, mkt=920.0)
        assert self._sync(db).detect_residue_positions() == []

    @patch("src.services.portfolio_sync.httpx")
    def test_alert_throttled_once_per_day(self, mock_httpx):
        db = _make_db()
        self._add_position(db, "DOCU")
        sync = self._sync(db)
        now = datetime(2026, 7, 14, 14, 0)
        sync.detect_residue_positions(now=now)
        sync.detect_residue_positions(now=now + timedelta(minutes=5))
        assert mock_httpx.post.call_count == 1
        # Next day re-alerts
        sync.detect_residue_positions(now=now + timedelta(days=1))
        assert mock_httpx.post.call_count == 2


class TestLiquidateResidue:
    """liquidate_residue: submit closing market orders for unowned broker
    positions when auto_liquidate_residue is on (2026-07-31). Signed qty:
    long (>0) sells, short (<0) buys."""

    def _residue(self):
        return [
            {"ticker": "MRNA260814P00066000", "underlying": "MRNA",
             "qty": -4.0, "market_value": -5180.0},   # short option -> buy to close
            {"ticker": "SOFI", "underlying": "SOFI",
             "qty": 16.0, "market_value": 1392.0},     # long stock -> sell to close
        ]

    def test_disabled_submits_nothing(self):
        db = _make_db()
        client = _mock_client()
        sync = PortfolioSync(db, client=client)
        with patch("src.services.portfolio_sync.get_settings") as gs:
            gs.return_value.auto_liquidate_residue = False
            n = sync.liquidate_residue(self._residue())
        assert n == 0
        client.submit_order.assert_not_called()

    def test_enabled_closes_each_by_signed_qty(self):
        db = _make_db()
        client = _mock_client()
        sync = PortfolioSync(db, client=client)
        with patch("src.services.portfolio_sync.get_settings") as gs:
            gs.return_value.auto_liquidate_residue = True
            n = sync.liquidate_residue(self._residue())
        assert n == 2
        calls = {c.kwargs["ticker"]: c.kwargs for c in client.submit_order.call_args_list}
        assert calls["MRNA260814P00066000"]["side"] == "buy"
        assert calls["MRNA260814P00066000"]["qty"] == 4.0
        assert calls["SOFI"]["side"] == "sell"
        assert calls["SOFI"]["qty"] == 16.0

    def test_empty_residue_noop(self):
        db = _make_db()
        client = _mock_client()
        sync = PortfolioSync(db, client=client)
        with patch("src.services.portfolio_sync.get_settings") as gs:
            gs.return_value.auto_liquidate_residue = True
            assert sync.liquidate_residue([]) == 0
        client.submit_order.assert_not_called()
