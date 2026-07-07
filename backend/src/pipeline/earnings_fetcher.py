"""Weekly earnings calendar fetcher (P9-005).

Pulls upcoming earnings dates from yfinance `Ticker.calendar`. Tolerant of
missing/malformed data — yfinance is flaky here. A ticker with unknown next
earnings simply gets no row, and feature extraction returns -1 for it.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Iterable

import yfinance as yf
from sqlalchemy.orm import Session

from src.db.models import EarningsCalendar
from src.db.session import SessionLocal

logger = logging.getLogger(__name__)

RATE_LIMIT_SLEEP = 0.5


def _parse_calendar(raw) -> list[date]:
    """Pull earnings dates out of yfinance's various calendar shapes."""
    if raw is None:
        return []
    out: list[date] = []
    # Newer yfinance: dict with 'Earnings Date' → list[date|datetime]
    if isinstance(raw, dict):
        candidate = raw.get("Earnings Date") or raw.get("earnings_date")
        if candidate:
            items = candidate if isinstance(candidate, (list, tuple)) else [candidate]
            for item in items:
                if isinstance(item, datetime):
                    out.append(item.date())
                elif isinstance(item, date):
                    out.append(item)
    # Older versions: pandas DataFrame
    elif hasattr(raw, "loc"):
        try:
            for col in raw.columns:
                val = raw.loc["Earnings Date", col]
                if isinstance(val, datetime):
                    out.append(val.date())
                elif isinstance(val, date):
                    out.append(val)
        except (KeyError, AttributeError):
            pass
    return out


class EarningsFetcher:
    """Persist upcoming earnings dates into the `earnings_calendar` table."""

    def __init__(self, db: Session | None = None, sleep_s: float = RATE_LIMIT_SLEEP):
        self._owns_db = db is None
        self.db = db or SessionLocal()
        self.sleep_s = sleep_s

    def close(self):
        if self._owns_db:
            self.db.close()

    def fetch_all(self, tickers: list[str] | None = None) -> dict[str, str]:
        if tickers is None:
            from src.db.watchlist import get_watchlist_tickers
            tickers = get_watchlist_tickers(self.db)

        from src.config import ETF_TICKERS

        results: dict[str, str] = {}
        for ticker in tickers:
            # ETFs/indexes have no earnings; yfinance 404s on every one.
            if ticker in ETF_TICKERS:
                results[ticker] = "skipped_etf"
                continue
            try:
                results[ticker] = self.fetch_one(ticker)
            except Exception:
                logger.exception("Earnings fetch failed for %s", ticker)
                results[ticker] = "error"
            time.sleep(self.sleep_s)
        return results

    def fetch_one(self, ticker: str) -> str:
        stock = yf.Ticker(ticker)
        try:
            raw = stock.calendar
        except Exception:
            logger.exception("yfinance.calendar error for %s", ticker)
            return "error"

        dates = _parse_calendar(raw)
        if not dates:
            return "no_data"

        for d in dates:
            self._upsert(ticker, d)
        return "ok"

    def _upsert(self, ticker: str, earnings_date: date):
        existing = (
            self.db.query(EarningsCalendar)
            .filter_by(ticker=ticker, earnings_date=earnings_date)
            .first()
        )
        if existing is None:
            existing = EarningsCalendar(
                ticker=ticker,
                earnings_date=earnings_date,
                source="yfinance",
                fetched_at=datetime.utcnow(),
            )
            self.db.add(existing)
        else:
            existing.fetched_at = datetime.utcnow()
        self.db.commit()
