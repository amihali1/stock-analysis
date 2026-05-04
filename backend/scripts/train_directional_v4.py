"""Train v4: v3 + SHORT_INTEREST_FEATURE_COLS (P10-003).

Saves as `trained_models/directional_xgb_v4.pkl`. Compare the test AUC against
v3's metrics in `directional_xgb_v3.metrics.json` to decide whether the short-
interest features moved the needle past v3's 0.5505.

Run inside the backend container:
    docker exec backend-backend-1 python -m scripts.train_directional_v4
"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import json
import logging
import sys
from pathlib import Path

from src.models.directional import DirectionalModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("train_v4")


def main() -> int:
    out_path = Path("/app/trained_models/directional_xgb_v4.pkl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    model = DirectionalModel(model_path=out_path)
    metrics = model.train(n_folds=3)

    logger.info("Training complete; saved to %s", out_path)
    print()
    print("=== Walk-forward fold metrics ===")
    for fm in metrics["fold_metrics"]:
        print(
            f"  fold {fm['fold']}: auc={fm['auc_roc']:.3f}  acc={fm['accuracy']:.3f}  "
            f"prec={fm['precision']:.3f}  rec={fm['recall']:.3f}  n_train={fm['train_size']}  n_val={fm['val_size']}"
        )

    tm = metrics["test_metrics"]
    print()
    print("=== Final held-out test ===")
    print(f"  AUC:        {tm['auc_roc']:.4f}")
    print(f"  Accuracy:   {tm['accuracy']:.4f}")
    print(f"  Precision:  {tm['precision']:.4f}")
    print(f"  Recall:     {tm['recall']:.4f}")
    print(f"  F1:         {tm['f1']:.4f}")
    print(f"  Brier:      {tm['brier_score']}")
    print(f"  Calibrated: {tm['calibrated']}")
    print(f"  Pos rate:   {metrics['positive_rate']:.4f}")
    print(f"  Dataset:    {metrics['dataset_size']} rows")

    print()
    print("=== Top 15 feature importance ===")
    for i, (name, imp) in enumerate(metrics["feature_importance"].items()):
        if i >= 15:
            break
        print(f"  {name:<32s} {imp:.4f}")

    print()
    print("=== Short-interest feature importance ===")
    for name, imp in metrics["feature_importance"].items():
        if name in {"short_percent_of_float", "short_ratio_days_to_cover",
                    "short_interest_change_pct", "short_interest_zscore_180d",
                    "days_since_short_report", "has_short_data"}:
            print(f"  {name:<32s} {imp:.4f}")

    summary_path = out_path.with_suffix(".metrics.json")
    summary_path.write_text(json.dumps(metrics, default=float, indent=2))
    print(f"\nMetrics JSON: {summary_path}")

    # Side-by-side vs v3 baseline if present.
    v3_metrics_path = out_path.parent / "directional_xgb_v3.metrics.json"
    if v3_metrics_path.exists():
        v3 = json.loads(v3_metrics_path.read_text())["test_metrics"]
        print()
        print("=== v4 vs v3 ===")
        print(f"  metric        v3        v4        delta")
        for key in ("auc_roc", "accuracy", "precision", "recall", "f1", "brier_score"):
            v3v = v3.get(key) or 0.0
            v4v = tm.get(key) or 0.0
            print(f"  {key:<12s}  {v3v:>7.4f}   {v4v:>7.4f}   {v4v - v3v:+.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
