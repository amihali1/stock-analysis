"""Train the bullish-side directional model (rise_5d) — Phase 1 of bullish build.

Mirrors `directional_xgb_v3` but flips the label to "forward return > +3% in 5
trading days." Sigmoid calibration from the start (skipping the isotonic detour
that produced plateau-clustered outputs on the drop model — see
`directional_calibration_plateaus_2026-05-12.md`).

Output: `trained_models/directional_xgb_rise_v1.pkl` (plus metrics JSON).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from src.models.directional import DirectionalModel, MODEL_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("train_rise")


def main() -> None:
    out_path = MODEL_DIR / "directional_xgb_rise_v1.pkl"
    model = DirectionalModel(
        model_path=out_path,
        direction="rise",
        calibration_method="sigmoid",
    )

    metrics = model.train()

    metrics_path = out_path.with_suffix("").with_name(out_path.stem + ".metrics.json")
    metrics_path.write_text(json.dumps(metrics, indent=2, default=str))
    logger.info("Wrote metrics to %s", metrics_path)

    test = metrics["test_metrics"]
    logger.info(
        "DONE rise v1: auc=%.4f brier=%.4f positive_rate=%.4f dataset_size=%d",
        test["auc_roc"],
        test["brier_score"] or 0.0,
        metrics["positive_rate"],
        metrics["dataset_size"],
    )


if __name__ == "__main__":
    main()
