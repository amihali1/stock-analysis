"""Backfill SEC Form 4 insider transactions for all watchlist tickers (P10-005).

Pulls per-ticker Form 4 filings from the SEC EDGAR submissions endpoint and
parses each filing's primary XML into one aggregated `insider_transactions`
row. Idempotent — `accession_number` carries a unique index, so re-runs
detect existing rows and skip the XML fetch entirely (cheap on repeat).

Default lookback is 730 days (~2 years) to match the directional model's
training window. SEC's `submissions/CIK{cik}.json` returns the most recent
~1000 filings; for the vast majority of watchlist names, 2 years' worth of
Form 4s fits comfortably inside that envelope.

Run inside the backend container:

    docker exec backend-backend-1 python -m scripts.backfill_insider_transactions
    docker exec backend-backend-1 python -m scripts.backfill_insider_transactions --lookback-days 365
    docker exec backend-backend-1 python -m scripts.backfill_insider_transactions --tickers AAPL,MSFT
"""

from __future__ import annotations

import argparse
import logging
import sys

from src.config import get_settings
from src.db.session import SessionLocal
from src.pipeline.insider_fetcher import InsiderTransactionFetcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_insider_transactions")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill SEC Form 4 insider transactions")
    p.add_argument("--lookback-days", type=int, default=730,
                   help="Days of history to backfill (default 730 ~ 2 years)")
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
        "Backfilling Form 4 insider transactions for %d tickers (lookback %d days)",
        len(tickers), args.lookback_days,
    )

    db = SessionLocal()
    try:
        with InsiderTransactionFetcher(db=db) as fetcher:
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
