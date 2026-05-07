"""Tests for the Wikipedia page-views fetcher (P10-008)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, Stock, WikipediaPageviews
from src.pipeline.wikipedia_fetcher import (
    MAX_DAYS_PER_CALL,
    USER_AGENT,
    WikipediaPageviewFetcher,
    _date_chunks,
    _parse_ts,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.add(Stock(ticker="AAPL"))
    s.add(Stock(ticker="BRK-B"))
    s.commit()
    yield s
    s.close()


@pytest.fixture
def titles_file(tmp_path):
    p = tmp_path / "titles.json"
    p.write_text(json.dumps({
        "titles": {
            "AAPL": "Apple_Inc.",
            "BRK-B": "Berkshire_Hathaway",
            "T": "AT&T",
        }
    }))
    return p


def _wiki_payload(start: date, end: date, views_per_day: int = 100) -> dict:
    items = []
    cur = start
    while cur <= end:
        items.append({
            "project": "en.wikipedia",
            "article": "Apple_Inc.",
            "granularity": "daily",
            "timestamp": cur.strftime("%Y%m%d") + "00",
            "access": "all-access",
            "agent": "all-agents",
            "views": views_per_day,
        })
        cur += timedelta(days=1)
    return {"items": items}


def _resp(status_code: int, payload: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = payload or {}
    return r


class TestDateChunks:
    def test_single_chunk_when_range_fits(self):
        chunks = list(_date_chunks(date(2026, 5, 1), date(2026, 5, 5), 50))
        assert chunks == [(date(2026, 5, 1), date(2026, 5, 5))]

    def test_multi_chunks_at_boundary(self):
        # 51 days, max 50/chunk
        start = date(2026, 1, 1)
        end = start + timedelta(days=50)
        chunks = list(_date_chunks(start, end, 50))
        assert len(chunks) == 2
        assert chunks[0] == (start, start + timedelta(days=49))
        assert chunks[1] == (start + timedelta(days=50), end)


class TestParseTs:
    def test_parses_yyyymmddhh(self):
        assert _parse_ts("2026050100") == date(2026, 5, 1)

    def test_returns_none_on_garbage(self):
        assert _parse_ts("garbage") is None
        assert _parse_ts("") is None


class TestFetcher:
    def test_user_agent_set_on_default_client(self, db, titles_file):
        f = WikipediaPageviewFetcher(db=db, titles_path=titles_file)
        assert f.client.headers["User-Agent"] == USER_AGENT
        f.close()

    def test_load_titles_from_config(self, db, titles_file):
        with WikipediaPageviewFetcher(db=db, titles_path=titles_file) as f:
            titles = f.load_titles()
            assert titles["AAPL"] == "Apple_Inc."
            assert titles["BRK-B"] == "Berkshire_Hathaway"

    def test_fetch_one_writes_dense_rows(self, db, titles_file):
        client = MagicMock()
        start, end = date(2026, 5, 1), date(2026, 5, 5)
        client.get.return_value = _resp(200, _wiki_payload(start, end, 250))

        with WikipediaPageviewFetcher(
            db=db, titles_path=titles_file, client=client
        ) as f:
            status = f.fetch_one("AAPL", "Apple_Inc.", start, end)

        assert status == "ok"
        rows = db.query(WikipediaPageviews).filter_by(ticker="AAPL").all()
        assert len(rows) == 5
        assert all(r.page_views == 250 for r in rows)
        assert all(r.wikipedia_title == "Apple_Inc." for r in rows)

    def test_fetch_one_stubs_zero_for_missing_days(self, db, titles_file):
        """Wikimedia returns gaps for low-traffic days; we densify with 0s."""
        client = MagicMock()
        start, end = date(2026, 5, 1), date(2026, 5, 5)
        # API only returns 3 of 5 days
        partial = {
            "items": [
                {"timestamp": "2026050100", "views": 100},
                {"timestamp": "2026050300", "views": 200},
                {"timestamp": "2026050500", "views": 300},
            ]
        }
        client.get.return_value = _resp(200, partial)

        with WikipediaPageviewFetcher(
            db=db, titles_path=titles_file, client=client
        ) as f:
            status = f.fetch_one("AAPL", "Apple_Inc.", start, end)

        assert status == "ok"
        rows = sorted(
            db.query(WikipediaPageviews).filter_by(ticker="AAPL").all(),
            key=lambda r: r.view_date,
        )
        assert [r.page_views for r in rows] == [100, 0, 200, 0, 300]

    def test_fetch_one_404_returns_no_data(self, db, titles_file):
        client = MagicMock()
        client.get.return_value = _resp(404)

        with WikipediaPageviewFetcher(
            db=db, titles_path=titles_file, client=client
        ) as f:
            status = f.fetch_one(
                "AAPL", "Apple_Inc.", date(2026, 5, 1), date(2026, 5, 3)
            )

        assert status == "no_data"
        # We still write zero stubs even on 404 so the series stays dense.
        rows = db.query(WikipediaPageviews).filter_by(ticker="AAPL").all()
        assert len(rows) == 3
        assert all(r.page_views == 0 for r in rows)

    def test_chunks_long_range_into_multiple_calls(self, db, titles_file):
        client = MagicMock()
        # 120 days = 3 chunks at 50/chunk
        start = date(2026, 1, 1)
        end = start + timedelta(days=119)
        # Each call returns its own date range
        def _side_effect(url, *a, **kw):
            # Parse the date range from the URL
            parts = url.rsplit("/", 2)
            s = date(int(parts[-2][:4]), int(parts[-2][4:6]), int(parts[-2][6:8]))
            e = date(int(parts[-1][:4]), int(parts[-1][4:6]), int(parts[-1][6:8]))
            return _resp(200, _wiki_payload(s, e, 50))
        client.get.side_effect = _side_effect

        with WikipediaPageviewFetcher(
            db=db, titles_path=titles_file, client=client, sleep_s=0
        ) as f:
            status = f.fetch_one("AAPL", "Apple_Inc.", start, end)

        assert status == "ok"
        assert client.get.call_count == 3
        rows = db.query(WikipediaPageviews).filter_by(ticker="AAPL").all()
        assert len(rows) == 120

    def test_retry_then_recover_on_5xx(self, db, titles_file):
        client = MagicMock()
        # First two attempts: 503, third: 200
        client.get.side_effect = [
            _resp(503),
            _resp(503),
            _resp(200, _wiki_payload(date(2026, 5, 1), date(2026, 5, 1), 42)),
        ]

        with WikipediaPageviewFetcher(
            db=db, titles_path=titles_file, client=client, sleep_s=0
        ) as f:
            status = f.fetch_one(
                "AAPL", "Apple_Inc.", date(2026, 5, 1), date(2026, 5, 1)
            )

        assert status == "ok"
        assert client.get.call_count == 3

    def test_url_encodes_special_chars_in_title(self, db, titles_file):
        client = MagicMock()
        client.get.return_value = _resp(200, {"items": []})

        with WikipediaPageviewFetcher(
            db=db, titles_path=titles_file, client=client, sleep_s=0
        ) as f:
            f.fetch_one("T", "AT&T", date(2026, 5, 1), date(2026, 5, 1))

        called_url = client.get.call_args[0][0]
        assert "AT%26T" in called_url
        assert "AT&T" not in called_url

    def test_fetch_all_skips_unmapped_ticker(self, db, titles_file):
        client = MagicMock()
        client.get.return_value = _resp(200, _wiki_payload(
            date(2026, 5, 1), date(2026, 5, 1), 10
        ))

        with WikipediaPageviewFetcher(
            db=db, titles_path=titles_file, client=client, sleep_s=0
        ) as f:
            results = f.fetch_all(
                tickers=["AAPL", "ZZZ"],
                start_date=date(2026, 5, 1), end_date=date(2026, 5, 1),
            )

        assert results["AAPL"] == "ok"
        assert results["ZZZ"] == "no_title"
