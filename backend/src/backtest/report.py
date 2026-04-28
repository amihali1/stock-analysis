"""Render walk-forward backtest results to a markdown report (P9-007)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.backtest.walk_forward import BacktestResult


SHIP_AUC = 0.55
SHIP_HIT_RATE = 0.52


def render_report(
    result: BacktestResult,
    *,
    git_sha: str = "unknown",
    old_metrics: dict | None = None,
    title: str | None = None,
) -> str:
    """Format a `BacktestResult` as a markdown comparison report."""
    title = title or f"Walk-forward backtest — {datetime.now().strftime('%Y-%m-%d')}"
    lines: list[str] = [f"# {title}", "", f"**git sha**: {git_sha}", ""]

    agg = result.aggregate
    lines += [
        "## Aggregate metrics",
        "",
        f"- folds run: {agg['n_folds']}",
        f"- mean AUC: **{agg['mean_auc']:.4f}**",
        f"- mean Brier: {agg['mean_brier']:.4f}",
        f"- mean hit rate: **{agg['mean_hit_rate']:.2%}**",
        f"- mean avg P&L per trade: {agg['mean_avg_pnl']:.4f}",
        f"- total trades: {agg['total_trades']}",
        "",
    ]

    if old_metrics:
        lines += [
            "## Old vs new",
            "",
            "| metric | old | new | delta |",
            "| --- | --- | --- | --- |",
        ]
        pairs = [
            ("AUC", old_metrics.get("auc_roc"), agg.get("mean_auc")),
            ("Brier", old_metrics.get("brier_score"), agg.get("mean_brier")),
            ("hit rate", old_metrics.get("hit_rate"), agg.get("mean_hit_rate")),
        ]
        for name, old, new in pairs:
            if old is None or new is None:
                lines.append(f"| {name} | — | {new if new is not None else '—'} | — |")
                continue
            delta = new - old
            lines.append(f"| {name} | {old:.4f} | {new:.4f} | {delta:+.4f} |")
        lines.append("")

    lines += ["## Per-fold detail", "",
              "| fold | train | test | n_train | n_test | AUC | Brier | trades | hit | avg P&L |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for f in result.folds:
        lines.append(
            f"| {f.fold} | {f.train_start}→{f.train_end} | {f.test_start}→{f.test_end} | "
            f"{f.n_train} | {f.n_test} | {f.auc:.3f} | {f.brier:.4f} | {f.n_trades} | "
            f"{f.hit_rate:.2%} | {f.avg_pnl_per_trade:+.3f} |"
        )

    lines += ["", "## Ship gate",
              f"- AUC > {SHIP_AUC}: **{'PASS' if agg['mean_auc'] > SHIP_AUC else 'FAIL'}** ({agg['mean_auc']:.4f})",
              f"- hit rate > {SHIP_HIT_RATE:.0%}: **{'PASS' if agg['mean_hit_rate'] > SHIP_HIT_RATE else 'FAIL'}** ({agg['mean_hit_rate']:.2%})"]
    if old_metrics and old_metrics.get("brier_score") is not None:
        improved = agg["mean_brier"] < old_metrics["brier_score"]
        lines.append(
            f"- Brier improved vs old: **{'PASS' if improved else 'FAIL'}** "
            f"({old_metrics['brier_score']:.4f} → {agg['mean_brier']:.4f})"
        )

    lines += ["", "## Feature columns", "", ", ".join(f"`{c}`" for c in result.feature_cols)]
    return "\n".join(lines)


def write_report(
    result: BacktestResult,
    out_dir: Path,
    *,
    git_sha: str = "unknown",
    old_metrics: dict | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{datetime.now().strftime('%Y-%m-%d')}-{git_sha}.md"
    out_path = out_dir / fname
    out_path.write_text(render_report(result, git_sha=git_sha, old_metrics=old_metrics), encoding="utf-8")
    return out_path
