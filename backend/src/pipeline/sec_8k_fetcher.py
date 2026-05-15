"""Fetch SEC Form 8-K filings into `sec_filings_8k` (P10-009).

8-K metadata (filing date, accession number, item codes) lives entirely in
the SEC `submissions/CIK{padded}.json` endpoint — no per-filing XML or HTML
download needed, unlike Form 4. Each ticker is therefore a single GET that
yields up to 1000 most-recent filings; older filings live in a separate
`files/` chunk that we walk only when a deeper backfill is requested.

Insert is keyed by `accession_number` (globally unique across SEC filings)
so re-runs are idempotent. `is_material` is precomputed at insert time so
feature aggregation doesn't re-parse the item string on every inference.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Iterable

from sqlalchemy.orm import Session

from src.db.models import SECFiling8K
from src.db.session import SessionLocal
from src.features.sec_filings import is_material as _is_material
from src.services import sec_cik
from src.services.sec_http import SecHttpClient

logger = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
DEFAULT_LOOKBACK_DAYS = 1825  # ~5 years; matches free per-ticker history budget


class SEC8KFetcher:
    """Fetch and persist 8-K filings for the watchlist."""

    def __init__(
        self,
        db: Session | None = None,
        http_client: SecHttpClient | None = None,
        sleep_s: float = 0.0,
    ):
        self._owns_db = db is None
        self.db: Session = db or SessionLocal()
        self._owns_http = http_client is None
        self.http: SecHttpClient = http_client or SecHttpClient()
        self.sleep_s = sleep_s

    def close(self) -> None:
        if self._owns_http:
            self.http.close()
        if self._owns_db:
            self.db.close()

    def __enter__(self) -> "SEC8KFetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def fetch_all(
        self,
        tickers: list[str] | None = None,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> dict[str, str]:
        """Fetch 8-K filings for each ticker over the lookback window.

        Returns {ticker: status} where status is one of:
            ok | no_cik | no_filings | error
        """
        if tickers is None:
            from src.db.watchlist import get_watchlist_tickers
            tickers = get_watchlist_tickers(self.db)

        sec_cik.ensure_cache_fresh(self.db, client=self.http)

        results: dict[str, str] = {}
        for ticker in tickers:
            try:
                results[ticker] = self.fetch_one(ticker, lookback_days=lookback_days)
            except Exception:
                logger.exception("8-K fetch failed for %s", ticker)
                results[ticker] = "error"
            if self.sleep_s:
                time.sleep(self.sleep_s)
        return results

    def fetch_one(self, ticker: str, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> str:
        cik = sec_cik.get_cik(self.db, ticker, auto_refresh=False, client=self.http)
        if not cik:
            logger.warning("No CIK for %s; skipping", ticker)
            return "no_cik"

        since = date.today() - timedelta(days=lookback_days)
        rows = list(self._list_8k_filings(cik, since=since))
        if not rows:
            return "no_filings"

        existing = self._existing_accessions({r["accession_number"] for r in rows})
        n_inserted = 0
        now = datetime.utcnow()
        for r in rows:
            if r["accession_number"] in existing:
                continue
            self.db.add(SECFiling8K(
                ticker=ticker,
                cik=cik,
                accession_number=r["accession_number"],
                filing_date=r["filing_date"],
                items=r["items"],
                is_material=_is_material(r["items"]),
                fetched_at=now,
            ))
            n_inserted += 1

        self.db.commit()
        logger.info("%s: inserted %d new 8-K rows (filings examined=%d)",
                    ticker, n_inserted, len(rows))
        return "ok"

    def _list_8k_filings(self, cik: str, since: date) -> Iterable[dict]:
        """Yield {accession_number, filing_date, items} for each 8-K filed
        on or after `since`. Walks the `recent` slice and any historical
        files referenced in `filings.files` whose date window overlaps `since`.
        """
        url = SUBMISSIONS_URL.format(cik=cik)
        payload = self.http.get(url).json()
        filings = payload.get("filings") or {}

        yield from self._extract_8k(filings.get("recent") or {}, since)

        for f in filings.get("files") or []:
            # Skip chunks whose entire date range is before `since`.
            to_date = _parse_date(f.get("filingTo"))
            if to_date is not None and to_date < since:
                continue
            chunk_url = f"https://data.sec.gov/submissions/{f.get('name')}"
            chunk = self.http.get(chunk_url).json()
            yield from self._extract_8k(chunk, since)

    @staticmethod
    def _extract_8k(payload: dict, since: date) -> Iterable[dict]:
        forms = payload.get("form") or []
        dates = payload.get("filingDate") or []
        items_list = payload.get("items") or []
        accs = payload.get("accessionNumber") or []
        n = min(len(forms), len(dates), len(items_list), len(accs))
        for i in range(n):
            if forms[i] != "8-K":
                continue
            d = _parse_date(dates[i])
            if d is None or d < since:
                continue
            yield {
                "accession_number": accs[i],
                "filing_date": d,
                "items": (items_list[i] or "").strip(),
            }

    def _existing_accessions(self, candidates: set[str]) -> set[str]:
        if not candidates:
            return set()
        rows = (
            self.db.query(SECFiling8K.accession_number)
            .filter(SECFiling8K.accession_number.in_(list(candidates)))
            .all()
        )
        return {r[0] for r in rows}


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
