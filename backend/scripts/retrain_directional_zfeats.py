"""Retrain rise v3 and drop v2 with per-ticker z-score features (2026-05-15).

HISTORICAL / RESEARCH-ONLY. The 10-seed sweep on rise v3 (see
`zfeats_retrain_negative_2026-05-15` memo) showed z-features regress
production walk-forward AUC by ~1.2pp vs rise v2. The z-features were
therefore removed from the production `FEATURE_COLS` list. This script
opts back into them explicitly via `DirectionalModel(feature_cols=...)`
so the experiment remains reproducible.

Trains:
  * rise v3 → directional_xgb_rise_v3.pkl (label_mode=excess, sigmoid calib)
  * drop v2 → directional_xgb_v2.pkl       (label_mode=absolute, sigmoid calib)

Both use the expanded 67-feature set (58 absolute + 9 per-ticker
rolling 120d z-scores). Writes JSON metrics next to each pkl.

Usage:
    python scripts/retrain_directional_zfeats.py
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.models.directional import (
    DirectionalModel,
    FEATURE_COLS,
    LABEL_MODE_ABSOLUTE,
    LABEL_MODE_EXCESS,
    PER_TICKER_RANK_FEATURE_COLS,
    _resolve_model_dir,
)

RESEARCH_FEATURE_COLS = FEATURE_COLS + PER_TICKER_RANK_FEATURE_COLS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("retrain_zfeats")


def main() -> int:
    model_dir = _resolve_model_dir()
    model_dir.mkdir(parents=True, exist_ok=True)
    logger.info("MODEL_DIR=%s", model_dir)

    outputs: dict[str, dict] = {}

    for name, direction, label_mode, pkl_name in [
        ("rise_v3", "rise", LABEL_MODE_EXCESS, "directional_xgb_rise_v3.pkl"),
        ("drop_v2", "drop", LABEL_MODE_ABSOLUTE, "directional_xgb_v2.pkl"),
    ]:
        pkl_path = model_dir / pkl_name
        logger.info("=== Training %s (direction=%s, label_mode=%s) → %s ===",
                    name, direction, label_mode, pkl_path)
        model = DirectionalModel(
            model_path=pkl_path,
            direction=direction,
            label_mode=label_mode,
            calibration_method="sigmoid",
            feature_cols=RESEARCH_FEATURE_COLS,
        )
        metrics = model.train(n_folds=3)
        outputs[name] = metrics

        # Write metrics JSON
        metrics_path = pkl_path.with_suffix(".metrics.json")
        metrics_path.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
        logger.info("Wrote %s", metrics_path)

    print("\n=== Summary ===")
    for name, m in outputs.items():
        test = m.get("test_metrics", {})
        print(f"{name}:")
        print(f"  test AUC : {test.get('auc_roc', float('nan')):.4f}")
        print(f"  test Brier: {m.get('brier_score', float('nan')):.4f}")
        print(f"  test acc : {test.get('accuracy', float('nan')):.4f}")
        print(f"  test prec: {test.get('precision', float('nan')):.4f}")
        print(f"  test rec : {test.get('recall', float('nan')):.4f}")
        print(f"  features : {m.get('n_features', '?')}")
        print(f"  rows     : {m.get('total_rows', '?')}")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
