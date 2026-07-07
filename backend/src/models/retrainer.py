"""Automated model retraining with champion/challenger comparison."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from src.models.directional import DirectionalModel, DEFAULT_MODEL_PATH as DIR_MODEL_PATH, _resolve_model_dir
from src.models.volatility import VolatilityModel, DEFAULT_MODEL_PATH as VOL_MODEL_PATH

logger = logging.getLogger(__name__)

MODEL_DIR = _resolve_model_dir()
REGISTRY_PATH = Path(__file__).parent.parent.parent.parent / "_memory" / "MODEL_REGISTRY.md"


class Retrainer:
    """Retrain ML models, compare against current champion, deploy if improved."""

    def __init__(self, improvement_threshold: float = 0.01):
        self.improvement_threshold = improvement_threshold

    def retrain_directional(self, tickers: list[str] | None = None) -> dict:
        """Retrain the directional XGBoost model.

        Returns dict with old/new metrics and whether the new model was deployed.
        """
        logger.info("Retraining directional model...")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Load champion metrics (if available)
        champion_metrics = self._load_champion_metrics("directional")

        # Train challenger
        challenger_path = MODEL_DIR / f"directional_xgb_challenger_{timestamp}.pkl"
        challenger = DirectionalModel(model_path=challenger_path)

        try:
            results = challenger.train(tickers=tickers, n_folds=3)
        except ValueError as e:
            logger.error(f"Retraining failed: {e}")
            return {"status": "failed", "error": str(e)}

        new_metrics = results["test_metrics"]
        deployed = False

        # Compare: use AUC-ROC as primary metric
        primary_metric = "auc_roc"
        new_score = new_metrics.get(primary_metric, 0)
        old_score = champion_metrics.get(primary_metric, 0) if champion_metrics else 0

        # Fail closed when the champion's metrics are unknown. The 2026-07-05
        # run auto-deployed against "auc_roc 0.0000" because no metrics file
        # existed (manual promotions never wrote one) — an unmeasured champion
        # is not a beaten champion. Seed {model}_metrics.json deliberately to
        # enable gated retraining.
        if champion_metrics is None:
            logger.warning(
                "No champion metrics for directional model — keeping current "
                "model (fail-closed). Seed directional_metrics.json to enable "
                "gated retraining."
            )
            if challenger_path.exists():
                challenger_path.unlink()
            return {
                "status": "kept_current (no champion metrics)",
                "model": "directional",
                "old_metrics": None,
                "new_metrics": new_metrics,
                "primary_metric": primary_metric,
                "old_score": None,
                "new_score": new_score,
                "deployed": False,
                "timestamp": timestamp,
            }

        if new_score > old_score + self.improvement_threshold:
            # Deploy new model
            logger.info(
                f"Deploying new directional model: {primary_metric} "
                f"{old_score:.4f} -> {new_score:.4f}"
            )
            # Backup current champion
            if DIR_MODEL_PATH.exists():
                backup_path = MODEL_DIR / f"directional_xgb_prev_{timestamp}.pkl"
                shutil.copy2(DIR_MODEL_PATH, backup_path)

            shutil.copy2(challenger_path, DIR_MODEL_PATH)
            deployed = True
            self._save_champion_metrics("directional", new_metrics, timestamp)
        else:
            logger.info(
                f"Keeping current directional model: new {primary_metric}={new_score:.4f} "
                f"vs current {old_score:.4f} (threshold={self.improvement_threshold})"
            )

        # Clean up challenger file
        if challenger_path.exists():
            challenger_path.unlink()

        return {
            "status": "deployed" if deployed else "kept_current",
            "model": "directional",
            "old_metrics": champion_metrics,
            "new_metrics": new_metrics,
            "primary_metric": primary_metric,
            "old_score": old_score,
            "new_score": new_score,
            "deployed": deployed,
            "timestamp": timestamp,
        }

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
        """Retrain both models."""
        return {
            "directional": self.retrain_directional(tickers),
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
