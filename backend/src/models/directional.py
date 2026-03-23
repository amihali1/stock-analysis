"""XGBoost directional classifier: predicts stocks that will drop >3% in 5 trading days."""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score

from src.db.models import PriceHistory, TechnicalIndicator
from src.db.session import SessionLocal

logger = logging.getLogger(__name__)

FEATURE_COLS = [
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_histogram",
    "bb_percent_b",
    "bb_upper",
    "bb_lower",
    "sma_50",
    "sma_200",
    "sma_crossover",
    "volume_zscore",
    # Derived features added in build_dataset
    "return_5d_lag",
    "return_10d_lag",
    "return_20d_lag",
    "close_to_sma50_ratio",
    "close_to_sma200_ratio",
    "volatility_20d",
]

MODEL_DIR = Path(__file__).parent.parent.parent / "trained_models"
DEFAULT_MODEL_PATH = MODEL_DIR / "directional_xgb_v1.pkl"

# Target: stock drops >3% in the next 5 trading days
DROP_THRESHOLD = -0.03
FORWARD_DAYS = 5


class DirectionalModel:
    def __init__(self, model_path: Path | None = None):
        self.model: xgb.XGBClassifier | None = None
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self.feature_cols = FEATURE_COLS

    def load(self) -> None:
        """Load a trained model from disk."""
        with open(self.model_path, "rb") as f:
            data = pickle.load(f)
        self.model = data["model"]
        self.feature_cols = data.get("feature_cols", FEATURE_COLS)
        logger.info(f"Loaded directional model from {self.model_path}")

    def save(self) -> None:
        """Save trained model to disk."""
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump({"model": self.model, "feature_cols": self.feature_cols}, f)
        logger.info(f"Saved directional model to {self.model_path}")

    def predict(self, features: dict) -> tuple[float, float]:
        """Predict drop probability for a single sample.

        Returns (probability_of_drop, confidence) where confidence
        is how far the probability is from 0.5.
        """
        if self.model is None:
            self.load()

        df = pd.DataFrame([features])
        # Ensure columns match training order
        for col in self.feature_cols:
            if col not in df.columns:
                df[col] = 0.0
        df = df[self.feature_cols]

        prob = self.model.predict_proba(df)[0, 1]  # Probability of class 1 (drop)
        confidence = abs(prob - 0.5) * 2  # 0.0 = no confidence, 1.0 = max confidence
        return float(prob), float(confidence)

    def predict_batch(self, df: pd.DataFrame) -> pd.DataFrame:
        """Predict for a batch of samples. Returns df with prob and confidence columns."""
        if self.model is None:
            self.load()

        X = df[self.feature_cols].copy()
        probs = self.model.predict_proba(X)[:, 1]
        result = df.copy()
        result["drop_probability"] = probs
        result["confidence"] = np.abs(probs - 0.5) * 2
        return result

    def train(self, tickers: list[str] | None = None, n_folds: int = 3) -> dict:
        """Train the model with walk-forward validation.

        Returns metrics dict.
        """
        logger.info("Building dataset...")
        df = build_dataset(tickers)

        if len(df) < 500:
            raise ValueError(f"Not enough data to train: {len(df)} rows (need 500+)")

        logger.info(f"Dataset: {len(df)} rows, {df['label'].sum()} positive ({df['label'].mean():.1%})")

        # Walk-forward validation
        fold_metrics = []
        dates = sorted(df["date"].unique())
        fold_size = len(dates) // (n_folds + 1)

        for fold in range(n_folds):
            train_end_idx = (fold + 1) * fold_size
            val_end_idx = train_end_idx + fold_size

            train_dates = dates[:train_end_idx]
            val_dates = dates[train_end_idx:val_end_idx]

            train_mask = df["date"].isin(train_dates)
            val_mask = df["date"].isin(val_dates)

            X_train = df.loc[train_mask, self.feature_cols]
            y_train = df.loc[train_mask, "label"]
            X_val = df.loc[val_mask, self.feature_cols]
            y_val = df.loc[val_mask, "label"]

            if len(X_val) == 0 or len(X_train) == 0:
                continue

            model = xgb.XGBClassifier(
                n_estimators=200,
                max_depth=5,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                scale_pos_weight=len(y_train[y_train == 0]) / max(len(y_train[y_train == 1]), 1),
                eval_metric="logloss",
                random_state=42,
                n_jobs=-1,
            )
            model.fit(
                X_train, y_train,
                eval_set=[(X_val, y_val)],
                verbose=False,
            )

            y_pred = model.predict(X_val)
            y_prob = model.predict_proba(X_val)[:, 1]

            metrics = {
                "fold": fold + 1,
                "train_size": len(X_train),
                "val_size": len(X_val),
                "accuracy": accuracy_score(y_val, y_pred),
                "precision": precision_score(y_val, y_pred, zero_division=0),
                "recall": recall_score(y_val, y_pred, zero_division=0),
                "f1": f1_score(y_val, y_pred, zero_division=0),
                "auc_roc": roc_auc_score(y_val, y_prob) if len(set(y_val)) > 1 else 0.0,
            }
            fold_metrics.append(metrics)
            logger.info(
                f"Fold {fold + 1}: acc={metrics['accuracy']:.3f} prec={metrics['precision']:.3f} "
                f"rec={metrics['recall']:.3f} f1={metrics['f1']:.3f} auc={metrics['auc_roc']:.3f}"
            )

        # Final model: train on all data except last fold_size for test
        test_dates = dates[-fold_size:]
        train_mask = ~df["date"].isin(test_dates)
        test_mask = df["date"].isin(test_dates)

        X_train = df.loc[train_mask, self.feature_cols]
        y_train = df.loc[train_mask, "label"]
        X_test = df.loc[test_mask, self.feature_cols]
        y_test = df.loc[test_mask, "label"]

        self.model = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            scale_pos_weight=len(y_train[y_train == 0]) / max(len(y_train[y_train == 1]), 1),
            eval_metric="logloss",
            random_state=42,
            n_jobs=-1,
        )
        self.model.fit(X_train, y_train, verbose=False)

        y_pred = self.model.predict(X_test)
        y_prob = self.model.predict_proba(X_test)[:, 1]

        test_metrics = {
            "accuracy": accuracy_score(y_test, y_pred),
            "precision": precision_score(y_test, y_pred, zero_division=0),
            "recall": recall_score(y_test, y_pred, zero_division=0),
            "f1": f1_score(y_test, y_pred, zero_division=0),
            "auc_roc": roc_auc_score(y_test, y_prob) if len(set(y_test)) > 1 else 0.0,
        }
        logger.info(
            f"Test: acc={test_metrics['accuracy']:.3f} prec={test_metrics['precision']:.3f} "
            f"rec={test_metrics['recall']:.3f} f1={test_metrics['f1']:.3f} auc={test_metrics['auc_roc']:.3f}"
        )

        # Feature importance
        importance = dict(zip(self.feature_cols, self.model.feature_importances_))
        importance = dict(sorted(importance.items(), key=lambda x: x[1], reverse=True))

        self.save()

        return {
            "fold_metrics": fold_metrics,
            "test_metrics": test_metrics,
            "feature_importance": importance,
            "dataset_size": len(df),
            "positive_rate": float(df["label"].mean()),
        }

    def get_feature_importance(self) -> dict[str, float]:
        """Return feature importance from the trained model."""
        if self.model is None:
            self.load()
        importance = dict(zip(self.feature_cols, self.model.feature_importances_))
        return dict(sorted(importance.items(), key=lambda x: x[1], reverse=True))


def build_dataset(tickers: list[str] | None = None) -> pd.DataFrame:
    """Build training dataset by joining price_history + technical_indicators.

    Creates binary label: 1 if stock drops >3% in the next 5 trading days.
    """
    db = SessionLocal()
    try:
        query = db.query(
            TechnicalIndicator.ticker,
            TechnicalIndicator.date,
            TechnicalIndicator.rsi_14,
            TechnicalIndicator.macd,
            TechnicalIndicator.macd_signal,
            TechnicalIndicator.macd_histogram,
            TechnicalIndicator.bb_percent_b,
            TechnicalIndicator.bb_upper,
            TechnicalIndicator.bb_lower,
            TechnicalIndicator.sma_50,
            TechnicalIndicator.sma_200,
            TechnicalIndicator.sma_crossover,
            TechnicalIndicator.volume_zscore,
            PriceHistory.close,
        ).join(
            PriceHistory,
            (TechnicalIndicator.ticker == PriceHistory.ticker)
            & (TechnicalIndicator.date == PriceHistory.date),
        )

        if tickers:
            query = query.filter(TechnicalIndicator.ticker.in_(tickers))

        rows = query.all()
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=[
            "ticker", "date", "rsi_14", "macd", "macd_signal", "macd_histogram",
            "bb_percent_b", "bb_upper", "bb_lower", "sma_50", "sma_200",
            "sma_crossover", "volume_zscore", "close",
        ])
    finally:
        db.close()

    # Sort by ticker and date for proper time-series operations
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)

    # Add derived features per ticker
    all_ticker_dfs = []
    for ticker, group in df.groupby("ticker"):
        g = group.copy()

        # Lagged returns
        g["return_5d_lag"] = g["close"].pct_change(5)
        g["return_10d_lag"] = g["close"].pct_change(10)
        g["return_20d_lag"] = g["close"].pct_change(20)

        # Price relative to SMAs
        g["close_to_sma50_ratio"] = g["close"] / g["sma_50"].replace(0, np.nan)
        g["close_to_sma200_ratio"] = g["close"] / g["sma_200"].replace(0, np.nan)

        # 20-day realized volatility
        g["volatility_20d"] = g["close"].pct_change().rolling(20).std() * np.sqrt(252)

        # Forward return for label (will drop >3%?)
        g["forward_return"] = g["close"].shift(-FORWARD_DAYS) / g["close"] - 1
        g["label"] = (g["forward_return"] < DROP_THRESHOLD).astype(int)

        all_ticker_dfs.append(g)

    df = pd.concat(all_ticker_dfs, ignore_index=True)

    # Drop rows with NaN features or missing labels
    feature_cols_with_label = FEATURE_COLS + ["label"]
    df = df.dropna(subset=feature_cols_with_label)

    # Drop the helper columns but keep date and ticker for splitting
    keep_cols = ["ticker", "date"] + FEATURE_COLS + ["label"]
    df = df[keep_cols]

    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from src.db.models import Base
    from src.db.session import engine
    from src.pipeline.data_fetcher import DataFetcher
    from src.pipeline.feature_eng import FeatureEngineer

    Base.metadata.create_all(engine)

    # Ensure we have data
    from src.db.watchlist import get_watchlist_tickers
    db = SessionLocal()
    tickers = get_watchlist_tickers(db)
    db.close()

    logger.info(f"Fetching data for {len(tickers)} tickers...")
    fetcher = DataFetcher()
    fetcher.fetch_daily(tickers=tickers, period="2y")
    fetcher.close()

    logger.info("Computing features...")
    eng = FeatureEngineer()
    eng.compute_all(tickers=tickers)
    eng.close()

    # Train model
    model = DirectionalModel()
    results = model.train(tickers=tickers)

    print(f"\nDataset: {results['dataset_size']} rows, {results['positive_rate']:.1%} positive rate")
    print(f"\nWalk-forward validation ({len(results['fold_metrics'])} folds):")
    for m in results["fold_metrics"]:
        print(f"  Fold {m['fold']}: acc={m['accuracy']:.3f} prec={m['precision']:.3f} rec={m['recall']:.3f} auc={m['auc_roc']:.3f}")

    t = results["test_metrics"]
    print(f"\nTest: acc={t['accuracy']:.3f} prec={t['precision']:.3f} rec={t['recall']:.3f} f1={t['f1']:.3f} auc={t['auc_roc']:.3f}")

    print("\nFeature importance:")
    for feat, imp in results["feature_importance"].items():
        print(f"  {feat:<25} {imp:.4f}")
