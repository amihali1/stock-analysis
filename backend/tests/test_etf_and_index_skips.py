"""Non-equity tickers must be skipped by company-only data fetchers.

Hygiene bundle 2026-07-07: earnings fetch 404'd on all 11 sector ETFs every
Sunday; Finviz headline fetch 404'd on ^VIX every sentiment run (Finviz has
no index quote pages).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.config import ETF_TICKERS
from src.pipeline.earnings_fetcher import EarningsFetcher
from src.services.headline_fetcher import FinvizFetcher


def test_etf_tickers_cover_watchlist_non_equities():
    from src.config import Settings
    watch = set(Settings.model_fields["default_watchlist"].default)
    assert ETF_TICKERS <= watch | {"^VIX"}
    assert "SPY" in ETF_TICKERS and "XLF" in ETF_TICKERS and "^VIX" in ETF_TICKERS


def test_earnings_fetch_all_skips_etfs():
    db = MagicMock()
    fetcher = EarningsFetcher(db=db, sleep_s=0)
    with patch.object(fetcher, "fetch_one") as mock_one:
        mock_one.return_value = "ok"
        results = fetcher.fetch_all(tickers=["AAPL", "SPY", "XLF", "^VIX"])

    assert results == {"AAPL": "ok", "SPY": "skipped_etf", "XLF": "skipped_etf", "^VIX": "skipped_etf"}
    mock_one.assert_called_once_with("AAPL")


def test_finviz_skips_index_symbols():
    with patch("src.services.headline_fetcher.finvizfinance") as mock_fv:
        result = FinvizFetcher().fetch("^VIX")

    assert result == []
    mock_fv.assert_not_called()


def test_finviz_still_fetches_equities():
    with patch("src.services.headline_fetcher.finvizfinance") as mock_fv:
        mock_fv.return_value.ticker_news.return_value = None
        result = FinvizFetcher().fetch("AAPL")

    assert result == []
    mock_fv.assert_called_once_with("AAPL")
