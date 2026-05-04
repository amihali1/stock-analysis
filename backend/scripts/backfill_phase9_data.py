"""Backfill historical data for Phase 9 features.

Two backfills:
  1. ^VIX price history (2024-04 → today) into price_history.
  2. Earnings dates for every watchlist ticker into earnings_calendar.

Idempotent: existing rows are skipped, not duplicated.

Run inside the backend container:
    docker exec backend-backend-1 python -m scripts.backfill_phase9_data
"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import logging
import sys
from datetime import date, datetime

import pandas as pd
import yfinance as yf

from src.config import get_settings
from src.db.models import EarningsCalendar, PriceHistory, Stock
from src.db.session import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill")

MACRO_START = "2024-04-01"

# Cross-asset macro tickers backfilled into price_history. Each is queried by
# the macro feature builder via its standard ticker symbol.
MACRO_TICKERS: list[tuple[str, str]] = [
    ("^VIX", "CBOE Volatility Index"),
    ("^TNX", "10-Year Treasury Yield"),
    ("^IRX", "13-Week Treasury Yield"),
    ("UUP",  "Invesco DB US Dollar Index Bullish Fund"),
]


def ensure_stock(db, ticker: str, name: str) -> None:
    existing = db.query(Stock).filter_by(ticker=ticker).first()
    if existing:
        return
    db.add(Stock(ticker=ticker, name=name))
    db.commit()
    logger.info("Added stock row for %s", ticker)


def backfill_index_ticker(db, ticker: str, name: str) -> int:
    """Insert daily OHLC rows from yfinance into price_history for a non-equity ticker."""
    ensure_stock(db, ticker, name)
    end = date.today().isoformat()
    logger.info("Fetching %s from %s to %s", ticker, MACRO_START, end)
    raw = yf.download(ticker, start=MACRO_START, end=end, progress=False, auto_adjust=False)
    if raw.empty:
        logger.error("yfinance returned empty history for %s", ticker)
        return 0

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0] for c in raw.columns]

    existing_dates = {
        d for (d,) in db.query(PriceHistory.date).filter_by(ticker=ticker).all()
    }
    inserted = 0
    for ts, row in raw.iterrows():
        d = ts.date() if hasattr(ts, "date") else ts
        if d in existing_dates:
            continue
        db.add(PriceHistory(
            ticker=ticker,
            date=d,
            open=float(row.get("Open", 0) or 0),
            high=float(row.get("High", 0) or 0),
            low=float(row.get("Low", 0) or 0),
            close=float(row.get("Close", 0) or 0),
            volume=float(row.get("Volume", 0) or 0),
            adj_close=float(row.get("Adj Close", row.get("Close", 0)) or 0),
        ))
        inserted += 1
    db.commit()
    logger.info("%s: inserted %d new rows (already had %d)", ticker, inserted, len(existing_dates))
    return inserted


def backfill_earnings(db, tickers: list[str]) -> tuple[int, int]:
    inserted = 0
    failed = 0
    for tk in tickers:
        if tk.startswith("^"):
            continue
        try:
            yt = yf.Ticker(tk)
            df = yt.earnings_dates  # property — past + future
            if df is None or df.empty:
                logger.warning("%s: no earnings_dates from yfinance", tk)
                continue
            existing = {
                d for (d,) in db.query(EarningsCalendar.earnings_date).filter_by(ticker=tk).all()
            }
            n_new = 0
            for ts in df.index:
                ed = ts.date() if hasattr(ts, "date") else ts
                if ed in existing:
                    continue
                db.add(EarningsCalendar(
                    ticker=tk,
                    earnings_date=ed,
                    source="yfinance",
                    fetched_at=datetime.utcnow(),
                ))
                n_new += 1
            db.commit()
            inserted += n_new
            logger.info("%s: inserted %d earnings dates (had %d)", tk, n_new, len(existing))
        except Exception as e:
            failed += 1
            db.rollback()
            logger.error("%s: earnings backfill failed: %s", tk, e)
    return inserted, failed


def main() -> int:
    settings = get_settings()
    db = SessionLocal()
    try:
        macro_added: dict[str, int] = {}
        for tk, name in MACRO_TICKERS:
            macro_added[tk] = backfill_index_ticker(db, tk, name)
        tickers = [t for t in settings.default_watchlist if not t.startswith("^")]
        logger.info("Backfilling earnings for %d tickers", len(tickers))
        earn_added, earn_failed = backfill_earnings(db, tickers)
        print()
        for tk, n in macro_added.items():
            print(f"{tk:<10} rows added: {n}")
        print(f"Earnings rows added:   {earn_added}")
        print(f"Tickers failed:        {earn_failed}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
