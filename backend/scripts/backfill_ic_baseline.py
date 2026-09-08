"""One-off: seed ic_metrics with a funded-only IC baseline from `recommendations`.

Before `signal_scores` accrues a full-universe history, this backfills a starting
IC time series from the funded recs we already have (2026-05 onward). Sample is
tagged 'funded' so it never mixes with the 'universe' series the nightly job
writes. Also prints the pooled IC (reproduces the 2026-09-08 audit numbers).

Usage (in the backend container):
    python -m scripts.backfill_ic_baseline
"""
from __future__ import annotations

from sqlalchemy import text

from src.db.session import SessionLocal
from src.services.ic_metrics import HORIZONS, _as_date, _daily_ic, _upsert, spearman

# Map recommendation.direction (long/short) → signal_scores direction (rise/drop).
_DIR = {"long": "rise", "short": "drop"}

_ROWS_SQL = text(
    """
    SELECT r.date AS d, r.direction AS dir, r.ticker AS ticker,
           r.directional_signal AS dirsig, r.score AS composite,
           p0.adj_close AS px0,
           (SELECT p2.adj_close FROM price_history p2
             WHERE p2.ticker = r.ticker AND p2.date > r.date
             ORDER BY p2.date LIMIT 1 OFFSET :off) AS fpx
    FROM recommendations r
    JOIN price_history p0 ON p0.ticker = r.ticker AND p0.date = r.date
    """
)


def main() -> None:
    db = SessionLocal()
    try:
        written = 0
        for horizon in HORIZONS:
            result = db.execute(_ROWS_SQL, {"off": horizon - 1})
            by_day: dict = {}
            pooled: dict[str, list[tuple[float, float]]] = {}
            for row in result.mappings():
                m = dict(row)
                px0, fpx = m.get("px0"), m.get("fpx")
                if not (px0 and fpx and px0 > 0):
                    continue
                fret = fpx / px0 - 1.0
                rdir = _DIR.get(m["dir"])
                if rdir is None:
                    continue
                rec = {
                    "dir": rdir,
                    "rise_prob": m["dirsig"] if rdir == "rise" else None,
                    "drop_prob": m["dirsig"] if rdir == "drop" else None,
                    "composite": m["composite"],
                    "fret": fret,
                }
                by_day.setdefault(_as_date(m["d"]), []).append(rec)
                # pooled buckets for the audit-style summary
                sign = 1.0 if rdir == "rise" else -1.0
                pooled.setdefault(f"dirsig_{rdir}", []).append((m["dirsig"], sign * fret))
                pooled.setdefault(f"composite_{rdir}", []).append((m["composite"], sign * fret))

            for d, rows in by_day.items():
                for signal, (ic, n) in _daily_ic(rows).items():
                    _upsert(db, d, signal, horizon, "funded", ic, n)
                    written += 1

            print(f"\n=== horizon {horizon}d — pooled IC (funded, audit-style) ===")
            for name, pairs in sorted(pooled.items()):
                xs = [x for x, _ in pairs]
                ys = [y for _, y in pairs]
                ic = spearman(xs, ys)
                print(f"  {name:18s} n={len(xs):4d}  IC={ic:+.3f}" if ic is not None else f"  {name:18s} n={len(xs):4d}  IC=n/a")

        db.commit()
        print(f"\nWrote {written} funded-sample ic_metrics rows.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
