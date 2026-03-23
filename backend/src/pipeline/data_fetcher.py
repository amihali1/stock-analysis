"""Fetch daily OHLCV data from yfinance and store in price_history table."""

from __future__ import annotations

import logging
from datetime import date

import yfinance as yf
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Stock, PriceHistory
from src.db.session import SessionLocal
from src.config import get_settings

logger = logging.getLogger(__name__)


class DataFetcher:
    def __init__(self, db: Session | None = None):
        self._owns_db = db is None
        self.db = db or SessionLocal()

    def close(self):
        if self._owns_db:
            self.db.close()

    def ensure_stock(self, ticker: str) -> Stock:
        """Get or create a Stock row for the given ticker."""
        stock = self.db.query(Stock).filter_by(ticker=ticker).first()
        if stock is None:
            stock = Stock(ticker=ticker)
            self.db.add(stock)
            self.db.commit()
        return stock

    def fetch_daily(
        self,
        tickers: list[str] | None = None,
        period: str = "2y",
    ) -> dict[str, int]:
        """Fetch daily OHLCV for tickers. Returns {ticker: rows_inserted} counts."""
        if tickers is None:
            from src.db.watchlist import get_watchlist_tickers
            tickers = get_watchlist_tickers(self.db)

        results: dict[str, int] = {}

        for ticker in tickers:
            try:
                count = self._fetch_ticker(ticker, period)
                results[ticker] = count
                logger.info(f"{ticker}: {count} new rows")
            except Exception:
                logger.exception(f"Failed to fetch {ticker}")
                results[ticker] = -1

        return results

    def _fetch_ticker(self, ticker: str, period: str) -> int:
        """Fetch and upsert OHLCV data for a single ticker. Returns rows inserted."""
        self.ensure_stock(ticker)

        yf_ticker = yf.Ticker(ticker)
        df: pd.DataFrame = yf_ticker.history(period=period, auto_adjust=False)

        if df.empty:
            logger.warning(f"{ticker}: no data returned from yfinance")
            return 0

        # Get existing dates to avoid duplicates
        existing_dates = set(
            row[0]
            for row in self.db.execute(
                select(PriceHistory.date).where(PriceHistory.ticker == ticker)
            ).all()
        )

        rows_inserted = 0
        for idx, row in df.iterrows():
            row_date = idx.date() if hasattr(idx, "date") else idx
            if row_date in existing_dates:
                continue

            price = PriceHistory(
                ticker=ticker,
                date=row_date,
                open=_safe_float(row.get("Open")),
                high=_safe_float(row.get("High")),
                low=_safe_float(row.get("Low")),
                close=_safe_float(row.get("Close")),
                volume=_safe_float(row.get("Volume")),
                adj_close=_safe_float(row.get("Adj Close")),
            )
            self.db.add(price)
            rows_inserted += 1

        if rows_inserted > 0:
            self.db.commit()

        # Update stock metadata from yfinance info if missing
        stock = self.db.query(Stock).filter_by(ticker=ticker).first()
        if stock and not stock.name:
            try:
                info = yf_ticker.info
                stock.name = info.get("longName") or info.get("shortName")
                stock.sector = info.get("sector")
                stock.exchange = info.get("exchange")
                self.db.commit()
            except Exception:
                logger.debug(f"{ticker}: could not fetch info metadata")

        return rows_inserted


def _safe_float(val) -> float | None:
    """Convert value to float, returning None for NaN/None."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Create tables if they don't exist (for standalone runs)
    from src.db.models import Base
    from src.db.session import engine
    Base.metadata.create_all(engine)

    fetcher = DataFetcher()
    try:
        # Test with a small set first
        test_tickers = ["AAPL", "MSFT", "GOOGL", "SPY", "NVDA"]
        logger.info(f"Fetching data for {test_tickers}")
        results = fetcher.fetch_daily(tickers=test_tickers, period="2y")
        for ticker, count in results.items():
            status = f"{count} rows" if count >= 0 else "FAILED"
            print(f"  {ticker}: {status}")
    finally:
        fetcher.close()
