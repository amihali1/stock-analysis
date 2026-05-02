"""Backfill analyst rating changes for all watchlist tickers (P10-001).

Pulls `Ticker.upgrades_downgrades` from yfinance for every non-index watchlist
ticker and inserts new rows into `analyst_ratings`. Idempotent — uses the
unique (ticker, date, firm, to_grade) index to skip duplicates already loaded.

Run inside the backend container:
    docker exec backend-backend-1 python -m scripts.backfill_analyst_ratings
"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import logging
import sys
from datetime import datetime

import pandas as pd
import yfinance as yf

from src.config import get_settings
from src.db.models import AnalystRating
from src.db.session import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_analyst")


def _normalize_action(raw: str | None) -> str:
    if raw is None:
        return "main"
    s = str(raw).strip().lower()
    if not s:
        return "main"
    # yfinance values: up, down, init, main, reit (sometimes longer forms)
    if s.startswith("up"):
        return "up"
    if s.startswith("down"):
        return "down"
    if s.startswith("init"):
        return "init"
    if s.startswith("reit"):
        return "reit"
    return s[:20]


def backfill_one(db, ticker: str) -> tuple[int, int]:
    """Insert any new rating changes for `ticker`. Returns (inserted, total_seen)."""
    yt = yf.Ticker(ticker)
    df = yt.upgrades_downgrades
    if df is None or df.empty:
        logger.warning("%s: no upgrades_downgrades from yfinance", ticker)
        return 0, 0

    existing_keys = {
        (d, (firm or ""), (to_grade or ""))
        for (d, firm, to_grade) in db.query(
            AnalystRating.date, AnalystRating.firm, AnalystRating.to_grade,
        ).filter_by(ticker=ticker).all()
    }

    inserted = 0
    total = 0
    for ts, row in df.iterrows():
        total += 1
        d = ts.date() if hasattr(ts, "date") else ts
        firm = str(row.get("Firm", "") or "")[:100]
        to_grade = str(row.get("ToGrade", "") or "")[:50]
        from_grade = str(row.get("FromGrade", "") or "")[:50]
        action = _normalize_action(row.get("Action"))

        if (d, firm, to_grade) in existing_keys:
            continue
        db.add(AnalystRating(
            ticker=ticker,
            date=d,
            firm=firm,
            from_grade=from_grade,
            to_grade=to_grade,
            action=action,
            source="yfinance",
            fetched_at=datetime.utcnow(),
        ))
        inserted += 1
    db.commit()
    logger.info("%s: inserted %d new (had %d, total seen %d)",
                ticker, inserted, len(existing_keys), total)
    return inserted, total


def main() -> int:
    settings = get_settings()
    db = SessionLocal()
    try:
        tickers = [t for t in settings.default_watchlist if not t.startswith("^")]
        logger.info("Backfilling analyst ratings for %d tickers", len(tickers))
        total_inserted = 0
        failed = 0
        for tk in tickers:
            try:
                ins, _ = backfill_one(db, tk)
                total_inserted += ins
            except Exception as e:
                failed += 1
                db.rollback()
                logger.error("%s: backfill failed: %s", tk, e)
        print()
        print(f"Total rating rows inserted: {total_inserted}")
        print(f"Tickers failed:             {failed}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
