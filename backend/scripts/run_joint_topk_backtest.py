"""Phase 4 joint top-K backtest (bull + bear) — bullish_side_build_2026-05-12.

Validates the joint rec_ranker.select_candidates path end-to-end on historical
data. Per fold:
  1. Train a drop model on training slice (label = 5d-forward return < -3%).
  2. Train a rise model on training slice (label = 5d-forward return > +3%).
  3. Predict drop_prob and rise_prob on test slice.
  4. Per test date, build candidates = [(ticker, "drop", drop_prob),
     (ticker, "rise", rise_prob)] for every test ticker, run
     select_candidates(top_k), evaluate each selected rec against the
     direction-matched forward-return label.
  5. Aggregate per-fold and per-direction metrics.

Trade scoring (mirrors the unit-backtester convention):
  drop bet wins → +1.0 if label_drop == 1, else -1.5
  rise bet wins → +1.0 if label_rise == 1, else -1.5

Outputs a markdown report at
`backend/backtest_reports/<YYYY-MM-DD>-joint-topk-<git-sha>.md`.

Usage:
    python scripts/run_joint_topk_backtest.py --folds 4 --top-k 10
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from src.models.directional import DirectionalModel, build_dataset
from src.models.ensemble import EnsembleScore
from src.pipeline.rec_ranker import Candidate, select_candidates

REPORT_DIR = Path(__file__).resolve().parent.parent / "backtest_reports"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("joint_topk_backtest")

WIN_PAYOFF = 1.0
LOSS_PAYOFF = -1.5


@dataclass
class JointFoldResult:
    fold: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    n_train: int
    n_test_dates: int
    drop_auc: float
    rise_auc: float
    n_selected: int
    n_drop_selected: int
    n_rise_selected: int
    drop_hit_rate: float
    rise_hit_rate: float
    overall_hit_rate: float
    avg_pnl_per_trade: float


@dataclass
class JointBacktestResult:
    folds: list[JointFoldResult] = field(default_factory=list)
    aggregate: dict[str, float] = field(default_factory=dict)
    direction_split: dict[str, int] = field(default_factory=dict)


def _fit_xgb(X: pd.DataFrame, y: pd.Series) -> xgb.XGBClassifier:
    pos_weight = len(y[y == 0]) / max(len(y[y == 1]), 1)
    model = xgb.XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=pos_weight, eval_metric="logloss",
        random_state=42, n_jobs=-1,
    )
    model.fit(X, y, verbose=False)
    return model


def _build_merged_dataset() -> pd.DataFrame:
    """Build a single frame with both `label_drop` and `label_rise`."""
    df_drop = build_dataset(direction="drop")
    df_rise = build_dataset(direction="rise")
    if df_drop.empty or df_rise.empty:
        raise ValueError("Empty drop or rise dataset")
    df = df_drop.rename(columns={"label": "label_drop"}).merge(
        df_rise[["ticker", "date", "label"]].rename(columns={"label": "label_rise"}),
        on=["ticker", "date"], how="inner",
    )
    if df.empty:
        raise ValueError("Drop/rise datasets did not align on (ticker, date)")
    return df.sort_values("date").reset_index(drop=True)


def _trade_pnl(direction: str, row: pd.Series) -> float:
    if direction == "drop":
        return WIN_PAYOFF if row["label_drop"] == 1 else LOSS_PAYOFF
    return WIN_PAYOFF if row["label_rise"] == 1 else LOSS_PAYOFF


def _build_candidates_for_date(test_slice: pd.DataFrame) -> list[Candidate]:
    """One drop + one rise Candidate per ticker on a given test date."""
    candidates: list[Candidate] = []
    for _, row in test_slice.iterrows():
        for direction, prob in (("drop", row["drop_prob"]), ("rise", row["rise_prob"])):
            candidates.append(
                Candidate(
                    ticker=row["ticker"],
                    direction=direction,
                    score=EnsembleScore(
                        ticker=row["ticker"],
                        direction=direction,
                        score=float(prob),
                        directional_signal=float(prob),
                        volatility_signal=0.0,
                        sentiment_signal=0.0,
                    ),
                    extras={"row_index": row.name},
                )
            )
    return candidates


def run_joint_backtest(
    df: pd.DataFrame, feature_cols: list[str], n_folds: int, top_k: int,
    train_min_rows: int = 1000,
) -> JointBacktestResult:
    dates = sorted(df["date"].unique())
    if len(dates) < n_folds + 1:
        raise ValueError(f"Need ≥{n_folds + 1} unique dates; have {len(dates)}")
    fold_size = len(dates) // (n_folds + 1)

    result = JointBacktestResult()
    direction_totals: Counter[str] = Counter()

    for fold in range(n_folds):
        train_end = (fold + 1) * fold_size
        test_end = min(train_end + fold_size, len(dates))
        train_dates = dates[:train_end]
        test_dates = dates[train_end:test_end]
        if not test_dates:
            continue

        train_df = df[df["date"].isin(train_dates)]
        test_df = df[df["date"].isin(test_dates)]
        if len(train_df) < train_min_rows or test_df.empty:
            logger.warning("Fold %d skipped (train=%d, test=%d)", fold + 1, len(train_df), len(test_df))
            continue

        X_train = train_df[feature_cols]
        y_drop = train_df["label_drop"]
        y_rise = train_df["label_rise"]

        drop_model = _fit_xgb(X_train, y_drop)
        rise_model = _fit_xgb(X_train, y_rise)

        X_test = test_df[feature_cols]
        drop_prob = drop_model.predict_proba(X_test)[:, 1]
        rise_prob = rise_model.predict_proba(X_test)[:, 1]

        test_view = test_df.copy()
        test_view["drop_prob"] = drop_prob
        test_view["rise_prob"] = rise_prob

        from sklearn.metrics import roc_auc_score
        drop_auc = (roc_auc_score(test_view["label_drop"], drop_prob)
                    if test_view["label_drop"].nunique() > 1 else float("nan"))
        rise_auc = (roc_auc_score(test_view["label_rise"], rise_prob)
                    if test_view["label_rise"].nunique() > 1 else float("nan"))

        per_direction_wins: dict[str, int] = defaultdict(int)
        per_direction_trades: dict[str, int] = defaultdict(int)
        pnls: list[float] = []

        for d, day_slice in test_view.groupby("date"):
            candidates = _build_candidates_for_date(day_slice)
            selected = select_candidates(candidates, top_k=top_k)
            for cand in selected:
                row_index = cand.extras["row_index"]
                row = day_slice.loc[row_index]
                pnl = _trade_pnl(cand.direction, row)
                pnls.append(pnl)
                per_direction_trades[cand.direction] += 1
                if pnl > 0:
                    per_direction_wins[cand.direction] += 1
                direction_totals[cand.direction] += 1

        n_drop = per_direction_trades.get("drop", 0)
        n_rise = per_direction_trades.get("rise", 0)
        drop_hit = (per_direction_wins["drop"] / n_drop) if n_drop else 0.0
        rise_hit = (per_direction_wins["rise"] / n_rise) if n_rise else 0.0
        overall_hit = (sum(per_direction_wins.values()) / max(len(pnls), 1))
        avg_pnl = float(np.mean(pnls)) if pnls else 0.0

        fr = JointFoldResult(
            fold=fold + 1,
            train_start=train_dates[0], train_end=train_dates[-1],
            test_start=test_dates[0], test_end=test_dates[-1],
            n_train=len(train_df), n_test_dates=len(test_dates),
            drop_auc=float(drop_auc) if not np.isnan(drop_auc) else 0.0,
            rise_auc=float(rise_auc) if not np.isnan(rise_auc) else 0.0,
            n_selected=len(pnls),
            n_drop_selected=n_drop, n_rise_selected=n_rise,
            drop_hit_rate=drop_hit, rise_hit_rate=rise_hit,
            overall_hit_rate=overall_hit, avg_pnl_per_trade=avg_pnl,
        )
        result.folds.append(fr)
        logger.info(
            "Fold %d: drop_AUC=%.3f rise_AUC=%.3f selected=%d (%d drop / %d rise) "
            "drop_hit=%.1f%% rise_hit=%.1f%% overall_hit=%.1f%% avg_pnl=%.3f",
            fr.fold, fr.drop_auc, fr.rise_auc, fr.n_selected,
            fr.n_drop_selected, fr.n_rise_selected,
            fr.drop_hit_rate * 100, fr.rise_hit_rate * 100,
            fr.overall_hit_rate * 100, fr.avg_pnl_per_trade,
        )

    if not result.folds:
        raise ValueError("No folds completed — check train_min_rows vs dataset size")

    result.direction_split = dict(direction_totals)
    drop_aucs = [f.drop_auc for f in result.folds if f.drop_auc > 0]
    rise_aucs = [f.rise_auc for f in result.folds if f.rise_auc > 0]
    drop_hits = [f.drop_hit_rate for f in result.folds if f.n_drop_selected > 0]
    rise_hits = [f.rise_hit_rate for f in result.folds if f.n_rise_selected > 0]
    pnls = [f.avg_pnl_per_trade for f in result.folds if f.n_selected > 0]
    result.aggregate = {
        "mean_drop_auc": float(np.mean(drop_aucs)) if drop_aucs else 0.0,
        "mean_rise_auc": float(np.mean(rise_aucs)) if rise_aucs else 0.0,
        "mean_drop_hit_rate": float(np.mean(drop_hits)) if drop_hits else 0.0,
        "mean_rise_hit_rate": float(np.mean(rise_hits)) if rise_hits else 0.0,
        "mean_avg_pnl": float(np.mean(pnls)) if pnls else 0.0,
        "total_selected": int(sum(f.n_selected for f in result.folds)),
        "n_folds": len(result.folds),
    }
    return result


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=Path(__file__).resolve().parent.parent).decode().strip()
    except Exception:
        return "local"


def write_report(result: JointBacktestResult, out_dir: Path, top_k: int, git_sha: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    out_path = out_dir / f"{today}-joint-topk-{git_sha}.md"

    lines = [
        f"# Phase 4 joint top-K backtest — {today} ({git_sha})",
        "",
        f"Top-K per date: **{top_k}**.  Folds: **{result.aggregate.get('n_folds', 0)}**.",
        "",
        "## Aggregate",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Mean drop AUC | {result.aggregate.get('mean_drop_auc', 0):.4f} |",
        f"| Mean rise AUC | {result.aggregate.get('mean_rise_auc', 0):.4f} |",
        f"| Mean drop hit rate | {result.aggregate.get('mean_drop_hit_rate', 0):.2%} |",
        f"| Mean rise hit rate | {result.aggregate.get('mean_rise_hit_rate', 0):.2%} |",
        f"| Mean avg P&L per trade | {result.aggregate.get('mean_avg_pnl', 0):.4f} |",
        f"| Total trades selected | {result.aggregate.get('total_selected', 0)} |",
        "",
        "## Direction split across all folds",
        "",
        "| Direction | Count |",
        "|-----------|-------|",
    ]
    for direction, count in sorted(result.direction_split.items()):
        lines.append(f"| {direction} | {count} |")
    lines += ["", "## Per-fold detail", ""]
    lines.append("| Fold | Train | Test | drop AUC | rise AUC | sel | drop sel | rise sel | drop hit | rise hit | avg P&L |")
    lines.append("|------|-------|------|---------:|---------:|----:|---------:|---------:|---------:|---------:|--------:|")
    for f in result.folds:
        lines.append(
            f"| {f.fold} | {f.train_start}→{f.train_end} | {f.test_start}→{f.test_end} | "
            f"{f.drop_auc:.4f} | {f.rise_auc:.4f} | {f.n_selected} | "
            f"{f.n_drop_selected} | {f.n_rise_selected} | "
            f"{f.drop_hit_rate:.2%} | {f.rise_hit_rate:.2%} | {f.avg_pnl_per_trade:.4f} |"
        )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 4 joint top-K (bull+bear) backtest")
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--top-k", type=int, default=10,
                        help="Per-date top-K cap (mirrors live scheduler default)")
    parser.add_argument("--train-min-rows", type=int, default=1000)
    parser.add_argument("--git-sha", default=_git_sha())
    args = parser.parse_args()

    df = _build_merged_dataset()
    feature_cols = DirectionalModel().feature_cols
    result = run_joint_backtest(
        df=df, feature_cols=feature_cols,
        n_folds=args.folds, top_k=args.top_k,
        train_min_rows=args.train_min_rows,
    )
    out = write_report(result, REPORT_DIR, top_k=args.top_k, git_sha=args.git_sha)
    logger.info("Report written: %s", out)

    agg = result.aggregate
    print(
        f"\nMean drop AUC: {agg['mean_drop_auc']:.4f}  "
        f"Mean rise AUC: {agg['mean_rise_auc']:.4f}  "
        f"Mean drop hit: {agg['mean_drop_hit_rate']:.2%}  "
        f"Mean rise hit: {agg['mean_rise_hit_rate']:.2%}  "
        f"Avg P&L: {agg['mean_avg_pnl']:.4f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
