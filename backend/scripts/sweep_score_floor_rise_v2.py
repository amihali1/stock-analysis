"""Sweep min-score floor on joint top-K backtest using rise v2 (excess label).

Rebuilds the merged drop/rise dataset once, then evaluates each floor against
the same time-series folds. Captures aggregate AUCs, hit rates, and avg P&L per
floor so we can pick the floor that turns the system from net-negative to
net-positive (if any exists).

Usage:
    python scripts/sweep_score_floor_rise_v2.py --folds 4 --top-k 10
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

# Reuse helpers from the joint backtest script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_joint_topk_backtest import (  # type: ignore
    _build_merged_dataset,
    run_joint_backtest,
)

from src.models.directional import DirectionalModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("score_floor_sweep_v2")

DEFAULT_FLOORS = [None, 0.50, 0.55, 0.60, 0.65, 0.70]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--train-min-rows", type=int, default=1000)
    parser.add_argument("--floors", nargs="*", type=str, default=None,
                        help="Space-separated floors; use 'none' for no floor. "
                             f"Default: {DEFAULT_FLOORS}")
    parser.add_argument("--out", type=str,
                        default="/app/trained_models/score_floor_sweep_rise_v2.json")
    args = parser.parse_args()

    if args.floors is None:
        floors = DEFAULT_FLOORS
    else:
        floors = [None if f.lower() == "none" else float(f) for f in args.floors]

    logger.info("Building merged dataset (rise label_mode=excess)...")
    df = _build_merged_dataset(rise_label_mode="excess")
    feature_cols = DirectionalModel().feature_cols
    logger.info("Dataset: %d rows, %d features", len(df), len(feature_cols))

    rows = []
    for floor in floors:
        tag = "none" if floor is None else f"{floor:.2f}"
        logger.info("=== Running floor=%s ===", tag)
        try:
            result = run_joint_backtest(
                df=df, feature_cols=feature_cols,
                n_folds=args.folds, top_k=args.top_k,
                train_min_rows=args.train_min_rows,
                min_score=floor,
            )
            agg = result.aggregate
            rows.append({
                "floor": tag,
                "mean_drop_auc": agg.get("mean_drop_auc", 0.0),
                "mean_rise_auc": agg.get("mean_rise_auc", 0.0),
                "mean_drop_hit_rate": agg.get("mean_drop_hit_rate", 0.0),
                "mean_rise_hit_rate": agg.get("mean_rise_hit_rate", 0.0),
                "mean_avg_pnl": agg.get("mean_avg_pnl", 0.0),
                "total_selected": agg.get("total_selected", 0),
                "direction_split": result.direction_split,
            })
        except Exception as e:
            logger.error("Floor=%s failed: %s", tag, e)
            rows.append({"floor": tag, "error": str(e)})

    payload = {
        "generated_at": date.today().isoformat(),
        "folds": args.folds,
        "top_k": args.top_k,
        "rise_label_mode": "excess",
        "results": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Wrote %s", out)

    print("\nFloor | drop AUC | rise AUC | drop hit | rise hit | avg P&L | trades | drop/rise")
    print("------|----------|----------|----------|----------|---------|--------|----------")
    for r in rows:
        if "error" in r:
            print(f"{r['floor']:>5} | ERROR: {r['error']}")
            continue
        ds = r["direction_split"]
        d = ds.get("drop", 0)
        ri = ds.get("rise", 0)
        print(
            f"{r['floor']:>5} | {r['mean_drop_auc']:.4f}   | {r['mean_rise_auc']:.4f}   | "
            f"{r['mean_drop_hit_rate']*100:5.1f}%   | {r['mean_rise_hit_rate']*100:5.1f}%   | "
            f"{r['mean_avg_pnl']:+.4f} | {r['total_selected']:6d} | {d}/{ri}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
