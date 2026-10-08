"""Fetch daily OHLCV data from yfinance and store in price_history table."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import yfinance as yf
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import PriceHistory, Stock, TechnicalIndicator
from src.db.session import SessionLocal
from src.config import get_settings

logger = logging.getLogger(__name__)


class DataFetcher:
    def __init__(self, db: Session | None = None):
        self._owns_db = db is None
        self.db = db or SessionLocal()
        # Tickers whose stored history was restated this session (splits).
        self.restated: list[str] = []

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
        """Fetch and upsert OHLCV data for a single ticker. Returns rows inserted.

        If yfinance's closes on dates already stored disagree with ours, the
        history was retroactively adjusted (split, spin-off) and the ticker's
        stored range is restated — see `_restate_history`.
        """
        self.ensure_stock(ticker)

        yf_ticker = yf.Ticker(ticker)
        df: pd.DataFrame = yf_ticker.history(period=period, auto_adjust=False)

        if df.empty:
            logger.warning(f"{ticker}: no data returned from yfinance")
            return 0

        stored_close = {
            row[0]: row[1]
            for row in self.db.execute(
                select(PriceHistory.date, PriceHistory.close).where(PriceHistory.ticker == ticker)
            ).all()
        }

        if stored_close:
            overlap_df = df
            if not any(d in stored_close for d in _row_dates(df)):
                # Gap longer than the fetch window (e.g. a week-long outage):
                # reach back to the last stored date so a split that landed in
                # the gap is still detected.
                start = max(stored_close) - timedelta(days=10)
                overlap_df = yf_ticker.history(start=start.isoformat(), auto_adjust=False)
            if _history_adjusted(overlap_df, stored_close):
                inserted = self._restate_history(ticker, yf_ticker, min(stored_close))
                self._update_metadata(ticker, yf_ticker)
                return inserted

        inserted = self._insert_new_rows(ticker, df, set(stored_close))
        self._update_metadata(ticker, yf_ticker)
        return inserted

    def _restate_history(self, ticker: str, yf_ticker, start: date) -> int:
        """Replace a ticker's stored prices with yfinance's current (adjusted)
        history from `start`, and drop its indicators so the next
        compute_indicators run rebuilds them from the corrected prices.

        Found 2026-09-29: KLAC's 10:1 split left ~2100 pre-split closes next
        to ~210 post-split ones (volatility_20d = 32.5) because rows are only
        ever inserted, never rewritten.
        """
        full = yf_ticker.history(start=start.isoformat(), auto_adjust=False)
        if full.empty:
            logger.error(f"{ticker}: history adjusted but full refetch returned nothing — not restating")
            return 0
        n_prices = self.db.query(PriceHistory).filter(PriceHistory.ticker == ticker).delete()
        n_ind = self.db.query(TechnicalIndicator).filter(TechnicalIndicator.ticker == ticker).delete()
        inserted = self._insert_new_rows(ticker, full, set(), commit=False)
        self.db.commit()
        self.restated.append(ticker)
        logger.warning(
            f"{ticker}: split/adjustment detected — restated {n_prices} → {inserted} price rows "
            f"from {start}, dropped {n_ind} indicator rows for recompute"
        )
        return inserted

    def _insert_new_rows(
        self, ticker: str, df: pd.DataFrame, existing_dates: set[date], commit: bool = True,
    ) -> int:
        rows_inserted = 0
        skipped_null_close = 0
        unsettled = _unsettled_session_date()
        for idx, row in df.iterrows():
            row_date = idx.date() if hasattr(idx, "date") else idx
            if row_date in existing_dates:
                continue
            # A bar for the session still in progress is partial. Indices trade
            # pre-market (^VIX), so the 06:00 ET fetch stored a partial bar
            # every morning; the 16:30 close then differed by >2% and the split
            # detector restated ^VIX's whole history daily (2026-10-02..08).
            # Mid-session backfills hit the same thing for every ticker.
            if unsettled is not None and row_date >= unsettled:
                continue

            # Yahoo sometimes returns partial-day rows with NaN close (OHLV present,
            # close missing). Persisting them poisons every downstream consumer and
            # the existing_dates dedup means they never get re-fetched — skip instead,
            # so a later fetch can fill the date with complete data.
            close = _safe_float(row.get("Close"))
            if close is None:
                skipped_null_close += 1
                continue

            price = PriceHistory(
                ticker=ticker,
                date=row_date,
                open=_safe_float(row.get("Open")),
                high=_safe_float(row.get("High")),
                low=_safe_float(row.get("Low")),
                close=close,
                volume=_safe_float(row.get("Volume")),
                adj_close=_safe_float(row.get("Adj Close")),
            )
            self.db.add(price)
            rows_inserted += 1

        if skipped_null_close:
            logger.warning(
                f"{ticker}: skipped {skipped_null_close} rows with NaN close from yfinance"
            )

        if rows_inserted > 0 and commit:
            self.db.commit()

        return rows_inserted

    def _update_metadata(self, ticker: str, yf_ticker) -> None:
        """Fill stock name/sector/exchange from yfinance info if missing."""
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


ET = ZoneInfo("America/New_York")
# Daily bars are final once the 16:00 ET close prints and settles; 16:15 gives
# index settlement (VIX) and late prints time to land before we persist.
SESSION_SETTLED_AT = time(16, 15)


def _unsettled_session_date(now: datetime | None = None) -> date | None:
    """Today's ET date while its session is still unsettled, else None."""
    now_et = (now or datetime.now(ET)).astimezone(ET)
    if now_et.time() < SESSION_SETTLED_AT:
        return now_et.date()
    return None


# Relative close disagreement on an already-stored date that means yfinance
# re-based the history. Splits move closes by >= 1/1.5 (3-for-2); HON's
# 2026-06-29 spin-off factor was 0.9535. Routine data revisions are far below 2%.
ADJUSTMENT_TOLERANCE = 0.02


def _row_dates(df: pd.DataFrame) -> list[date]:
    return [idx.date() if hasattr(idx, "date") else idx for idx in df.index]


def _history_adjusted(df: pd.DataFrame, stored_close: dict[date, float | None]) -> bool:
    """True if any overlapping date's close differs from ours by more than
    ADJUSTMENT_TOLERANCE — i.e. yfinance retroactively adjusted the series."""
    for row_date, raw in zip(_row_dates(df), df["Close"] if "Close" in df else []):
        ours = stored_close.get(row_date)
        theirs = _safe_float(raw)
        if ours and theirs and abs(ours / theirs - 1.0) > ADJUSTMENT_TOLERANCE:
            return True
    return False


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
