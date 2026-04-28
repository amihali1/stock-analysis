"""CLI entry point for the walk-forward backtest harness (P9-007).

Usage:
    python scripts/run_backtest.py --folds 4 --threshold 0.5 [--git-sha abc1234]

Writes a markdown report to backend/backtest_reports/<date>-<sha>.md
and exits non-zero if the ship-gate (AUC > 0.55, hit_rate > 52%) fails.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.backtest.report import SHIP_AUC, SHIP_HIT_RATE, write_report
from src.backtest.walk_forward import walk_forward
from src.models.directional import DirectionalModel

REPORT_DIR = Path(__file__).resolve().parent.parent / "backtest_reports"
MODEL_METRICS = Path(__file__).resolve().parent.parent / "trained_models" / "directional_metrics.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_backtest")


def _load_old_metrics() -> dict | None:
    if not MODEL_METRICS.exists():
        return None
    with MODEL_METRICS.open() as f:
        data = json.load(f)
    return {
        "auc_roc": data.get("auc_roc"),
        "brier_score": data.get("brier_score"),
        "hit_rate": data.get("hit_rate"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Walk-forward directional backtest")
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="Probability threshold for triggering a trade")
    parser.add_argument("--train-min-rows", type=int, default=1000)
    parser.add_argument("--git-sha", default="local")
    parser.add_argument("--no-fail", action="store_true",
                        help="Always exit 0, even if ship gate fails")
    args = parser.parse_args()

    feature_cols = DirectionalModel().feature_cols
    result = walk_forward(
        feature_cols=feature_cols,
        n_folds=args.folds,
        train_min_rows=args.train_min_rows,
        confidence_threshold=args.threshold,
    )

    out = write_report(result, REPORT_DIR, git_sha=args.git_sha,
                       old_metrics=_load_old_metrics())
    logger.info("Report written: %s", out)

    agg = result.aggregate
    print(f"\nMean AUC: {agg['mean_auc']:.4f}  Hit rate: {agg['mean_hit_rate']:.2%}")
    failed = agg["mean_auc"] <= SHIP_AUC or agg["mean_hit_rate"] <= SHIP_HIT_RATE
    if failed and not args.no_fail:
        print("Ship gate FAILED — model not deployable.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
