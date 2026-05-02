"""One-off diagnostic: feature importance, threshold sweep, per-ticker AUC.

Trains a single XGBoost on the first 80% of build_dataset() and evaluates on
the held-out 20%. Mirrors fold-1 of walk_forward but emits richer diagnostics.

Run inside the backend container:
    docker exec backend-backend-1 python -m scripts.diagnose_model
"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import roc_auc_score

from src.models.directional import DirectionalModel, build_dataset


def main() -> int:
    print("Building dataset...")
    df = build_dataset()
    print(f"Total rows: {len(df)}  positive_rate: {df['label'].mean():.3f}")

    feature_cols = DirectionalModel().feature_cols
    df = df.sort_values("date").reset_index(drop=True)

    cut = int(len(df) * 0.8)
    train, test = df.iloc[:cut], df.iloc[cut:]
    print(f"Train rows: {len(train)}  Test rows: {len(test)}")
    print(f"Train pos rate: {train['label'].mean():.3f}  Test pos rate: {test['label'].mean():.3f}")

    pos_weight = (train["label"] == 0).sum() / max((train["label"] == 1).sum(), 1)
    model = xgb.XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=pos_weight, eval_metric="logloss",
        random_state=42, n_jobs=-1,
    )
    model.fit(train[feature_cols], train["label"], verbose=False)

    y_test = test["label"].to_numpy()
    y_prob = model.predict_proba(test[feature_cols])[:, 1]
    auc = roc_auc_score(y_test, y_prob) if len(set(y_test)) > 1 else float("nan")
    print(f"\nOverall test AUC: {auc:.4f}")

    print("\n=== Feature importance (gain) ===")
    importances = sorted(
        zip(feature_cols, model.feature_importances_),
        key=lambda x: x[1], reverse=True,
    )
    for name, imp in importances:
        print(f"  {name:<30s} {imp:.4f}")

    print("\n=== Threshold sweep (test set) ===")
    print(f"  {'thresh':>7} {'trades':>7} {'hit_rate':>9} {'avg_pnl':>9}")
    for t in [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
        traded = []
        for p, lab in zip(y_prob, y_test):
            if p < t:
                continue
            traded.append(1.0 if lab == 1 else -1.5)
        if not traded:
            print(f"  {t:>7.2f} {0:>7d} {'-':>9} {'-':>9}")
            continue
        hit = sum(1 for x in traded if x > 0) / len(traded)
        avg = float(np.mean(traded))
        print(f"  {t:>7.2f} {len(traded):>7d} {hit*100:>8.2f}% {avg:>9.3f}")

    print("\n=== Per-ticker test AUC (top/bottom 10) ===")
    test_with = test.copy()
    test_with["_prob"] = y_prob
    rows = []
    for tk, g in test_with.groupby("ticker"):
        if g["label"].nunique() < 2 or len(g) < 20:
            continue
        try:
            tk_auc = roc_auc_score(g["label"], g["_prob"])
            rows.append((tk, tk_auc, len(g), g["label"].mean()))
        except Exception:
            pass
    rows.sort(key=lambda r: r[1], reverse=True)
    print(f"  {'ticker':<8} {'auc':>6} {'n':>5} {'pos_rate':>9}")
    print("  -- top 10 --")
    for r in rows[:10]:
        print(f"  {r[0]:<8s} {r[1]:>6.3f} {r[2]:>5d} {r[3]:>9.3f}")
    print("  -- bottom 10 --")
    for r in rows[-10:]:
        print(f"  {r[0]:<8s} {r[1]:>6.3f} {r[2]:>5d} {r[3]:>9.3f}")
    print(f"  total tickers with AUC: {len(rows)}  median AUC: {np.median([r[1] for r in rows]):.3f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
