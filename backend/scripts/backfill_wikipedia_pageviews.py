"""Backfill Wikipedia daily page-views for all watchlist tickers (P10-008).

Pulls per-article daily counts from the Wikimedia REST API. Idempotent —
the unique (ticker, view_date) index plus an upsert in the fetcher means
re-runs are safe and cheap. Default lookback is 730 days (~2 years), which
is the same window the directional model trains on.

Run inside the backend container:
    docker exec backend-backend-1 python -m scripts.backfill_wikipedia_pageviews
    docker exec backend-backend-1 python -m scripts.backfill_wikipedia_pageviews --lookback-days 365
    docker exec backend-backend-1 python -m scripts.backfill_wikipedia_pageviews --tickers AAPL,MSFT
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

from src.config import get_settings
from src.db.session import SessionLocal
from src.pipeline.wikipedia_fetcher import WikipediaPageviewFetcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_wikipedia_pageviews")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill Wikipedia page views")
    p.add_argument("--lookback-days", type=int, default=730,
                   help="Days of history to backfill (default 730 ~ 2 years)")
    p.add_argument("--tickers", type=str, default=None,
                   help="Comma-separated ticker subset (default = full watchlist)")
    p.add_argument("--end-date", type=str, default=None,
                   help="ISO end date (default = yesterday)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()

    end_date = (
        date.fromisoformat(args.end_date)
        if args.end_date
        else date.today() - timedelta(days=1)
    )
    start_date = end_date - timedelta(days=args.lookback_days)

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = [t for t in settings.default_watchlist if not t.startswith("^")]

    logger.info(
        "Backfilling Wikipedia pageviews for %d tickers from %s to %s",
        len(tickers), start_date, end_date,
    )

    db = SessionLocal()
    try:
        with WikipediaPageviewFetcher(db=db) as fetcher:
            results = fetcher.fetch_all(
                tickers=tickers,
                start_date=start_date,
                end_date=end_date,
            )
    finally:
        db.close()

    counts: dict[str, int] = {}
    for status in results.values():
        counts[status] = counts.get(status, 0) + 1

    print()
    print(f"Tickers processed: {len(results)}")
    for status, n in sorted(counts.items()):
        print(f"  {status:<10} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
