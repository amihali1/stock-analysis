"""Pipeline runner: orchestrates data fetch → feature computation."""

from __future__ import annotations

import logging
import time

from src.db.session import SessionLocal
from src.pipeline.data_fetcher import DataFetcher
from src.pipeline.feature_eng import FeatureEngineer
from src.config import get_settings

logger = logging.getLogger(__name__)


def run_pipeline(tickers: list[str] | None = None, period: str = "2y") -> dict:
    """Run the full data pipeline: fetch prices → compute features.

    Returns summary dict with counts per ticker.
    """
    if tickers is None:
        tickers = get_settings().default_watchlist

    db = SessionLocal()
    summary = {"tickers": {}, "elapsed_seconds": 0.0}

    try:
        t0 = time.time()

        # Step 1: Fetch price data
        logger.info(f"Step 1/2: Fetching prices for {len(tickers)} tickers")
        fetcher = DataFetcher(db=db)
        fetch_results = fetcher.fetch_daily(tickers=tickers, period=period)

        # Step 2: Compute features
        logger.info(f"Step 2/2: Computing technical indicators")
        engineer = FeatureEngineer(db=db)
        feature_results = engineer.compute_all(tickers=tickers)

        elapsed = time.time() - t0
        summary["elapsed_seconds"] = round(elapsed, 1)

        for ticker in tickers:
            summary["tickers"][ticker] = {
                "prices_fetched": fetch_results.get(ticker, 0),
                "indicators_computed": feature_results.get(ticker, 0),
            }

        logger.info(f"Pipeline complete in {elapsed:.1f}s — {len(tickers)} tickers processed")

    finally:
        db.close()

    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from src.db.models import Base
    from src.db.session import engine
    Base.metadata.create_all(engine)

    test_tickers = ["AAPL", "MSFT", "GOOGL", "SPY", "NVDA"]
    result = run_pipeline(tickers=test_tickers, period="2y")

    print(f"\nPipeline finished in {result['elapsed_seconds']}s")
    print(f"{'Ticker':<8} {'Prices':>8} {'Indicators':>12}")
    print("-" * 30)
    for ticker, data in result["tickers"].items():
        print(f"{ticker:<8} {data['prices_fetched']:>8} {data['indicators_computed']:>12}")
