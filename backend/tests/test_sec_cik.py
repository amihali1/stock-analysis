"""Tests for the ticker -> SEC CIK resolver (P10-005)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, SecCikMap
from src.services import sec_cik
from src.services.sec_cik import (
    cache_age_hours,
    ensure_cache_fresh,
    get_cik,
    refresh_cache,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _mock_client(payload: dict | list) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    client = MagicMock()
    client.get.return_value = resp
    return client


def _sec_payload() -> dict:
    # Real SEC payload is a dict keyed by row index strings.
    return {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corporation"},
        "2": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire Hathaway Inc."},
    }


class TestPadCik:
    def test_zero_pads_to_ten_digits(self):
        assert sec_cik._pad_cik(320193) == "0000320193"
        assert sec_cik._pad_cik("789019") == "0000789019"
        assert sec_cik._pad_cik(1067983) == "0001067983"

    def test_already_padded_returns_unchanged(self):
        assert sec_cik._pad_cik("0000320193") == "0000320193"


class TestRefreshCache:
    def test_populates_cache_with_padded_cik(self, db):
        client = _mock_client(_sec_payload())
        n = refresh_cache(db, client=client)
        assert n == 3
        rows = db.query(SecCikMap).order_by(SecCikMap.ticker).all()
        assert [r.ticker for r in rows] == ["AAPL", "BRK-B", "MSFT"]
        assert all(len(r.cik) == 10 for r in rows)
        assert {r.ticker: r.cik for r in rows} == {
            "AAPL": "0000320193",
            "MSFT": "0000789019",
            "BRK-B": "0001067983",
        }

    def test_replaces_existing_rows(self, db):
        # Pre-existing stale row for AAPL with wrong CIK
        db.add(SecCikMap(
            ticker="AAPL", cik="0000000001", company_name="Stale",
            fetched_at=datetime.utcnow() - timedelta(days=5),
        ))
        db.commit()

        client = _mock_client(_sec_payload())
        refresh_cache(db, client=client)

        rows = db.query(SecCikMap).filter(SecCikMap.ticker == "AAPL").all()
        assert len(rows) == 1
        assert rows[0].cik == "0000320193"

    def test_skips_rows_missing_ticker_or_cik(self, db):
        client = _mock_client({
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"},
            "1": {"cik_str": None, "ticker": "BAD", "title": "No CIK"},
            "2": {"cik_str": 999, "ticker": "", "title": "No Ticker"},
        })
        refresh_cache(db, client=client)
        tickers = {r.ticker for r in db.query(SecCikMap).all()}
        assert tickers == {"AAPL"}


class TestCacheAge:
    def test_returns_none_when_empty(self, db):
        assert cache_age_hours(db) is None

    def test_returns_age_in_hours_for_newest_row(self, db):
        old = datetime.utcnow() - timedelta(hours=10)
        new = datetime.utcnow() - timedelta(hours=2)
        db.add(SecCikMap(ticker="OLD", cik="0", fetched_at=old))
        db.add(SecCikMap(ticker="NEW", cik="0", fetched_at=new))
        db.commit()
        age = cache_age_hours(db)
        assert age is not None
        assert 1.5 < age < 2.5  # ~2 hours


class TestEnsureCacheFresh:
    def test_refreshes_when_empty(self, db):
        client = _mock_client(_sec_payload())
        did_refresh = ensure_cache_fresh(db, client=client)
        assert did_refresh is True
        assert db.query(SecCikMap).count() == 3

    def test_skips_refresh_when_recent(self, db):
        db.add(SecCikMap(
            ticker="AAPL", cik="0000320193",
            fetched_at=datetime.utcnow() - timedelta(hours=1),
        ))
        db.commit()
        client = _mock_client(_sec_payload())
        did_refresh = ensure_cache_fresh(db, max_age_hours=24, client=client)
        assert did_refresh is False
        client.get.assert_not_called()

    def test_refreshes_when_stale(self, db):
        db.add(SecCikMap(
            ticker="AAPL", cik="0000000001",
            fetched_at=datetime.utcnow() - timedelta(hours=48),
        ))
        db.commit()
        client = _mock_client(_sec_payload())
        did_refresh = ensure_cache_fresh(db, max_age_hours=24, client=client)
        assert did_refresh is True
        # Stale CIK was replaced
        row = db.query(SecCikMap).filter(SecCikMap.ticker == "AAPL").one()
        assert row.cik == "0000320193"


class TestGetCik:
    def test_resolves_known_ticker(self, db):
        client = _mock_client(_sec_payload())
        cik = get_cik(db, "AAPL", client=client)
        assert cik == "0000320193"

    def test_uppercases_input(self, db):
        client = _mock_client(_sec_payload())
        assert get_cik(db, "aapl", client=client) == "0000320193"

    def test_unknown_ticker_returns_none(self, db):
        client = _mock_client(_sec_payload())
        assert get_cik(db, "NOPE", client=client) is None

    def test_empty_input_returns_none(self, db):
        assert get_cik(db, "", auto_refresh=False) is None
        assert get_cik(db, None, auto_refresh=False) is None  # type: ignore

    def test_auto_refresh_false_uses_only_cache(self, db):
        # No cache → no refresh → no result
        client = _mock_client(_sec_payload())
        assert get_cik(db, "AAPL", auto_refresh=False, client=client) is None
        client.get.assert_not_called()

        # Pre-populated cache → returns even with auto_refresh disabled
        db.add(SecCikMap(
            ticker="AAPL", cik="0000320193", fetched_at=datetime.utcnow(),
        ))
        db.commit()
        assert get_cik(db, "AAPL", auto_refresh=False) == "0000320193"
