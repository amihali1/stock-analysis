"""Quick hyperparameter sweep on the directional model.

Tests a few regularization variants against the baseline to see if the model
is feature-saturated (overfitting on 12k rows × 32 features).

Run inside the backend container:
    docker exec backend-backend-1 python -m scripts.sweep_hyperparams
"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import xgboost as xgb
from sklearn.metrics import roc_auc_score

from src.models.directional import DirectionalModel, build_dataset


def fit_eval(X_train, y_train, X_test, y_test, label: str, **xgb_kwargs):
    pos_weight = (y_train == 0).sum() / max((y_train == 1).sum(), 1)
    model = xgb.XGBClassifier(
        scale_pos_weight=pos_weight, eval_metric="logloss",
        random_state=42, n_jobs=-1, **xgb_kwargs,
    )
    model.fit(X_train, y_train, verbose=False)
    y_prob = model.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, y_prob) if len(set(y_test)) > 1 else float("nan")
    return label, auc


def main() -> int:
    print("Building dataset...")
    df = build_dataset()
    feature_cols = DirectionalModel().feature_cols
    df = df.sort_values("date").reset_index(drop=True)
    cut = int(len(df) * 0.8)
    train, test = df.iloc[:cut], df.iloc[cut:]
    X_train, y_train = train[feature_cols], train["label"]
    X_test, y_test = test[feature_cols], test["label"]
    print(f"Train: {len(X_train)}  Test: {len(X_test)}  Features: {len(feature_cols)}")
    print()

    configs = [
        ("baseline (depth=5, n=200, lr=0.05)",
         dict(max_depth=5, n_estimators=200, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8)),
        ("shallow (depth=3, n=300, lr=0.05)",
         dict(max_depth=3, n_estimators=300, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8)),
        ("very shallow (depth=2, n=500, lr=0.03)",
         dict(max_depth=2, n_estimators=500, learning_rate=0.03, subsample=0.8, colsample_bytree=0.7)),
        ("L2-reg (depth=5, reg_lambda=10)",
         dict(max_depth=5, n_estimators=200, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, reg_lambda=10.0)),
        ("min_child_weight=20",
         dict(max_depth=5, n_estimators=200, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, min_child_weight=20)),
        ("colsample=0.5 (more feature dropout)",
         dict(max_depth=5, n_estimators=200, learning_rate=0.05, subsample=0.8, colsample_bytree=0.5)),
        ("low gamma (depth=4, gamma=2)",
         dict(max_depth=4, n_estimators=300, learning_rate=0.04, subsample=0.8, colsample_bytree=0.7, gamma=2.0)),
    ]

    print(f"{'config':<45s} {'auc':>7}")
    print("-" * 55)
    results = []
    for label, cfg in configs:
        _, auc = fit_eval(X_train, y_train, X_test, y_test, label, **cfg)
        results.append((label, auc))
        print(f"{label:<45s} {auc:>7.4f}")

    results.sort(key=lambda r: r[1], reverse=True)
    print()
    print("=== Top 3 ===")
    for label, auc in results[:3]:
        print(f"  {auc:.4f}  {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
