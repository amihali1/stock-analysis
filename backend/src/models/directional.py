"""XGBoost directional classifier: predicts stocks that will drop >3% in 5 trading days."""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (
    accuracy_score, brier_score_loss, f1_score, precision_score,
    recall_score, roc_auc_score,
)

from src.db.models import PriceHistory, TechnicalIndicator
from src.db.session import SessionLocal
from src.features.analyst import ANALYST_FEATURE_COLS, attach_analyst_features
from src.features.earnings import EARNINGS_FEATURE_COLS, attach_earnings_features
from src.features.insider import INSIDER_FEATURE_COLS, attach_insider_features
from src.features.macro import MACRO_FEATURE_COLS, attach_macro_features
from src.features.options import OPTIONS_FEATURE_COLS, attach_options_features
from src.features.sector import SECTOR_FEATURE_COLS, attach_sector_features
from src.features.sentiment import SENTIMENT_FEATURE_COLS, attach_sentiment_features
from src.features.short_interest import SHORT_INTEREST_FEATURE_COLS, attach_short_interest_features
from src.features.wikipedia import WIKIPEDIA_FEATURE_COLS, attach_wikipedia_features

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
    # Phase 9 features (only those with historical coverage; OPTIONS_FEATURE_COLS
    # and SENTIMENT_FEATURE_COLS are excluded from training because we lack
    # historical IV chains and historical sentiment scoring beyond ~2 weeks).
    *MACRO_FEATURE_COLS,          # P9-002
    *SECTOR_FEATURE_COLS,         # P9-003
    *EARNINGS_FEATURE_COLS,       # P9-005
    *ANALYST_FEATURE_COLS,        # P10-001
    *SHORT_INTEREST_FEATURE_COLS, # P10-003
    *WIKIPEDIA_FEATURE_COLS,      # P10-008
    *INSIDER_FEATURE_COLS,        # P10-005
]

def _resolve_model_dir() -> Path:
    """Find the trained_models directory.

    In dev, the package is editable-installed and `__file__` points back into the
    repo so `parent.parent.parent / "trained_models"` resolves correctly. In the
    Docker prod image (post PR #42, 2026-05-14) `__file__` lives in site-packages
    and `/app/src` was deleted, so the relative path resolves to a nonexistent
    site-packages/trained_models. Walk the candidate list and return the first
    one that exists; fall back to the legacy relative path so dev behavior is
    unchanged when neither exists yet (first-time install).
    """
    candidates = [
        Path("/app/trained_models"),
        Path(__file__).parent.parent.parent / "trained_models",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[-1]


MODEL_DIR = _resolve_model_dir()
DEFAULT_MODEL_PATH = MODEL_DIR / "directional_xgb_v1.pkl"
DEFAULT_RISE_MODEL_PATH = MODEL_DIR / "directional_xgb_rise_v2.pkl"

# Target labels: 5-trading-day forward move past ±3%.
DROP_THRESHOLD = -0.03
RISE_THRESHOLD = 0.03
FORWARD_DAYS = 5

# Label modes:
#   "absolute" — label fires on ticker's own forward return crossing ±3% threshold.
#   "excess"   — label fires on (ticker_fwd_return - SPY_fwd_return) crossing ±3%.
# The excess-vs-SPY label removes the macro confound that dominated absolute labels
# (2026-05-14 root cause: volatile regimes show 40% rise base rate driven by
# bounce-back; model learned "high VIX → rise" instead of per-ticker discrimination).
# Rise v2 ships with label_mode="excess"; drop side stays "absolute" pending a
# catalyst-feature backfill (sentiment historicals).
LABEL_MODE_ABSOLUTE = "absolute"
LABEL_MODE_EXCESS = "excess"
SPY_TICKER = "SPY"


class DirectionalModel:
    def __init__(
        self,
        model_path: Path | None = None,
        direction: str = "drop",
        calibration_method: str | None = None,
        label_mode: str | None = None,
    ):
        """A binary directional classifier.

        direction: "drop" (default) labels forward returns < DROP_THRESHOLD;
                   "rise" labels forward returns > RISE_THRESHOLD.
        calibration_method: when None, auto-pick (isotonic for ≥1k samples,
                   sigmoid otherwise). Override to "sigmoid" to skip isotonic —
                   we use sigmoid for both directions now after the 2026-05-12
                   plateau finding (see directional_calibration_plateaus memory).
        label_mode: "absolute" or "excess". When None, defaults to "excess" for
                   rise (rise v2 ships with regime-relative label, 2026-05-14)
                   and "absolute" for drop.
        """
        if direction not in ("drop", "rise"):
            raise ValueError(f"direction must be 'drop' or 'rise', got {direction!r}")
        if label_mode is None:
            label_mode = LABEL_MODE_EXCESS if direction == "rise" else LABEL_MODE_ABSOLUTE
        if label_mode not in (LABEL_MODE_ABSOLUTE, LABEL_MODE_EXCESS):
            raise ValueError(f"label_mode must be 'absolute' or 'excess', got {label_mode!r}")
        self.model: xgb.XGBClassifier | None = None
        self.calibrator: CalibratedClassifierCV | None = None
        self.brier_score: float | None = None
        if model_path is not None:
            self.model_path = model_path
        elif direction == "rise":
            self.model_path = DEFAULT_RISE_MODEL_PATH
        else:
            self.model_path = DEFAULT_MODEL_PATH
        self.feature_cols = FEATURE_COLS
        self.direction = direction
        self.calibration_method = calibration_method
        self.label_mode = label_mode

    def load(self) -> None:
        """Load a trained model from disk."""
        with open(self.model_path, "rb") as f:
            data = pickle.load(f)
        self.model = data["model"]
        self.feature_cols = data.get("feature_cols", FEATURE_COLS)
        self.calibrator = data.get("calibrator")
        self.brier_score = data.get("brier_score")
        # Legacy pickles predate the field; default to "drop" (the only direction
        # we trained before 2026-05-12).
        self.direction = data.get("direction", "drop")
        self.label_mode = data.get("label_mode", LABEL_MODE_ABSOLUTE)
        logger.info(
            "Loaded directional model from %s (direction=%s, label_mode=%s, calibrator=%s)",
            self.model_path, self.direction, self.label_mode,
            "present" if self.calibrator is not None else "absent",
        )

    def save(self) -> None:
        """Save trained model to disk."""
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model": self.model,
            "feature_cols": self.feature_cols,
            "calibrator": self.calibrator,
            "brier_score": self.brier_score,
            "direction": self.direction,
            "label_mode": self.label_mode,
        }
        with open(self.model_path, "wb") as f:
            pickle.dump(payload, f)
        logger.info(f"Saved directional model to {self.model_path}")

    def _proba(self, X: pd.DataFrame) -> np.ndarray:
        """Use the calibrated wrapper if available, else the raw booster."""
        estimator = self.calibrator if self.calibrator is not None else self.model
        return estimator.predict_proba(X)[:, 1]

    def predict(self, features: dict) -> tuple[float, float]:
        """Predict drop probability for a single sample.

        Returns (probability_of_drop, confidence) where confidence
        is how far the probability is from 0.5.
        """
        if self.model is None:
            self.load()

        df = pd.DataFrame([features])
        # Ensure columns match training order; use neutral defaults per feature group
        all_defaults = self._merged_defaults()
        for col in self.feature_cols:
            if col not in df.columns:
                df[col] = all_defaults.get(col, 0.0)
        df = df[self.feature_cols]

        prob = self._proba(df)[0]  # calibrated if available
        confidence = abs(prob - 0.5) * 2  # 0.0 = no confidence, 1.0 = max confidence
        return float(prob), float(confidence)

    def predict_batch(self, df: pd.DataFrame) -> pd.DataFrame:
        """Predict for a batch of samples. Returns df with prob and confidence columns."""
        if self.model is None:
            self.load()

        X = df[self.feature_cols].copy()
        probs = self._proba(X)
        result = df.copy()
        result["drop_probability"] = probs
        result["confidence"] = np.abs(probs - 0.5) * 2
        return result

    @staticmethod
    def _merged_defaults() -> dict[str, float]:
        from src.features.analyst import DEFAULT_ANALYST_FEATURES
        from src.features.earnings import DEFAULT_EARNINGS_FEATURES
        from src.features.insider import DEFAULT_INSIDER_FEATURES
        from src.features.macro import DEFAULT_MACRO_FEATURES
        from src.features.options import DEFAULT_FEATURES as OPTIONS_DEFAULTS
        from src.features.sector import DEFAULT_SECTOR_FEATURES
        from src.features.sentiment import DEFAULT_SENTIMENT_FEATURES
        from src.features.short_interest import DEFAULT_SHORT_INTEREST_FEATURES
        from src.features.wikipedia import DEFAULT_WIKIPEDIA_FEATURES
        out: dict[str, float] = {}
        out.update(OPTIONS_DEFAULTS)
        out.update(DEFAULT_MACRO_FEATURES)
        out.update(DEFAULT_SECTOR_FEATURES)
        out.update(DEFAULT_SENTIMENT_FEATURES)
        out.update(DEFAULT_EARNINGS_FEATURES)
        out.update(DEFAULT_ANALYST_FEATURES)
        out.update(DEFAULT_SHORT_INTEREST_FEATURES)
        out.update(DEFAULT_WIKIPEDIA_FEATURES)
        out.update(DEFAULT_INSIDER_FEATURES)
        return out

    def train(self, tickers: list[str] | None = None, n_folds: int = 3) -> dict:
        """Train the model with walk-forward validation.

        Returns metrics dict.
        """
        logger.info("Building dataset (direction=%s, label_mode=%s)...", self.direction, self.label_mode)
        df = build_dataset(tickers, direction=self.direction, label_mode=self.label_mode)

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

        # Final model: time-ordered three-way split for train / calibration / test
        # so we can fit a CalibratedClassifierCV(cv='prefit') on disjoint data.
        n_dates = len(dates)
        train_end = int(n_dates * 0.70)
        calib_end = int(n_dates * 0.85)
        train_dates_final = set(dates[:train_end])
        calib_dates = set(dates[train_end:calib_end])
        test_dates_final = set(dates[calib_end:])

        train_mask = df["date"].isin(train_dates_final)
        calib_mask = df["date"].isin(calib_dates)
        test_mask = df["date"].isin(test_dates_final)

        X_train = df.loc[train_mask, self.feature_cols]
        y_train = df.loc[train_mask, "label"]
        X_calib = df.loc[calib_mask, self.feature_cols]
        y_calib = df.loc[calib_mask, "label"]
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

        # Calibrate on the held-out calibration fold (P9-006).
        # Auto-pick: isotonic for ≥1k samples, sigmoid otherwise. Override via
        # constructor `calibration_method`. Post-2026-05-12 we force sigmoid for
        # rise too — isotonic emits ~4 discrete plateaus that broke the gate.
        self.calibrator = None
        if len(X_calib) >= 200 and len(set(y_calib)) > 1:
            method = self.calibration_method or (
                "isotonic" if len(X_calib) >= 1000 else "sigmoid"
            )
            try:
                self.calibrator = CalibratedClassifierCV(
                    estimator=self.model, cv="prefit", method=method,
                )
                self.calibrator.fit(X_calib, y_calib)
                logger.info("Fitted %s calibrator on %d rows", method, len(X_calib))
            except Exception:
                logger.exception("Calibration failed; serving raw probabilities")
                self.calibrator = None
        else:
            logger.warning("Calibration set too small (%d rows) — skipping", len(X_calib))

        y_pred = self.model.predict(X_test)
        y_prob = self._proba(X_test)
        # Brier score reflects calibration quality (lower = better).
        brier = brier_score_loss(y_test, y_prob) if len(set(y_test)) > 1 else None
        self.brier_score = float(brier) if brier is not None else None

        test_metrics = {
            "accuracy": accuracy_score(y_test, y_pred),
            "precision": precision_score(y_test, y_pred, zero_division=0),
            "recall": recall_score(y_test, y_pred, zero_division=0),
            "f1": f1_score(y_test, y_pred, zero_division=0),
            "auc_roc": roc_auc_score(y_test, y_prob) if len(set(y_test)) > 1 else 0.0,
            "brier_score": self.brier_score,
            "calibrated": self.calibrator is not None,
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


def build_dataset(
    tickers: list[str] | None = None,
    direction: str = "drop",
    label_mode: str = LABEL_MODE_ABSOLUTE,
) -> pd.DataFrame:
    """Build training dataset by joining price_history + technical_indicators.

    `direction` selects which forward-return threshold defines the positive class:
    "drop" (default) → label=1 when forward_return < -3%
    "rise"           → label=1 when forward_return > +3%

    `label_mode` controls the return used for the threshold comparison:
    "absolute" → ticker's own forward return
    "excess"   → ticker forward return minus SPY's forward return (same window).
                 Removes macro confound — see module-level comment.
    """
    if label_mode not in (LABEL_MODE_ABSOLUTE, LABEL_MODE_EXCESS):
        raise ValueError(f"label_mode must be 'absolute' or 'excess', got {label_mode!r}")
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

        # For excess-mode, pull SPY closes so we can compute SPY forward return
        # per date and align onto the per-ticker rows.
        spy_fwd_by_date: dict | None = None
        if label_mode == LABEL_MODE_EXCESS:
            spy_rows = db.query(PriceHistory.date, PriceHistory.close).filter(
                PriceHistory.ticker == SPY_TICKER
            ).order_by(PriceHistory.date).all()
            spy_df = pd.DataFrame(spy_rows, columns=["date", "spy_close"])
            spy_df = spy_df.sort_values("date").reset_index(drop=True)
            spy_df["spy_fwd"] = spy_df["spy_close"].shift(-FORWARD_DAYS) / spy_df["spy_close"] - 1
            spy_fwd_by_date = dict(zip(spy_df["date"], spy_df["spy_fwd"]))
    finally:
        db.close()

    # Sort by ticker and date for proper time-series operations
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)

    # Add derived features per ticker
    all_ticker_dfs = []
    for ticker, group in df.groupby("ticker"):
        # SPY is needed for excess-label alignment but never gets a label itself.
        if label_mode == LABEL_MODE_EXCESS and ticker in (SPY_TICKER, "^VIX"):
            continue
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

        # Forward return for label.
        g["forward_return"] = g["close"].shift(-FORWARD_DAYS) / g["close"] - 1

        if label_mode == LABEL_MODE_EXCESS:
            g["spy_fwd"] = g["date"].map(spy_fwd_by_date)
            label_return = g["forward_return"] - g["spy_fwd"]
        else:
            label_return = g["forward_return"]

        # Build label only where label_return is defined. NaN rows get NaN
        # labels and are dropped at the end alongside NaN-feature rows.
        if direction == "rise":
            g["label"] = np.where(label_return.notna(), (label_return > RISE_THRESHOLD).astype(int), np.nan)
        else:
            g["label"] = np.where(label_return.notna(), (label_return < DROP_THRESHOLD).astype(int), np.nan)

        all_ticker_dfs.append(g)

    df = pd.concat(all_ticker_dfs, ignore_index=True)

    # Attach Phase 9 features via DB joins.
    db = SessionLocal()
    try:
        df = attach_options_features(db, df)
        df = attach_macro_features(db, df)
        df = attach_sector_features(db, df)
        df = attach_sentiment_features(db, df)
        df = attach_earnings_features(db, df)
        df = attach_analyst_features(db, df)
        df = attach_short_interest_features(db, df)
        df = attach_wikipedia_features(db, df)
        df = attach_insider_features(db, df)
    finally:
        db.close()

    # Drop rows with NaN features or missing labels (options cols are filled by defaults)
    feature_cols_with_label = FEATURE_COLS + ["label"]
    df = df.dropna(subset=feature_cols_with_label)
    # np.where with NaN branch coerces label dtype to float; cast back now that
    # NaN rows are gone so downstream XGB sees integer labels.
    df["label"] = df["label"].astype(int)

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
