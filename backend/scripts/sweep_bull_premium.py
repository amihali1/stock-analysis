"""P11-001: bull-side short-premium sweep — which structure should express bull picks?

The prod bull book buys call debit spreads, i.e. PAYS the volatility risk
premium that systematic sellers harvest (audit 2026-07-14). This sweep
re-simulates the SAME walk-forward rise picks under four structures:

  1. long_stock   — reference; what the money-layer sweep actually measured
  2. call_debit   — prod baseline: buy call @ entry*1.02, sell call @ entry*1.07
                    (SpreadBuilder._bull_call_spread targets), return on debit
  3. put_credit   — bull put credit spread: sell put @ entry*0.98, buy put
                    @ entry*0.93 (prod _bull_put_credit_spread targets),
                    return on collateral (width - credit)
  4. csp          — cash-secured put, strike entry*(1-dist), dist {2%, 5%},
                    return on strike (cash securing the put)
  5. put_credit_delta — bull put credit spread with the sell leg at a target
                    |put delta| {0.20, 0.25, 0.30} (prod bull_put_sell_delta,
                    2026-09-29), long leg 5% of entry below; return on
                    collateral. Strikes are continuous (no chain grid).

Both put-credit arms run under two tenors:
  expiry — option expires at the hold horizon (T = hold/252), settled at
           intrinsic on the exit close. The original P11-001 setup.
  30dte  — prod-like: sell a 30-calendar-day spread, exit at the hold
           horizon marked with Black-Scholes on the remaining ~30-hold*7/5
           calendar days at the same sigma. Closer to live, which sells
           ~30 DTE; still no early stop-outs.

Pricing is Black-Scholes with sigma = HV20 * vrp_mult. CRITICAL CAVEAT:
at vrp_mult=1.0 the simulation is VRP-NEUTRAL — implied equals realized, so
it measures payoff geometry against realized paths but cannot show the
volatility risk premium itself. vrp_mult=1.15 prices options ~15% richer
than realized vol (roughly the documented equity VRP), which HELPS the two
short-premium arms and HURTS the debit arm. Compare both to see how much of
any conclusion rides on the premium existing.

Decision criteria are the LEFT-TAIL metrics (p5, worst-5 mean, min), not win
rate — our own bear-side sweep (2026-07-07) had high-win-rate credit spreads
with negative expectancy.

Exit-management arm (put_credit_exits): 30 DTE spreads at the legacy and
delta-0.25 strikes walked DAILY to expiry, marked with Black-Scholes at the
remaining time each session. Exits: take-profit when the spread has captured
TP x credit (TP {none, 0.25, 0.50, 0.75}); stop when the mark-to-market loss
reaches STOP x max loss (STOP {off, 0.60} — 0.60 is prod
catastrophic_spread_loss_fraction); otherwise settle at intrinsic on expiry.
Reports ror_per_day (return on collateral / sessions held) because early
exits free the slot for the next trade. Same-sigma marks ignore IV moves —
real IV usually rises into selloffs (stops fire earlier) and decays in
rallies (take-profits fire earlier).

Grid: K {5,10} x H {5,10} x vrp_mult {1.0, 1.15} x regime {all, up, down}
(x tenor {expiry, 30dte} x sell_delta for the put-credit arms).
Output: trained_models/sweep_bull_premium.json

Usage (inside backend container):
    python scripts/sweep_bull_premium.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.db.models import PriceHistory  # noqa: E402
from src.db.session import SessionLocal  # noqa: E402
from src.models.directional import (  # noqa: E402
    DEFAULT_DROP_VOL_K,
    FEATURE_COLS,
    LABEL_MODE_EXCESS,
    LABEL_MODE_VOL_NORMALIZED,
    _resolve_model_dir,
    build_dataset,
)
from run_joint_topk_backtest import _build_candidates_for_date, _fit_xgb  # noqa: E402
from src.pipeline.rec_ranker import select_candidates  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bull-premium")

N_FOLDS = 4
KS = [5, 10]
HOLDS = [5, 10]
VRP_MULTS = [1.0, 1.15]
CSP_DISTS = [0.02, 0.05]
RISK_FREE = 0.04
# Prod strike discipline (options_strategies.py targets)
DEBIT_BUY, DEBIT_SELL = 1.02, 1.07
CREDIT_SELL, CREDIT_BUY = 0.98, 0.93
# Delta-targeted put credit (options_strategies bull_put_sell_delta)
SELL_DELTAS = [0.20, 0.25, 0.30]
DELTA_WIDTH_PCT = 0.05
TENORS = ["expiry", "30dte"]
OPT_DTE_CAL = 30
# Exit-management sweep: 30 calendar days ~= 21 sessions
EXIT_SESSIONS = 21
TAKE_PROFITS = [None, 0.25, 0.50, 0.75]
STOP_FRACTIONS = [None, 0.60]
# Bad-data guard: annualized HV above this is a price_history artifact
# (unadjusted splits — KLAC 10:1 2026-06-12 gave volatility_20d = 32.5, whose
# BS credit ~= spread width turned three picks into +200..+450x returns).
MAX_VOL_ANN = 3.0


def _bs_call(S, K, T, sigma, r=RISK_FREE):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, S - K)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return float(S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2))


def _bs_put(S, K, T, sigma, r=RISK_FREE):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, K - S)
    call = _bs_call(S, K, T, sigma, r)
    return float(call - S + K * np.exp(-r * T))


def _put_strike_for_delta(S, abs_delta, T, sigma, r=RISK_FREE):
    """Strike whose put delta N(d1) - 1 equals -abs_delta."""
    d1 = norm.ppf(1.0 - abs_delta)
    return float(S * np.exp(-(d1 * sigma * np.sqrt(T) - (r + 0.5 * sigma**2) * T)))


def _put_spread_value(S, ksell, kbuy, T, sigma):
    """Value of the short-put-spread liability (sell leg minus buy leg)."""
    return _bs_put(S, ksell, T, sigma) - _bs_put(S, kbuy, T, sigma)


def _put_credit_ror(entry, exit_, ksell, kbuy, T_open, T_left, sigma):
    """Return on collateral for a bull put credit spread opened at `entry`
    with T_open to expiry and closed at `exit_` with T_left remaining
    (T_left <= 0 settles at intrinsic). None if the spread has no credit."""
    credit = _put_spread_value(entry, ksell, kbuy, T_open, sigma)
    collateral = (ksell - kbuy) - credit
    if credit <= 0 or collateral <= 0:
        return None
    close_cost = _put_spread_value(exit_, ksell, kbuy, T_left, sigma)
    return (credit - close_cost) / collateral


def _tenor_times(tenor: str, hold: int) -> tuple[float, float]:
    """(T at open, T remaining at exit) in years."""
    if tenor == "expiry":
        return hold / 252.0, 0.0
    left_cal = max(OPT_DTE_CAL - hold * 7 / 5, 0.0)
    return OPT_DTE_CAL / 365.0, left_cal / 365.0


def _managed_put_credit(closes, ksell, kbuy, sigma, take_profit, stop_frac):
    """Walk a 30-DTE bull put credit spread day by day.

    `closes[0]` is the entry close, `closes[i]` the close i sessions later.
    Returns (return on collateral, sessions held, exit reason) or None if the
    spread has no credit.
    """
    entry = closes[0]
    T_open = OPT_DTE_CAL / 365.0
    credit = _put_spread_value(entry, ksell, kbuy, T_open, sigma)
    collateral = (ksell - kbuy) - credit
    if credit <= 0 or collateral <= 0:
        return None
    last = len(closes) - 1
    for i in range(1, last + 1):
        T_left = max(OPT_DTE_CAL - i * 7 / 5, 0.0) / 365.0
        if i == last:
            T_left = 0.0
        pnl = credit - _put_spread_value(closes[i], ksell, kbuy, T_left, sigma)
        if take_profit is not None and pnl >= take_profit * credit:
            return pnl / collateral, i, "tp"
        if stop_frac is not None and -pnl >= stop_frac * collateral:
            return pnl / collateral, i, "stop"
        if T_left <= 0:
            return pnl / collateral, i, "expiry"
    return None


def _load_paths() -> dict[str, pd.DataFrame]:
    db = SessionLocal()
    try:
        rows = db.query(
            PriceHistory.ticker, PriceHistory.date, PriceHistory.close,
        ).filter(PriceHistory.close.isnot(None)).all()
    finally:
        db.close()
    df = pd.DataFrame(rows, columns=["ticker", "date", "close"])
    out: dict[str, pd.DataFrame] = {}
    for ticker, g in df.groupby("ticker"):
        out[ticker] = g.sort_values("date").set_index("date")
    return out


def _merged_dataset() -> pd.DataFrame:
    df_drop = build_dataset(
        direction="drop", label_mode=LABEL_MODE_VOL_NORMALIZED,
        feature_cols=FEATURE_COLS, vol_k=DEFAULT_DROP_VOL_K,
    )
    df_rise = build_dataset(
        direction="rise", label_mode=LABEL_MODE_EXCESS, feature_cols=FEATURE_COLS,
    )
    df = df_drop.rename(columns={"label": "label_drop"}).merge(
        df_rise[["ticker", "date", "label"]].rename(columns={"label": "label_rise"}),
        on=["ticker", "date"], how="inner",
    )
    return df.sort_values("date").reset_index(drop=True)


def _exit_close(path: pd.DataFrame, entry_date, hold: int):
    try:
        loc = path.index.get_loc(entry_date)
    except KeyError:
        return None
    if loc + hold >= len(path):
        return None
    entry = float(path.iloc[loc]["close"])
    exit_ = float(path.iloc[loc + hold]["close"])
    if entry <= 0 or not np.isfinite(entry) or not np.isfinite(exit_):
        return None
    return entry, exit_


def _summ(rets: list[float]) -> dict | None:
    if not rets:
        return None
    arr = np.sort(np.array(rets))
    worst5 = arr[: min(5, len(arr))]
    return {
        "n": len(arr),
        "expectancy": round(float(arr.mean()), 5),
        "std": round(float(arr.std()), 5),
        "win_rate": round(float((arr > 0).mean()), 4),
        "total": round(float(arr.sum()), 3),
        "p5": round(float(np.percentile(arr, 5)), 5),
        "worst5_mean": round(float(worst5.mean()), 5),
        "min": round(float(arr[0]), 5),
    }


def main() -> int:
    logger.info("Building merged dataset...")
    df = _merged_dataset()
    paths = _load_paths()
    spy = paths["SPY"].copy()
    spy["sma50"] = spy["close"].rolling(50).mean()
    logger.info("Merged: %d rows; paths: %d tickers", len(df), len(paths))

    dates = sorted(df["date"].unique())
    fold_size = len(dates) // (N_FOLDS + 1)

    bull_picks: dict[int, list[dict]] = {k: [] for k in KS}
    for fold in range(N_FOLDS):
        train = df[df["date"].isin(set(dates[: (fold + 1) * fold_size]))]
        test = df[df["date"].isin(set(dates[(fold + 1) * fold_size: (fold + 2) * fold_size]))].copy()
        logger.info("Fold %d: train=%d test=%d", fold + 1, len(train), len(test))
        drop_model = _fit_xgb(train[FEATURE_COLS], train["label_drop"], dates=train["date"], calibrate=True)
        rise_model = _fit_xgb(train[FEATURE_COLS], train["label_rise"], dates=train["date"], calibrate=True)
        test["drop_prob"] = drop_model.predict_proba(test[FEATURE_COLS])[:, 1]
        test["rise_prob"] = rise_model.predict_proba(test[FEATURE_COLS])[:, 1]

        for d, day_slice in test.groupby("date"):
            candidates = _build_candidates_for_date(day_slice)
            for k in KS:
                for c in select_candidates(candidates, top_k=k, min_score=None):
                    if c.direction != "rise":
                        continue
                    row = day_slice.loc[c.extras["row_index"]]
                    vol_ann = float(row["volatility_20d"])
                    if not np.isfinite(vol_ann) or vol_ann <= 0 or vol_ann > MAX_VOL_ANN:
                        continue
                    bull_picks[k].append({"ticker": c.ticker, "date": d, "vol_ann": vol_ann})

    logger.info("Bull picks: %s", {k: len(v) for k, v in bull_picks.items()})

    results = []
    for k in KS:
        for hold in HOLDS:
            T = hold / 252.0
            buckets: dict[tuple, list[float]] = {}
            for tr in bull_picks[k]:
                path = paths.get(tr["ticker"])
                if path is None:
                    continue
                sim = _exit_close(path, tr["date"], hold)
                if sim is None:
                    continue
                entry, exit_ = sim

                sma = spy.loc[tr["date"], "sma50"] if tr["date"] in spy.index else np.nan
                spy_close = float(spy.loc[tr["date"], "close"]) if tr["date"] in spy.index else np.nan
                regime = "up" if (np.isfinite(sma) and np.isfinite(spy_close) and spy_close > sma) else "down"
                regs = ("all", regime)

                long_ret = (exit_ - entry) / entry
                for reg in regs:
                    buckets.setdefault(("long_stock", None, 1.0, reg, "expiry"), []).append(long_ret)

                for vrp in VRP_MULTS:
                    sigma = tr["vol_ann"] * vrp

                    # (2) call debit spread at prod strikes; return on debit
                    kb, ks_ = entry * DEBIT_BUY, entry * DEBIT_SELL
                    debit = _bs_call(entry, kb, T, sigma) - _bs_call(entry, ks_, T, sigma)
                    if debit > 0:
                        payoff = max(exit_ - kb, 0.0) - max(exit_ - ks_, 0.0)
                        ror = (payoff - debit) / debit
                        for reg in regs:
                            buckets.setdefault(("call_debit", None, vrp, reg, "expiry"), []).append(ror)

                    for tenor in TENORS:
                        T_open, T_left = _tenor_times(tenor, hold)

                        # (3) bull put credit spread at legacy prod strikes
                        ror = _put_credit_ror(
                            entry, exit_, entry * CREDIT_SELL, entry * CREDIT_BUY, T_open, T_left, sigma,
                        )
                        if ror is not None:
                            for reg in regs:
                                buckets.setdefault(("put_credit", None, vrp, reg, tenor), []).append(ror)

                        # (5) bull put credit spread, delta-targeted sell leg
                        for sd in SELL_DELTAS:
                            ksell = _put_strike_for_delta(entry, sd, T_open, sigma)
                            ror = _put_credit_ror(
                                entry, exit_, ksell, ksell - entry * DELTA_WIDTH_PCT, T_open, T_left, sigma,
                            )
                            if ror is not None:
                                for reg in regs:
                                    buckets.setdefault(("put_credit_delta", sd, vrp, reg, tenor), []).append(ror)

                    # (4) cash-secured put; return on strike (cash collateral)
                    for dist in CSP_DISTS:
                        kp = entry * (1 - dist)
                        credit = _bs_put(entry, kp, T, sigma)
                        if credit <= 0:
                            continue
                        payoff = max(kp - exit_, 0.0)
                        ror = (credit - payoff) / kp
                        for reg in regs:
                            buckets.setdefault(("csp", dist, vrp, reg, "expiry"), []).append(ror)

            for (structure, param, vrp, reg, tenor), rets in sorted(
                buckets.items(), key=lambda kv: tuple(str(x) for x in kv[0]),
            ):
                s = _summ(rets)
                if s:
                    results.append({
                        "k": k, "hold": hold, "structure": structure,
                        "csp_dist": param if structure == "csp" else None,
                        "sell_delta": param if structure == "put_credit_delta" else None,
                        "tenor": tenor, "vrp_mult": vrp, "regime": reg, **s,
                    })

    # Exit-management arm: daily walk to expiry with take-profit / stop rules.
    for k in KS:
        buckets_x: dict[tuple, list[tuple[float, int, str]]] = {}
        for tr in bull_picks[k]:
            path = paths.get(tr["ticker"])
            if path is None:
                continue
            try:
                loc = path.index.get_loc(tr["date"])
            except KeyError:
                continue
            if loc + EXIT_SESSIONS >= len(path):
                continue
            closes = [float(c) for c in path["close"].iloc[loc: loc + EXIT_SESSIONS + 1]]
            if not all(np.isfinite(c) and c > 0 for c in closes):
                continue
            entry = closes[0]
            sma = spy.loc[tr["date"], "sma50"] if tr["date"] in spy.index else np.nan
            spy_close = float(spy.loc[tr["date"], "close"]) if tr["date"] in spy.index else np.nan
            regime = "up" if (np.isfinite(sma) and np.isfinite(spy_close) and spy_close > sma) else "down"
            for vrp in VRP_MULTS:
                sigma = tr["vol_ann"] * vrp
                T_open = OPT_DTE_CAL / 365.0
                ksell_d = _put_strike_for_delta(entry, 0.25, T_open, sigma)
                strikes = {
                    "legacy": (entry * CREDIT_SELL, entry * CREDIT_BUY),
                    "delta0.25": (ksell_d, ksell_d - entry * DELTA_WIDTH_PCT),
                }
                for strike_rule, (ks, kb) in strikes.items():
                    for tp in TAKE_PROFITS:
                        for stop in STOP_FRACTIONS:
                            res = _managed_put_credit(closes, ks, kb, sigma, tp, stop)
                            if res is None:
                                continue
                            for reg in ("all", regime):
                                buckets_x.setdefault((strike_rule, tp, stop, vrp, reg), []).append(res)
        for (strike_rule, tp, stop, vrp, reg), rows in buckets_x.items():
            rets = [r[0] for r in rows]
            days = np.array([r[1] for r in rows], dtype=float)
            s = _summ(rets)
            if not s:
                continue
            reasons = pd.Series([r[2] for r in rows]).value_counts(normalize=True).round(3).to_dict()
            results.append({
                "k": k, "hold": EXIT_SESSIONS, "structure": "put_credit_exits",
                "strike_rule": strike_rule, "take_profit": tp, "stop_frac": stop,
                "csp_dist": None, "sell_delta": 0.25 if strike_rule == "delta0.25" else None,
                "tenor": "30dte_managed", "vrp_mult": vrp, "regime": reg, **s,
                "avg_sessions": round(float(days.mean()), 2),
                "ror_per_session": round(float(np.mean(rets) / days.mean()), 5),
                "exit_mix": reasons,
            })

    out_path = _resolve_model_dir() / "sweep_bull_premium.json"
    out_path.write_text(json.dumps({
        "note": "vrp_mult=1.0 is VRP-neutral (IV=HV): measures payoff geometry only. "
                "1.15 prices options ~15% over realized (documented equity VRP). "
                "tenor=expiry settles at intrinsic on the hold horizon; 30dte sells 30 "
                "calendar days and marks at exit with Black-Scholes.",
        "configs": results,
    }, indent=2))
    logger.info("Wrote %s", out_path)

    logger.info("=== EXIT MANAGEMENT (K=10, all-regime, 30 DTE walked to expiry) ===")
    for r in sorted(
        [r for r in results if r["structure"] == "put_credit_exits" and r["k"] == 10 and r["regime"] == "all"],
        key=lambda x: (x["vrp_mult"], x["strike_rule"], -x["ror_per_session"]),
    ):
        logger.info(
            "  vrp=%.2f %-9s tp=%-4s stop=%-4s: exp=%+.4f win=%.2f p5=%+.3f days=%.1f exp/day=%+.5f mix=%s n=%d",
            r["vrp_mult"], r["strike_rule"], r["take_profit"], r["stop_frac"], r["expectancy"],
            r["win_rate"], r["p5"], r["avg_sessions"], r["ror_per_session"], r["exit_mix"], r["n"],
        )

    for vrp in VRP_MULTS:
        logger.info("=== ALL-REGIME, vrp_mult=%.2f ===", vrp)
        subset = [r for r in results if r["regime"] == "all" and r["vrp_mult"] in (vrp, 1.0)
                  and (r["vrp_mult"] == vrp or r["structure"] == "long_stock")]
        for r in sorted(subset, key=lambda x: x["expectancy"], reverse=True):
            if r["structure"] == "put_credit_exits":
                continue
            param = r["csp_dist"] if r["csp_dist"] is not None else r["sell_delta"]
            logger.info(
                "  K=%d H=%d %-16s(p=%s) %-6s: exp=%+.4f win=%.2f p5=%+.3f worst5=%+.3f min=%+.3f n=%d",
                r["k"], r["hold"], r["structure"], param, r["tenor"],
                r["expectancy"], r["win_rate"], r["p5"], r["worst5_mean"], r["min"], r["n"],
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
