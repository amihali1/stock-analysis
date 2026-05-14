"""Train rise v2 with the excess-vs-SPY label and persist to trained_models/directional_xgb_rise_v2.pkl.

Run inside the prod backend container so it shares Postgres + feature attachers
with live. Writes both the pkl (via DirectionalModel.save()) and a sibling
.metrics.json next to it.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from src.models.directional import (
    LABEL_MODE_EXCESS,
    MODEL_DIR,
    DirectionalModel,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("retrain-rise-v2")


def main() -> int:
    model_path = MODEL_DIR / "directional_xgb_rise_v2.pkl"
    metrics_path = model_path.with_suffix(".metrics.json")

    model = DirectionalModel(
        model_path=model_path,
        direction="rise",
        label_mode=LABEL_MODE_EXCESS,
        calibration_method="sigmoid",
    )
    metrics = model.train(n_folds=3)

    payload = {
        "model_version": "rise_v2",
        "label_mode": LABEL_MODE_EXCESS,
        **metrics,
    }
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    logger.info("Wrote metrics to %s", metrics_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
