"""Automated model retraining with champion/challenger comparison."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from src.models.directional import (
    DirectionalModel,
    DEFAULT_MODEL_PATH as DIR_MODEL_PATH,
    DEFAULT_RISE_MODEL_PATH as RISE_MODEL_PATH,
    _resolve_model_dir,
)
from src.models.volatility import VolatilityModel, DEFAULT_MODEL_PATH as VOL_MODEL_PATH

logger = logging.getLogger(__name__)

MODEL_DIR = _resolve_model_dir()
REGISTRY_PATH = Path(__file__).parent.parent.parent.parent / "_memory" / "MODEL_REGISTRY.md"


class Retrainer:
    """Retrain ML models, compare against current champion, deploy if improved."""

    def __init__(self, improvement_threshold: float = 0.01, brier_tolerance: float = 0.005):
        self.improvement_threshold = improvement_threshold
        # A challenger may beat the champion on AUC while regressing calibration;
        # ranker floors and lift gates consume calibrated probabilities, so a
        # worse brier score is a production regression even at higher AUC.
        self.brier_tolerance = brier_tolerance

    def retrain_directional(self, tickers: list[str] | None = None, direction: str = "drop") -> dict:
        """Retrain a directional XGBoost model (drop or rise side).

        Gate: primary metric is the walk-forward mean AUC across train() folds
        (the project-standard evaluation — single-slice test AUC ran 0.04-0.06
        optimistic in the 2026-05-15 vol-K sweep). Champions promoted before
        this gate existed only carry single-slice `auc_roc`; those compare on
        `auc_roc` once more, and the first deployment writes walk-forward
        fields so subsequent retrains use the standard gate. A challenger must
        also not regress brier by more than brier_tolerance, and an
        uncalibrated challenger never deploys.

        Returns dict with old/new metrics and whether the new model was deployed.
        """
        if direction not in ("drop", "rise"):
            raise ValueError(f"direction must be 'drop' or 'rise', got {direction!r}")
        model_name = "directional" if direction == "drop" else "directional_rise"
        champion_path = DIR_MODEL_PATH if direction == "drop" else RISE_MODEL_PATH

        logger.info(f"Retraining {model_name} model...")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Load champion metrics (if available)
        champion_metrics = self._load_champion_metrics(model_name)

        # Train challenger. Calibration pinned to sigmoid: the constructor
        # auto-picks isotonic on large calibration sets, which emits discrete
        # probability plateaus that break downstream gates (2026-05-12).
        challenger_path = MODEL_DIR / f"{model_name}_xgb_challenger_{timestamp}.pkl"
        challenger = DirectionalModel(
            model_path=challenger_path, direction=direction, calibration_method="sigmoid",
        )

        try:
            results = challenger.train(tickers=tickers, n_folds=3)
        except ValueError as e:
            logger.error(f"Retraining failed: {e}")
            return {"status": "failed", "model": model_name, "error": str(e)}

        fold_aucs = [
            f["auc_roc"] for f in results.get("fold_metrics", [])
            if f.get("auc_roc")
        ]
        wf_mean = sum(fold_aucs) / len(fold_aucs) if fold_aucs else None
        wf_std = (
            (sum((a - wf_mean) ** 2 for a in fold_aucs) / len(fold_aucs)) ** 0.5
            if fold_aucs else None
        )
        new_metrics = {
            **results["test_metrics"],
            "walk_forward_auc_mean": wf_mean,
            "walk_forward_auc_std": wf_std,
            "walk_forward_folds": len(fold_aucs),
            "direction": direction,
        }

        def _finish(status: str, deployed: bool, primary_metric: str,
                    old_score, new_score) -> dict:
            if challenger_path.exists():
                challenger_path.unlink()
            return {
                "status": status,
                "model": model_name,
                "old_metrics": champion_metrics,
                "new_metrics": new_metrics,
                "primary_metric": primary_metric,
                "old_score": old_score,
                "new_score": new_score,
                "deployed": deployed,
                "timestamp": timestamp,
            }

        # Fail closed when the champion's metrics are unknown. The 2026-07-05
        # run auto-deployed against "auc_roc 0.0000" because no metrics file
        # existed (manual promotions never wrote one) — an unmeasured champion
        # is not a beaten champion. Seed {model}_metrics.json deliberately to
        # enable gated retraining.
        if champion_metrics is None:
            logger.warning(
                f"No champion metrics for {model_name} model — keeping current "
                f"model (fail-closed). Seed {model_name}_metrics.json to enable "
                f"gated retraining."
            )
            return _finish("kept_current (no champion metrics)", False, "walk_forward_auc_mean", None,
                           wf_mean)

        # Pick the comparison metric: walk-forward mean when both sides have
        # it, otherwise fall back to the champion's legacy single-slice AUC.
        old_wf = champion_metrics.get("walk_forward_auc_mean")
        if old_wf is not None and wf_mean is not None:
            primary_metric = "walk_forward_auc_mean"
            old_score, new_score = old_wf, wf_mean
        else:
            primary_metric = "auc_roc"
            old_score = champion_metrics.get("auc_roc", 0)
            new_score = new_metrics.get("auc_roc", 0)
            logger.warning(
                f"{model_name}: champion has no walk-forward metrics — comparing "
                f"on single-slice auc_roc this cycle; walk-forward fields are "
                f"written on deployment."
            )

        if new_score <= old_score + self.improvement_threshold:
            logger.info(
                f"Keeping current {model_name} model: new {primary_metric}={new_score:.4f} "
                f"vs current {old_score:.4f} (threshold={self.improvement_threshold})"
            )
            return _finish("kept_current", False, primary_metric, old_score, new_score)

        # Brier guard. None means the challenger served raw probabilities
        # (calibration skipped/failed) — never deploy that into a pipeline
        # whose gates assume calibrated probs.
        new_brier = new_metrics.get("brier_score")
        old_brier = champion_metrics.get("brier_score")
        if new_brier is None:
            logger.warning(
                f"{model_name}: challenger is uncalibrated (no brier score) — keeping current model."
            )
            return _finish("kept_current (challenger uncalibrated)", False, primary_metric,
                           old_score, new_score)
        if old_brier is not None and new_brier > old_brier + self.brier_tolerance:
            logger.warning(
                f"{model_name}: challenger wins {primary_metric} ({old_score:.4f} -> "
                f"{new_score:.4f}) but regresses brier ({old_brier:.4f} -> {new_brier:.4f}, "
                f"tolerance {self.brier_tolerance}) — keeping current model."
            )
            return _finish("kept_current (brier regression)", False, primary_metric,
                           old_score, new_score)

        logger.info(
            f"Deploying new {model_name} model: {primary_metric} "
            f"{old_score:.4f} -> {new_score:.4f} (brier {old_brier} -> {new_brier:.4f})"
        )
        # Backup current champion
        if champion_path.exists():
            backup_path = MODEL_DIR / f"{model_name}_xgb_prev_{timestamp}.pkl"
            shutil.copy2(champion_path, backup_path)

        shutil.copy2(challenger_path, champion_path)
        self._save_champion_metrics(model_name, new_metrics, timestamp)
        return _finish("deployed", True, primary_metric, old_score, new_score)

    def retrain_rise(self, tickers: list[str] | None = None) -> dict:
        """Retrain the rise-side directional model (same gate as drop)."""
        return self.retrain_directional(tickers, direction="rise")

    def retrain_volatility(self, tickers: list[str] | None = None) -> dict:
        """Retrain the volatility LSTM model.

        Returns dict with old/new metrics and whether the new model was deployed.
        """
        logger.info("Retraining volatility model...")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        champion_metrics = self._load_champion_metrics("volatility")

        challenger_path = MODEL_DIR / f"volatility_lstm_challenger_{timestamp}.pt"
        challenger = VolatilityModel(model_path=challenger_path)

        try:
            results = challenger.train(tickers=tickers, epochs=50)
        except ValueError as e:
            logger.error(f"Retraining failed: {e}")
            return {"status": "failed", "error": str(e)}

        new_metrics = {
            "test_mae": results["test_mae"],
            "test_rmse": results["test_rmse"],
            "test_correlation": results["test_correlation"],
        }
        deployed = False

        # Compare: lower MAE is better
        primary_metric = "test_mae"
        new_score = new_metrics.get(primary_metric, float("inf"))
        old_score = champion_metrics.get(primary_metric, float("inf")) if champion_metrics else float("inf")

        # Fail closed on unknown champion — see retrain_directional. The 7/5
        # run deployed against "test_mae inf" for the same reason.
        if champion_metrics is None:
            logger.warning(
                "No champion metrics for volatility model — keeping current "
                "model (fail-closed). Seed volatility_metrics.json to enable "
                "gated retraining."
            )
            if challenger_path.exists():
                challenger_path.unlink()
            return {
                "status": "kept_current (no champion metrics)",
                "model": "volatility",
                "old_metrics": None,
                "new_metrics": new_metrics,
                "primary_metric": primary_metric,
                "old_score": None,
                "new_score": new_score,
                "deployed": False,
                "timestamp": timestamp,
            }

        # For MAE, lower is better — deploy if new < old - threshold
        if new_score < old_score - self.improvement_threshold:
            logger.info(
                f"Deploying new volatility model: {primary_metric} "
                f"{old_score:.4f} -> {new_score:.4f}"
            )
            if VOL_MODEL_PATH.exists():
                backup_path = MODEL_DIR / f"volatility_lstm_prev_{timestamp}.pt"
                shutil.copy2(VOL_MODEL_PATH, backup_path)

            shutil.copy2(challenger_path, VOL_MODEL_PATH)
            deployed = True
            self._save_champion_metrics("volatility", new_metrics, timestamp)
        else:
            logger.info(
                f"Keeping current volatility model: new {primary_metric}={new_score:.4f} "
                f"vs current {old_score:.4f}"
            )

        if challenger_path.exists():
            challenger_path.unlink()

        return {
            "status": "deployed" if deployed else "kept_current",
            "model": "volatility",
            "old_metrics": champion_metrics,
            "new_metrics": new_metrics,
            "primary_metric": primary_metric,
            "old_score": old_score,
            "new_score": new_score,
            "deployed": deployed,
            "timestamp": timestamp,
        }

    def retrain_all(self, tickers: list[str] | None = None) -> dict:
        """Retrain all models (drop + rise directional, volatility)."""
        return {
            "directional": self.retrain_directional(tickers),
            "directional_rise": self.retrain_rise(tickers),
            "volatility": self.retrain_volatility(tickers),
        }

    def _load_champion_metrics(self, model_name: str) -> dict | None:
        """Load the current champion's metrics from the registry file."""
        metrics_path = MODEL_DIR / f"{model_name}_metrics.json"
        if metrics_path.exists():
            with open(metrics_path) as f:
                return json.load(f)
        return None

    def _save_champion_metrics(self, model_name: str, metrics: dict, timestamp: str):
        """Save the new champion's metrics."""
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        metrics_path = MODEL_DIR / f"{model_name}_metrics.json"
        data = {**metrics, "deployed_at": timestamp}
        with open(metrics_path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved {model_name} metrics to {metrics_path}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    retrainer = Retrainer()
    results = retrainer.retrain_all()

    for model_name, result in results.items():
        print(f"\n{model_name}: {result['status']}")
        if result.get("deployed"):
            print(f"  {result['primary_metric']}: {result['old_score']:.4f} -> {result['new_score']:.4f}")
