"""Train drop v7: vol-normalized label, K=1.75.

Walk-forward sweep (2026-05-15) selected K=1.75 as the AUC peak
(0.598 ± 0.021 over 5 seeds × 3 expanding folds). This script trains a
single production-shaped model on that label and saves to
`trained_models/directional_xgb_v7.pkl` + sidecar metrics JSON.

No auto-promotion. Promotion to `directional_xgb_v1.pkl` (served) is a
manual gate-check step because v7's AUC is on the vol-normalized label
and v3 sigmoid's AUC is on the absolute label — direct number compare
is misleading. The gate check is: does v7 produce more (and better)
short recommendations on the production threshold pipeline?

Run inside the backend container:
    docker exec backend-backend-1 python -m scripts.train_directional_v7
"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import json
import logging
import sys
from pathlib import Path

from src.models.directional import (
    DEFAULT_DROP_VOL_K,
    DirectionalModel,
    LABEL_MODE_VOL_NORMALIZED,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("train_v7")


def main() -> int:
    out_path = Path("/app/trained_models/directional_xgb_v7.pkl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    model = DirectionalModel(
        model_path=out_path,
        direction="drop",
        label_mode=LABEL_MODE_VOL_NORMALIZED,
        vol_k=DEFAULT_DROP_VOL_K,
        calibration_method="sigmoid",
    )
    metrics = model.train(n_folds=3)

    logger.info("Training complete; saved to %s", out_path)

    print()
    print("=== Walk-forward fold metrics ===")
    for fm in metrics["fold_metrics"]:
        print(
            f"  fold {fm['fold']}: auc={fm['auc_roc']:.3f}  acc={fm['accuracy']:.3f}  "
            f"prec={fm['precision']:.3f}  rec={fm['recall']:.3f}  "
            f"n_train={fm['train_size']}  n_val={fm['val_size']}"
        )

    tm = metrics["test_metrics"]
    print()
    print("=== Final held-out test ===")
    print(f"  Label mode: vol_normalized  K={DEFAULT_DROP_VOL_K}")
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

    summary_path = out_path.with_suffix(".metrics.json")
    summary_path.write_text(
        json.dumps(
            {
                **metrics,
                "label_mode": LABEL_MODE_VOL_NORMALIZED,
                "vol_k": DEFAULT_DROP_VOL_K,
            },
            default=float,
            indent=2,
        )
    )
    print(f"\nMetrics JSON: {summary_path}")

    walk_forward_path = out_path.parent / "walk_forward_drop_vol_k_1.75.json"
    if walk_forward_path.exists():
        wf = json.loads(walk_forward_path.read_text())
        wf_auc = wf.get("auc_mean")
        wf_std = wf.get("auc_std")
        if wf_auc is not None:
            print()
            print(f"Walk-forward (research) AUC mean: {wf_auc:.4f} ± "
                  f"{wf_std:.4f}" if wf_std else f"{wf_auc:.4f}")
            print(f"Single-split (this run)  AUC:     {tm['auc_roc']:.4f}")
            print("  (NOTE: these use different splits — research uses 5-seed expanding "
                  "folds, this uses a single 70/15/15 split. Compare directionally.)")

    print()
    print("Promotion: NOT auto-promoted. Gate-check next:")
    print(f"  - Inspect rec count in /api/recommendations after a one-shot run with v7")
    print(f"  - Compare top-K short-rec quality vs v3 sigmoid")
    print(f"  - If green, manually: cp {out_path} {out_path.parent / 'directional_xgb_v1.pkl'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
