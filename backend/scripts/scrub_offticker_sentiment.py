"""One-shot scrub of off-ticker sentiment rows.

Applies the same `_is_relevant_to_ticker` filter that now gates new
sentiment ingestion (commit 7897191) to every existing row in
`sentiment_scores`. Future model retrains that pull from this table
would otherwise inherit pre-fix pollution (e.g. Viking Therapeutics
headlines tagged INTC, Walmart headlines tagged INTC, etc.).

Usage:
  # Dry run — print per-ticker counts, change nothing.
  python -m scripts.scrub_offticker_sentiment

  # Actually delete.
  python -m scripts.scrub_offticker_sentiment --apply

The script processes tickers one at a time, batches deletes per
ticker, and commits per ticker so a mid-run failure leaves earlier
tickers cleaned rather than rolling back everything.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict

from src.db.models import SentimentScore, Stock
from src.db.session import SessionLocal
from src.pipeline.sentiment import _is_relevant_to_ticker, _ticker_aliases

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _build_alias_map(db) -> dict[str, list[str]]:
    """Map every ticker that has sentiment rows to its alias list."""
    stocks = {s.ticker: s.name for s in db.query(Stock).all()}
    tickers_with_sentiment = {
        t for (t,) in db.query(SentimentScore.ticker).distinct().all()
    }
    return {
        ticker: _ticker_aliases(ticker, stocks.get(ticker))
        for ticker in tickers_with_sentiment
    }


def _classify_rows(db, ticker: str, aliases: list[str]) -> tuple[list[int], int]:
    """Return (ids_to_drop, kept_count) for one ticker."""
    rows = db.query(SentimentScore.id, SentimentScore.headline).filter_by(
        ticker=ticker,
    ).all()
    drop_ids: list[int] = []
    kept = 0
    for row_id, headline in rows:
        if not headline:
            drop_ids.append(row_id)
            continue
        if _is_relevant_to_ticker(headline, aliases):
            kept += 1
        else:
            drop_ids.append(row_id)
    return drop_ids, kept


def run(apply: bool) -> None:
    db = SessionLocal()
    try:
        alias_map = _build_alias_map(db)
        if not alias_map:
            logger.info("No tickers with sentiment rows found — nothing to do.")
            return

        summary: dict[str, tuple[int, int]] = {}
        total_drop = 0
        total_keep = 0

        for ticker in sorted(alias_map):
            aliases = alias_map[ticker]
            drop_ids, kept = _classify_rows(db, ticker, aliases)
            summary[ticker] = (len(drop_ids), kept)
            total_drop += len(drop_ids)
            total_keep += kept

            if apply and drop_ids:
                # Batch delete in chunks; large IN-lists choke some DBs.
                for chunk_start in range(0, len(drop_ids), 500):
                    chunk = drop_ids[chunk_start : chunk_start + 500]
                    db.query(SentimentScore).filter(SentimentScore.id.in_(chunk)).delete(
                        synchronize_session=False,
                    )
                db.commit()

        # Per-ticker summary table, sorted by drop count desc so the worst
        # offenders surface at the top.
        print(f"{'TICKER':<8}{'DROP':>8}{'KEEP':>8}{'DROP %':>10}  aliases")
        print("-" * 80)
        for ticker, (drop, keep) in sorted(
            summary.items(), key=lambda kv: -kv[1][0]
        ):
            total = drop + keep
            pct = (drop / total * 100) if total else 0.0
            aliases = alias_map[ticker]
            print(f"{ticker:<8}{drop:>8}{keep:>8}{pct:>9.1f}%  {aliases}")

        print()
        grand_total = total_drop + total_keep
        grand_pct = (total_drop / grand_total * 100) if grand_total else 0.0
        action = "DELETED" if apply else "WOULD DELETE"
        print(
            f"{action} {total_drop:,} of {grand_total:,} rows "
            f"({grand_pct:.1f}%); kept {total_keep:,}"
        )
        if not apply:
            print("Dry run — pass --apply to commit deletions.")

    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete off-ticker rows. Without this, runs as dry run.",
    )
    args = parser.parse_args()
    run(apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
