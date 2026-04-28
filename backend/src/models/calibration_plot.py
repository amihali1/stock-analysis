"""Reliability diagram + Brier score helpers (P9-006).

Renders a reliability curve next to a model artifact so calibration regressions
are visible at a glance after each retrain.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from sklearn.calibration import calibration_curve
from sklearn.metrics import brier_score_loss

logger = logging.getLogger(__name__)


def reliability_data(y_true, y_prob, n_bins: int = 10) -> dict:
    """Compute reliability-diagram bins + Brier score without plotting."""
    if len(set(y_true)) < 2:
        return {"prob_pred": [], "prob_true": [], "brier": None, "bins": n_bins}
    prob_true, prob_pred = calibration_curve(y_true, y_prob, n_bins=n_bins, strategy="quantile")
    brier = brier_score_loss(y_true, y_prob)
    return {
        "prob_pred": [float(x) for x in prob_pred],
        "prob_true": [float(x) for x in prob_true],
        "brier": float(brier),
        "bins": n_bins,
    }


def save_reliability_plot(y_true, y_prob, out_path: Path, n_bins: int = 10) -> dict:
    """Save reliability diagram + return summary stats. Headless matplotlib."""
    data = reliability_data(y_true, y_prob, n_bins=n_bins)
    if not data["prob_pred"]:
        logger.warning("Skipping reliability plot — only one class present in y_true")
        return data

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not installed — skipping reliability plot, returning stats only")
        return data

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot([0, 1], [0, 1], "--", color="gray", label="perfect")
    ax.plot(data["prob_pred"], data["prob_true"], "o-", label=f"model (Brier={data['brier']:.4f})")
    ax.set_xlabel("Predicted probability")
    ax.set_ylabel("Empirical frequency")
    ax.set_title("Reliability diagram")
    ax.legend(loc="lower right")
    ax.grid(True, alpha=0.3)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    logger.info("Saved reliability plot to %s", out_path)
    return data
