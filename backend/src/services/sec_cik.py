"""Ticker -> SEC CIK resolver (P10-005, reused by P10-006).

SEC EDGAR identifies companies by Central Index Key (CIK), not ticker. The
canonical ticker -> CIK mapping is published as a single ~1.5MB JSON file at
https://www.sec.gov/files/company_tickers.json and refreshed by SEC roughly
daily. Rather than fetch it on every Form 4 lookup, we cache the full mapping
in `sec_cik_map` and only refresh when the cache is older than `MAX_AGE_HOURS`.

CIKs in EDGAR URLs are zero-padded to 10 digits. The `cik` we store and return
already carries the padding so callers can drop it straight into a URL.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from src.db.models import SecCikMap
from src.services.sec_http import SecHttpClient

logger = logging.getLogger(__name__)

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
MAX_AGE_HOURS = 24


def _pad_cik(cik: int | str) -> str:
    return str(cik).zfill(10)


def cache_age_hours(db: Session) -> float | None:
    """Return age of newest cache row in hours, or None if cache is empty."""
    newest = (
        db.query(SecCikMap.fetched_at)
        .order_by(SecCikMap.fetched_at.desc())
        .first()
    )
    if not newest or not newest[0]:
        return None
    return (datetime.utcnow() - newest[0]).total_seconds() / 3600.0


def refresh_cache(
    db: Session,
    client: SecHttpClient | None = None,
) -> int:
    """Pull the SEC company_tickers.json and replace the cache. Returns row count."""
    owns_client = client is None
    client = client or SecHttpClient()
    try:
        resp = client.get(COMPANY_TICKERS_URL)
        payload = resp.json()
    finally:
        if owns_client:
            client.close()

    # Payload is a dict keyed by stringified row index. Each value has
    # {cik_str: int, ticker: str, title: str}.
    rows = list(payload.values()) if isinstance(payload, dict) else payload
    logger.info("SEC company_tickers.json returned %d rows", len(rows))

    db.query(SecCikMap).delete()
    now = datetime.utcnow()
    for row in rows:
        ticker = (row.get("ticker") or "").upper().strip()
        cik = row.get("cik_str")
        if not ticker or cik is None:
            continue
        db.add(SecCikMap(
            ticker=ticker,
            cik=_pad_cik(cik),
            company_name=row.get("title"),
            fetched_at=now,
        ))
    db.commit()
    return len(rows)


def ensure_cache_fresh(
    db: Session,
    max_age_hours: float = MAX_AGE_HOURS,
    client: SecHttpClient | None = None,
) -> bool:
    """Refresh the cache if missing or stale. Returns True iff a refresh ran."""
    age = cache_age_hours(db)
    if age is not None and age < max_age_hours:
        return False
    refresh_cache(db, client=client)
    return True


def get_cik(
    db: Session,
    ticker: str,
    auto_refresh: bool = True,
    client: SecHttpClient | None = None,
) -> str | None:
    """Resolve a ticker to its zero-padded 10-digit CIK, or None if unknown."""
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return None

    if auto_refresh:
        ensure_cache_fresh(db, client=client)

    row = db.query(SecCikMap).filter(SecCikMap.ticker == ticker).first()
    return row.cik if row else None
