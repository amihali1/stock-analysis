"""Retrainer champion/challenger gate.

Regression for 2026-07-05: the first scheduled retrain auto-deployed both
models because no champion metrics file existed — the gate compared the
challenger against auc_roc 0.0 / test_mae inf and trivially passed. An
unmeasured champion must fail CLOSED (keep current), not open.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import src.models.retrainer as retrainer_module
from src.models.retrainer import Retrainer


@pytest.fixture()
def model_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(retrainer_module, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(retrainer_module, "DIR_MODEL_PATH", tmp_path / "directional_xgb_v1.pkl")
    monkeypatch.setattr(retrainer_module, "VOL_MODEL_PATH", tmp_path / "volatility_lstm_v1.pt")
    return tmp_path


def _mock_directional(auc: float):
    """Patch DirectionalModel so train() returns a challenger with given AUC
    and writes a fake pickle to its model_path."""
    def _factory(model_path):
        m = MagicMock()
        def _train(**kw):
            model_path.write_bytes(b"challenger")
            return {"test_metrics": {"auc_roc": auc, "brier_score": 0.04}}
        m.train = _train
        return m
    return patch.object(retrainer_module, "DirectionalModel", side_effect=_factory)


def _mock_volatility(mae: float):
    def _factory(model_path):
        m = MagicMock()
        def _train(**kw):
            model_path.write_bytes(b"challenger")
            return {"test_mae": mae, "test_rmse": mae * 1.3, "test_correlation": 0.5}
        m.train = _train
        return m
    return patch.object(retrainer_module, "VolatilityModel", side_effect=_factory)


def _seed_champion(model_dir, name: str, metrics: dict, model_file: str):
    (model_dir / f"{name}_metrics.json").write_text(json.dumps(metrics))
    (model_dir / model_file).write_bytes(b"champion")


class TestDirectionalGate:
    def test_no_champion_metrics_fails_closed(self, model_dir):
        (model_dir / "directional_xgb_v1.pkl").write_bytes(b"champion")

        with _mock_directional(auc=0.99):
            result = Retrainer().retrain_directional()

        assert result["deployed"] is False
        assert "no champion metrics" in result["status"]
        # Serving model untouched, challenger cleaned up, no metrics seeded
        assert (model_dir / "directional_xgb_v1.pkl").read_bytes() == b"champion"
        assert not (model_dir / "directional_metrics.json").exists()
        assert not list(model_dir.glob("directional_xgb_challenger_*.pkl"))

    def test_better_challenger_deploys(self, model_dir):
        _seed_champion(model_dir, "directional", {"auc_roc": 0.60}, "directional_xgb_v1.pkl")

        with _mock_directional(auc=0.65):
            result = Retrainer(improvement_threshold=0.01).retrain_directional()

        assert result["deployed"] is True
        assert (model_dir / "directional_xgb_v1.pkl").read_bytes() == b"challenger"
        saved = json.loads((model_dir / "directional_metrics.json").read_text())
        assert saved["auc_roc"] == 0.65
        # Old champion backed up
        assert list(model_dir.glob("directional_xgb_prev_*.pkl"))

    def test_worse_challenger_kept_out(self, model_dir):
        _seed_champion(model_dir, "directional", {"auc_roc": 0.60}, "directional_xgb_v1.pkl")

        with _mock_directional(auc=0.58):
            result = Retrainer(improvement_threshold=0.01).retrain_directional()

        assert result["deployed"] is False
        assert (model_dir / "directional_xgb_v1.pkl").read_bytes() == b"champion"
        assert json.loads((model_dir / "directional_metrics.json").read_text())["auc_roc"] == 0.60

    def test_marginal_improvement_below_threshold_kept_out(self, model_dir):
        _seed_champion(model_dir, "directional", {"auc_roc": 0.60}, "directional_xgb_v1.pkl")

        with _mock_directional(auc=0.605):
            result = Retrainer(improvement_threshold=0.01).retrain_directional()

        assert result["deployed"] is False


class TestVolatilityGate:
    def test_no_champion_metrics_fails_closed(self, model_dir):
        (model_dir / "volatility_lstm_v1.pt").write_bytes(b"champion")

        with _mock_volatility(mae=0.01):
            result = Retrainer().retrain_volatility()

        assert result["deployed"] is False
        assert "no champion metrics" in result["status"]
        assert (model_dir / "volatility_lstm_v1.pt").read_bytes() == b"champion"
        assert not (model_dir / "volatility_metrics.json").exists()

    def test_lower_mae_deploys(self, model_dir):
        _seed_champion(model_dir, "volatility", {"test_mae": 0.20}, "volatility_lstm_v1.pt")

        with _mock_volatility(mae=0.15):
            result = Retrainer(improvement_threshold=0.01).retrain_volatility()

        assert result["deployed"] is True
        assert (model_dir / "volatility_lstm_v1.pt").read_bytes() == b"challenger"

    def test_higher_mae_kept_out(self, model_dir):
        _seed_champion(model_dir, "volatility", {"test_mae": 0.20}, "volatility_lstm_v1.pt")

        with _mock_volatility(mae=0.25):
            result = Retrainer(improvement_threshold=0.01).retrain_volatility()

        assert result["deployed"] is False
        assert (model_dir / "volatility_lstm_v1.pt").read_bytes() == b"champion"
