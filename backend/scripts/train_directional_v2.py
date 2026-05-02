"""Train and persist a fresh directional model with backfilled Phase 9 data.

Saves as `trained_models/directional_xgb_v2.pkl` so v1 is preserved as a rollback.

Run inside the backend container:
    docker exec backend-backend-1 python -m scripts.train_directional_v2
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
logger = logging.getLogger("train_v2")


def main() -> int:
    out_path = Path("/app/trained_models/directional_xgb_v2.pkl")
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
    print("=== Top 10 feature importance ===")
    for i, (name, imp) in enumerate(metrics["feature_importance"].items()):
        if i >= 10:
            break
        print(f"  {name:<28s} {imp:.4f}")

    # Emit a JSON summary for downstream tooling.
    summary_path = out_path.with_suffix(".metrics.json")
    summary_path.write_text(json.dumps(metrics, default=float, indent=2))
    print(f"\nMetrics JSON: {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
