"""Information Coefficient (IC) computation for the recommendation signals.

IC = cross-sectional rank correlation (Spearman) between a signal's value on
day d and the realized forward return over the next H trading days. It is the
primary quality metric for the ranker — the 2026-09-08 audit found the composite
score's IC was *negative* (it anti-ranked), which win-rate/P&L reporting hid.

This module reads the full scored universe from `signal_scores` (persisted by
the recommendations job — every candidate, not just the funded top-K), joins
forward returns from `price_history`, and writes a daily IC time series to
`ic_metrics`.

Signals measured (each sign-adjusted so a POSITIVE IC always means "the signal
ranks in the intended direction"):
  - rise_prob       : rise rows, y = fwd_ret
  - drop_prob       : drop rows, y = -fwd_ret
  - composite_rise  : rise rows composite, y = fwd_ret
  - composite_drop  : drop rows composite, y = -fwd_ret
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.models import IcMetric

HORIZONS: tuple[int, ...] = (5, 10)


def _as_date(v) -> date:
    """Normalize a raw-SQL date value. Postgres returns date; SQLite returns str."""
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])

# Per-row forward return over `:off + 1` trading days (OFFSET is zero-based, so
# the 5th subsequent session is OFFSET 4). px0 is the close on the score date.
_ROWS_SQL = text(
    """
    SELECT s.date AS d, s.direction AS dir, s.ticker AS ticker,
           s.rise_prob AS rise_prob, s.drop_prob AS drop_prob,
           s.composite_score AS composite,
           p0.adj_close AS px0,
           (SELECT p2.adj_close FROM price_history p2
             WHERE p2.ticker = s.ticker AND p2.date > s.date
             ORDER BY p2.date LIMIT 1 OFFSET :off) AS fpx
    FROM signal_scores s
    JOIN price_history p0 ON p0.ticker = s.ticker AND p0.date = s.date
    WHERE s.date >= :start AND s.date <= :end
    """
)


def _rank(values: list[float]) -> list[float]:
    """Average-rank of each value (ties share the mean rank)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:  # no variance → IC undefined
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / (sxx**0.5 * syy**0.5)


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation, or None if undefined (n<3 or no variance)."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    return _pearson(_rank(xs), _rank(ys))


def _daily_ic(rows: list[dict]) -> dict[str, tuple[float, int]]:
    """Compute each signal's IC for a single day's cross-section.

    Returns {signal: (ic, n)} for signals that had enough names with variance.
    """
    rise = [r for r in rows if r["dir"] == "rise" and r["fret"] is not None]
    drop = [r for r in rows if r["dir"] == "drop" and r["fret"] is not None]

    out: dict[str, tuple[float, int]] = {}

    def _add(name: str, sample: list[dict], xkey: str, sign: float) -> None:
        xs = [r[xkey] for r in sample if r[xkey] is not None]
        ys = [sign * r["fret"] for r in sample if r[xkey] is not None]
        ic = spearman(xs, ys)
        if ic is not None:
            out[name] = (ic, len(xs))

    _add("rise_prob", rise, "rise_prob", 1.0)
    _add("drop_prob", drop, "drop_prob", -1.0)
    _add("composite_rise", rise, "composite", 1.0)
    _add("composite_drop", drop, "composite", -1.0)
    return out


def compute_and_store_ic(db: Session, lookback_days: int = 90, sample: str = "universe") -> dict:
    """Compute daily IC for all eligible dates in the lookback window and upsert.

    A date is eligible for horizon H once H forward sessions of prices exist.
    Idempotent: re-running overwrites the same (date, signal, horizon, sample) rows.
    Returns a summary dict for logging.
    """
    end = date.today()
    start = end - timedelta(days=lookback_days)
    written = 0
    days_by_horizon: dict[int, int] = {}

    for horizon in HORIZONS:
        result = db.execute(_ROWS_SQL, {"off": horizon - 1, "start": start, "end": end})
        by_day: dict[date, list[dict]] = {}
        for row in result.mappings():
            m = dict(row)
            px0, fpx = m.get("px0"), m.get("fpx")
            m["fret"] = (fpx / px0 - 1.0) if (px0 and fpx and px0 > 0) else None
            by_day.setdefault(_as_date(m["d"]), []).append(m)

        for d, rows in by_day.items():
            # Skip days without a full forward window (no priced forward return).
            if not any(r["fret"] is not None for r in rows):
                continue
            metrics = _daily_ic(rows)
            if metrics:
                days_by_horizon[horizon] = days_by_horizon.get(horizon, 0) + 1
            for signal, (ic, n) in metrics.items():
                _upsert(db, d, signal, horizon, sample, ic, n)
                written += 1

    db.commit()
    return {"written": written, "days_by_horizon": days_by_horizon, "start": str(start), "end": str(end)}


def _upsert(db: Session, d: date, signal: str, horizon: int, sample: str, ic: float, n: int) -> None:
    existing = (
        db.query(IcMetric)
        .filter_by(date=d, signal=signal, horizon=horizon, sample=sample)
        .first()
    )
    if existing is None:
        db.add(IcMetric(date=d, signal=signal, horizon=horizon, sample=sample, ic=ic, n=n))
    else:
        existing.ic = ic
        existing.n = n


def rolling_ic_summary(db: Session, window_days: int = 60, sample: str = "universe") -> list[dict]:
    """Mean IC and IR (mean/std) per (signal, horizon) over the recent window.

    IR uses the fundamental-law framing: a small but stable IC compounds into a
    tradeable Information Ratio. Returned sorted by signal then horizon.
    """
    cutoff = date.today() - timedelta(days=window_days)
    rows = (
        db.query(IcMetric)
        .filter(IcMetric.sample == sample, IcMetric.date >= cutoff)
        .all()
    )
    grouped: dict[tuple[str, int], list[float]] = {}
    for r in rows:
        grouped.setdefault((r.signal, r.horizon), []).append(r.ic)

    out: list[dict] = []
    for (signal, horizon), ics in grouped.items():
        n = len(ics)
        mean = sum(ics) / n if n else 0.0
        if n >= 2:
            var = sum((x - mean) ** 2 for x in ics) / (n - 1)
            std = var**0.5
            ir = mean / std if std > 0 else 0.0
        else:
            ir = 0.0
        out.append({
            "signal": signal, "horizon": horizon, "days": n,
            "mean_ic": round(mean, 4), "ir": round(ir, 3),
        })
    out.sort(key=lambda x: (x["signal"], x["horizon"]))
    return out
