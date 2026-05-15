"""Backfill SEC Form 8-K filings for all watchlist tickers (P10-009).

Pulls per-ticker 8-K filing metadata from the SEC EDGAR submissions endpoint
(`submissions/CIK{cik}.json`). One GET per ticker yields up to 1000 of the
most-recent filings; older filings live in `filings.files` chunks that the
fetcher walks only when the lookback window requires it.

Idempotent — `accession_number` carries a unique index, so re-runs detect
existing rows and skip them.

Default lookback is 1825 days (~5 years) to give the directional model a
deep history of catalyst events. SEC EDGAR has full 8-K history back to
1994; the per-ticker recent slice is usually deep enough for 5 years.

Run inside the backend container:

    docker exec backend-backend-1 python -m scripts.backfill_sec_filings
    docker exec backend-backend-1 python -m scripts.backfill_sec_filings --lookback-days 1095
    docker exec backend-backend-1 python -m scripts.backfill_sec_filings --tickers AAPL,MSFT
"""

from __future__ import annotations

import argparse
import logging
import sys

from src.config import get_settings
from src.db.session import SessionLocal
from src.pipeline.sec_8k_fetcher import SEC8KFetcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_sec_filings")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill SEC Form 8-K filings")
    p.add_argument("--lookback-days", type=int, default=1825,
                   help="Days of history to backfill (default 1825 ~ 5 years)")
    p.add_argument("--tickers", type=str, default=None,
                   help="Comma-separated ticker subset (default = full watchlist)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = [t for t in settings.default_watchlist if not t.startswith("^")]

    logger.info(
        "Backfilling SEC 8-K filings for %d tickers (lookback %d days)",
        len(tickers), args.lookback_days,
    )

    db = SessionLocal()
    try:
        with SEC8KFetcher(db=db) as fetcher:
            results = fetcher.fetch_all(
                tickers=tickers,
                lookback_days=args.lookback_days,
            )
    finally:
        db.close()

    counts: dict[str, int] = {}
    for status in results.values():
        counts[status] = counts.get(status, 0) + 1

    print()
    print(f"Tickers processed: {len(results)}")
    for status, n in sorted(counts.items()):
        print(f"  {status:<12} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
