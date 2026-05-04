"""Train v3: same as v2 but includes ANALYST_FEATURE_COLS (P10-001).

Saves as `trained_models/directional_xgb_v3.pkl`. Compare the test AUC against
v2's metrics in `directional_xgb_v2.metrics.json` to decide whether the analyst
rating-change features moved the needle past the AUC ~0.555 information ceiling.

Run inside the backend container:
    docker exec backend-backend-1 python -m scripts.train_directional_v3
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
logger = logging.getLogger("train_v3")


def main() -> int:
    out_path = Path("/app/trained_models/directional_xgb_v3.pkl")
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
        print(f"  {name:<28s} {imp:.4f}")

    print()
    print("=== Analyst feature importance ===")
    for name, imp in metrics["feature_importance"].items():
        if name in {"days_since_downgrade", "days_since_upgrade",
                    "downgrades_30d", "upgrades_30d",
                    "net_rating_actions_60d", "analyst_action_5d"}:
            print(f"  {name:<28s} {imp:.4f}")

    summary_path = out_path.with_suffix(".metrics.json")
    summary_path.write_text(json.dumps(metrics, default=float, indent=2))
    print(f"\nMetrics JSON: {summary_path}")

    # Side-by-side vs v2 baseline if present.
    v2_metrics_path = out_path.parent / "directional_xgb_v2.metrics.json"
    if v2_metrics_path.exists():
        v2 = json.loads(v2_metrics_path.read_text())["test_metrics"]
        print()
        print("=== v3 vs v2 ===")
        print(f"  metric        v2        v3        delta")
        for key in ("auc_roc", "accuracy", "precision", "recall", "f1", "brier_score"):
            v2v = v2.get(key) or 0.0
            v3v = tm.get(key) or 0.0
            print(f"  {key:<12s}  {v2v:>7.4f}   {v3v:>7.4f}   {v3v - v2v:+.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
