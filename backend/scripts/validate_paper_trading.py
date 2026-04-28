"""CLI entrypoint for paper-vs-backtest validation.

Usage:
    python scripts/validate_paper_trading.py --start 2026-04-01 --end 2026-04-21
    python scripts/validate_paper_trading.py --days 14
    python scripts/validate_paper_trading.py --days 14 --json out.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow running as a script from the backend/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.session import SessionLocal  # noqa: E402
from src.services.paper_validation import PaperValidator, format_report  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare Alpaca paper trades vs backtester results.")
    p.add_argument("--start", type=date.fromisoformat, help="Window start date (YYYY-MM-DD)")
    p.add_argument("--end", type=date.fromisoformat, help="Window end date (YYYY-MM-DD)")
    p.add_argument("--days", type=int, default=14, help="If --start/--end omitted, use last N days (default 14)")
    p.add_argument("--json", type=Path, help="Optional path to write JSON report")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args()

    end = args.end or date.today()
    start = args.start or (end - timedelta(days=args.days))

    db = SessionLocal()
    try:
        report = PaperValidator(db).validate(start, end)
    finally:
        db.close()

    print(format_report(report))

    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str))
        print(f"\nWrote JSON report to {args.json}")

    # Non-zero exit if divergences found, useful for CI gating before going live
    return 1 if report["divergences"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
