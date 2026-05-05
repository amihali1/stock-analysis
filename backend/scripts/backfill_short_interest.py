"""Backfill short interest snapshots for all watchlist tickers (P10-003).

yfinance.info returns up to two FINRA settlement reports per call:
- current: `dateShortInterest` + `sharesShort` + `shortPercentOfFloat` + `shortRatio`
- prior:   `sharesShortPreviousMonthDate` + `sharesShortPriorMonth`

Each backfill run can therefore add 0-2 historical points per ticker.
Idempotent — uses the unique (ticker, report_date) index.

Run inside the backend container:
    docker exec backend-backend-1 python -m scripts.backfill_short_interest
"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import logging
import sys
from datetime import datetime, date

import yfinance as yf

from src.config import get_settings
from src.db.models import ShortInterestSnapshot
from src.db.session import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_short_interest")


def _to_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, (int, float)):
        try:
            return datetime.utcfromtimestamp(int(v)).date()
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v).date()
        except ValueError:
            return None
    if hasattr(v, "date"):
        try:
            return v.date()
        except Exception:
            return None
    return None


def _f(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN check
            return None
        return f
    except (TypeError, ValueError):
        return None


def backfill_one(db, ticker: str) -> tuple[int, int]:
    """Insert any new short-interest snapshots for `ticker`. Returns (inserted, total_seen)."""
    yt = yf.Ticker(ticker)
    info = yt.info or {}

    # Existing report_dates so we can skip
    existing = {
        d for (d,) in db.query(ShortInterestSnapshot.report_date)
        .filter_by(ticker=ticker).all()
    }

    snapshots: list[tuple[date, float | None, float | None, float | None]] = []

    cur_date = _to_date(info.get("dateShortInterest"))
    if cur_date:
        snapshots.append((
            cur_date,
            _f(info.get("sharesShort")),
            _f(info.get("shortPercentOfFloat")),
            _f(info.get("shortRatio")),
        ))

    prior_date = _to_date(info.get("sharesShortPreviousMonthDate"))
    if prior_date:
        # Only short ratio + percent-of-float for current; prior month gives shares only.
        # Reuse current ratios as a coarse fill — better than NULL since the prior
        # snapshot is mostly used for change-pct, not for absolute values.
        snapshots.append((
            prior_date,
            _f(info.get("sharesShortPriorMonth")),
            _f(info.get("shortPercentOfFloat")),
            _f(info.get("shortRatio")),
        ))

    if not snapshots:
        # Stub row so the ticker shows has_data=0 in the table
        # — only if no prior snapshot exists at all.
        if not existing:
            db.add(ShortInterestSnapshot(
                ticker=ticker,
                report_date=date.today(),
                shares_short=None,
                short_percent_of_float=None,
                short_ratio_days_to_cover=None,
                has_data=0,
                fetched_at=datetime.utcnow(),
            ))
            db.commit()
            logger.warning("%s: no short-interest data, wrote stub", ticker)
            return 0, 0
        logger.warning("%s: no short-interest data this fetch (existing rows kept)", ticker)
        return 0, 0

    inserted = 0
    seen_in_batch: set[date] = set()
    for d, shares, sp_float, days_cover in snapshots:
        if d in existing or d in seen_in_batch:
            continue
        seen_in_batch.add(d)
        db.add(ShortInterestSnapshot(
            ticker=ticker,
            report_date=d,
            shares_short=shares,
            short_percent_of_float=sp_float,
            short_ratio_days_to_cover=days_cover,
            has_data=1,
            fetched_at=datetime.utcnow(),
        ))
        inserted += 1
    db.commit()
    logger.info("%s: inserted %d new (had %d, fetched %d)",
                ticker, inserted, len(existing), len(snapshots))
    return inserted, len(snapshots)


def main() -> int:
    settings = get_settings()
    db = SessionLocal()
    try:
        tickers = [t for t in settings.default_watchlist if not t.startswith("^")]
        logger.info("Backfilling short interest for %d tickers", len(tickers))
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
        print(f"Total snapshots inserted: {total_inserted}")
        print(f"Tickers failed:           {failed}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
