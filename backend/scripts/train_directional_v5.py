"""Train v5: v3 + INSIDER_FEATURE_COLS (P10-005).

Trains the directional model with the current `FEATURE_COLS` (which now
includes insider features after P10-005 part 4), saves the artifact to
`trained_models/directional_xgb_v5.pkl`, and promotes to
`directional_xgb_v1.pkl` (the served model) only if test AUC clears
v3's baseline by at least 0.005.

Run inside the backend container:
    docker exec backend-backend-1 python -m scripts.train_directional_v5
"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

from src.features.insider import INSIDER_FEATURE_COLS
from src.models.directional import DirectionalModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("train_v5")

PROMOTION_DELTA = 0.005


def main() -> int:
    out_path = Path("/app/trained_models/directional_xgb_v5.pkl")
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
    print("=== Insider feature importance ===")
    insider_set = set(INSIDER_FEATURE_COLS)
    for name, imp in metrics["feature_importance"].items():
        if name in insider_set:
            print(f"  {name:<32s} {imp:.4f}")

    summary_path = out_path.with_suffix(".metrics.json")
    summary_path.write_text(json.dumps(metrics, default=float, indent=2))
    print(f"\nMetrics JSON: {summary_path}")

    v3_metrics_path = out_path.parent / "directional_xgb_v3.metrics.json"
    if not v3_metrics_path.exists():
        print(f"\nNo v3 metrics at {v3_metrics_path} — skipping promotion check.")
        return 0

    v3 = json.loads(v3_metrics_path.read_text())["test_metrics"]
    print()
    print("=== v5 vs v3 ===")
    print(f"  metric        v3        v5        delta")
    for key in ("auc_roc", "accuracy", "precision", "recall", "f1", "brier_score"):
        v3v = v3.get(key) or 0.0
        v5v = tm.get(key) or 0.0
        print(f"  {key:<12s}  {v3v:>7.4f}   {v5v:>7.4f}   {v5v - v3v:+.4f}")

    v3_auc = float(v3.get("auc_roc") or 0.0)
    v5_auc = float(tm.get("auc_roc") or 0.0)
    delta = v5_auc - v3_auc
    served_path = out_path.parent / "directional_xgb_v1.pkl"
    print()
    print(f"Promotion gate: v5 AUC must clear v3 by >= {PROMOTION_DELTA:.4f}  "
          f"(delta = {delta:+.4f})")
    if delta >= PROMOTION_DELTA:
        if served_path.exists():
            backup = served_path.with_suffix(
                f".pkl.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
            )
            shutil.copy2(served_path, backup)
            print(f"Backed up existing served model to {backup}")
        shutil.copy2(out_path, served_path)
        print(f"PROMOTED v5 -> {served_path}")
    else:
        print("NOT promoted — keeping current served model.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
