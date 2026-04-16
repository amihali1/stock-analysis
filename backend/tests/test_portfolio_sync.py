"""Tests for PortfolioSync."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import AlpacaOrder, AlpacaPosition, Base, PaperTrade, Stock
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
