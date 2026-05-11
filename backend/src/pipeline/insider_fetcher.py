"""SEC Form 4 insider-transaction fetcher (P10-005).

Pulls per-ticker insider activity from EDGAR and writes one row per Form 4
filing into `insider_transactions`. Used both as a daily incremental fetcher
(via the scheduler) and as a bulk backfill (via `scripts/backfill_insider_transactions.py`).

Endpoints:
    - Submissions index:
        https://data.sec.gov/submissions/CIK{padded_cik}.json
      Returns a JSON envelope with `filings.recent.{accessionNumber, filingDate,
      form, primaryDocument, ...}` parallel arrays. We filter `form == "4"`.
    - Filing primary document:
        https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{primary_doc}
      The Form 4 ownership XML — same schema across filers.

Aggregation: the schema (P10-005 part 1) carries `accession_number` as the
unique key, so we collapse multi-line filings (e.g. a buy split across price
lots) into one row per filing. The aggregation rules below are intentionally
lossy in favor of matching the feature shape:

    - `transaction_code`: the most-frequent code across the filing's lines
      (ties broken by first occurrence). Almost always uniform within a
      filing in practice — Form 4s rarely mix P and S in one document.
    - `shares`: signed sum (acquired = +, disposed = -) of all lines'
      shares. Reported as |shares| with sign carried by the code.
    - `price_per_share`: volume-weighted average across lines that have
      a price. Lines with missing price (e.g. grants) are skipped from the
      VWAP, not zero-weighted, so a $0 grant doesn't drag the average down.
    - `total_value`: sum of |shares| * price across priced lines.
    - `shares_owned_after`: taken from the *last* line (post-filing balance
      is the same across lines anyway).

`accession_number` is the natural dedup key — re-fetching a ticker is cheap
because the unique index rejects rows we already have, and we just continue.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable
from xml.etree import ElementTree as ET

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models import InsiderTransaction
from src.db.session import SessionLocal
from src.services import sec_cik
from src.services.sec_http import SecHttpClient

logger = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVES_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{primary_doc}"
)
DEFAULT_LOOKBACK_DAYS = 730


@dataclass
class Form4FilingMeta:
    accession_number: str  # canonical "0001234567-89-012345" form
    filing_date: date
    primary_doc: str


@dataclass
class Form4Parsed:
    transaction_date: date
    insider_name: str | None
    insider_title: str | None
    transaction_code: str | None
    shares: float
    price_per_share: float | None
    total_value: float | None
    shares_owned_after: float | None
    is_director: bool
    is_officer: bool
    is_10pct_owner: bool


def _strip_dashes(accession: str) -> str:
    return accession.replace("-", "")


def _parse_iso_date(s: str) -> date | None:
    """Parse a YYYY-MM-DD string into a date, dropping obviously corrupt years.

    Some Form 4 XMLs carry transcription errors like `0025-07-25` (the SEC
    filer dropped the `20` prefix). `strptime` accepts any year 1-9999, so
    those rows would otherwise land in `transaction_date` and skew the
    "days since insider buy/sell" features. We reject anything outside a
    reasonable trading-data window.
    """
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    if d.year < 1990 or d.year > date.today().year + 1:
        return None
    return d


def _bool_xml(text: str | None) -> bool:
    """Form 4 booleans are '1'/'0' or 'true'/'false'. Anything else => False."""
    if text is None:
        return False
    return text.strip().lower() in ("1", "true")


def _text(elem: ET.Element | None, path: str) -> str | None:
    if elem is None:
        return None
    found = elem.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _float(elem: ET.Element | None, path: str) -> float | None:
    raw = _text(elem, path)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class InsiderTransactionFetcher:
    """Fetch and persist Form 4 filings for the watchlist."""

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
        # Per-iteration sleep is on top of the SEC rate limiter — keep at 0
        # in production, raise during long backfills if SEC starts pushing back.
        self.sleep_s = sleep_s

    def close(self) -> None:
        if self._owns_http:
            self.http.close()
        if self._owns_db:
            self.db.close()

    def __enter__(self) -> "InsiderTransactionFetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ public

    def fetch_all(
        self,
        tickers: list[str] | None = None,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> dict[str, str]:
        """Fetch Form 4 filings for each ticker over the lookback window.

        Returns {ticker: status} where status is one of:
            ok | no_cik | no_filings | error
        """
        if tickers is None:
            from src.db.watchlist import get_watchlist_tickers
            tickers = get_watchlist_tickers(self.db)

        # Refresh CIK cache once at the top so per-ticker calls don't each
        # check freshness (and don't each pay the SEC roundtrip if stale).
        sec_cik.ensure_cache_fresh(self.db, client=self.http)

        results: dict[str, str] = {}
        for ticker in tickers:
            try:
                results[ticker] = self.fetch_one(ticker, lookback_days=lookback_days)
            except Exception:
                logger.exception("Insider fetch failed for %s", ticker)
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
        filings = self._list_form4_filings(cik, since=since)
        if not filings:
            return "no_filings"

        n_inserted = 0
        for meta in filings:
            if self._already_have(meta.accession_number):
                continue
            xml = self._fetch_form4_xml(cik, meta)
            if xml is None:
                continue
            parsed = parse_form4_xml(xml)
            if parsed is None:
                continue
            if self._insert(ticker, meta, parsed):
                n_inserted += 1

        self.db.commit()
        logger.info("%s: inserted %d new Form 4 rows", ticker, n_inserted)
        return "ok"

    # ---------------------------------------------------------------- private

    def _list_form4_filings(self, cik: str, since: date) -> list[Form4FilingMeta]:
        url = SUBMISSIONS_URL.format(cik=cik)
        resp = self.http.get(url)
        payload = resp.json()
        recent = (payload.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        accessions = recent.get("accessionNumber") or []
        filing_dates = recent.get("filingDate") or []
        primary_docs = recent.get("primaryDocument") or []

        n = min(len(forms), len(accessions), len(filing_dates), len(primary_docs))
        out: list[Form4FilingMeta] = []
        for i in range(n):
            if forms[i] != "4":
                continue
            fd = _parse_iso_date(filing_dates[i])
            if fd is None or fd < since:
                continue
            out.append(Form4FilingMeta(
                accession_number=accessions[i],
                filing_date=fd,
                primary_doc=primary_docs[i],
            ))
        return out

    def _fetch_form4_xml(self, cik: str, meta: Form4FilingMeta) -> str | None:
        cik_int = str(int(cik))  # strip leading zeros for archive URL
        # SEC's submissions API often points primaryDocument at the XSL-rendered
        # viewer path (e.g. "xslF345X05/wk-form4_X.xml") which serves HTML. Strip
        # any leading xsl*/ prefix so we fetch the raw ownership XML.
        primary_doc = re.sub(r"^xsl[^/]*/", "", meta.primary_doc)
        url = ARCHIVES_URL.format(
            cik_int=cik_int,
            accession_nodash=_strip_dashes(meta.accession_number),
            primary_doc=primary_doc,
        )
        try:
            resp = self.http.get(url)
        except Exception:
            logger.exception("Failed fetching Form 4 XML at %s", url)
            return None
        return resp.text

    def _already_have(self, accession_number: str) -> bool:
        return (
            self.db.query(InsiderTransaction.id)
            .filter(InsiderTransaction.accession_number == accession_number)
            .first()
            is not None
        )

    def _insert(self, ticker: str, meta: Form4FilingMeta, p: Form4Parsed) -> bool:
        row = InsiderTransaction(
            ticker=ticker,
            accession_number=meta.accession_number,
            filing_date=meta.filing_date,
            transaction_date=p.transaction_date,
            insider_name=p.insider_name,
            insider_title=p.insider_title,
            transaction_code=p.transaction_code,
            shares=p.shares,
            price_per_share=p.price_per_share,
            total_value=p.total_value,
            shares_owned_after=p.shares_owned_after,
            is_director=p.is_director,
            is_officer=p.is_officer,
            is_10pct_owner=p.is_10pct_owner,
            fetched_at=datetime.utcnow(),
        )
        self.db.add(row)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            return False
        return True


# --------------------------------------------------------------- XML parsing

# Form 4 XML is namespace-free and small. We only extract what feeds features —
# fancy schema-aware parsers would be overkill and brittle to schema variants.

_NAMESPACE_RE = re.compile(r"\sxmlns(:[a-zA-Z0-9]+)?=\"[^\"]*\"")


def _strip_namespaces(xml: str) -> str:
    """Drop namespace declarations so ElementTree paths stay simple."""
    return _NAMESPACE_RE.sub("", xml)


def _aggregate_lines(lines: list[Form4Parsed]) -> Form4Parsed | None:
    """Collapse multi-line filings into a single row per filing."""
    if not lines:
        return None

    code_counts: dict[str, int] = {}
    code_first_seen: dict[str, int] = {}
    for i, ln in enumerate(lines):
        if not ln.transaction_code:
            continue
        code_counts[ln.transaction_code] = code_counts.get(ln.transaction_code, 0) + 1
        code_first_seen.setdefault(ln.transaction_code, i)

    dominant_code: str | None
    if code_counts:
        dominant_code = max(
            code_counts.items(),
            key=lambda kv: (kv[1], -code_first_seen[kv[0]]),
        )[0]
    else:
        dominant_code = None

    total_shares = sum(ln.shares for ln in lines)
    priced = [ln for ln in lines if ln.price_per_share is not None and ln.shares != 0]
    if priced:
        total_value_priced = sum(abs(ln.shares) * (ln.price_per_share or 0) for ln in priced)
        total_shares_priced = sum(abs(ln.shares) for ln in priced)
        vwap = total_value_priced / total_shares_priced if total_shares_priced else None
    else:
        vwap = None

    total_value = sum(abs(ln.shares) * (ln.price_per_share or 0) for ln in priced) or None

    last = lines[-1]
    earliest_date = min(ln.transaction_date for ln in lines)

    return Form4Parsed(
        transaction_date=earliest_date,
        insider_name=last.insider_name,
        insider_title=last.insider_title,
        transaction_code=dominant_code,
        shares=total_shares,
        price_per_share=vwap,
        total_value=total_value,
        shares_owned_after=last.shares_owned_after,
        is_director=last.is_director,
        is_officer=last.is_officer,
        is_10pct_owner=last.is_10pct_owner,
    )


def parse_form4_xml(xml: str) -> Form4Parsed | None:
    """Parse a Form 4 ownership XML into a single aggregated row.

    Returns None if the XML is malformed, has no parseable transactions, or
    has zero net shares across all lines.
    """
    cleaned = _strip_namespaces(xml)
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError:
        logger.warning("Form 4 XML parse error")
        return None

    owner = root.find("reportingOwner")
    insider_name = _text(owner.find("reportingOwnerId"), "rptOwnerName") if owner is not None else None
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    insider_title = _text(rel, "officerTitle") if rel is not None else None
    is_director = _bool_xml(_text(rel, "isDirector")) if rel is not None else False
    is_officer = _bool_xml(_text(rel, "isOfficer")) if rel is not None else False
    is_10pct = _bool_xml(_text(rel, "isTenPercentOwner")) if rel is not None else False

    lines: list[Form4Parsed] = []
    # Only non-derivative transactions feed insider features — derivative
    # exercises and option grants don't carry the same directional signal.
    table = root.find("nonDerivativeTable")
    if table is not None:
        for tx in table.findall("nonDerivativeTransaction"):
            line = _parse_non_derivative_tx(
                tx,
                insider_name=insider_name,
                insider_title=insider_title,
                is_director=is_director,
                is_officer=is_officer,
                is_10pct=is_10pct,
            )
            if line is not None:
                lines.append(line)

    return _aggregate_lines(lines)


def _parse_non_derivative_tx(
    tx: ET.Element,
    *,
    insider_name: str | None,
    insider_title: str | None,
    is_director: bool,
    is_officer: bool,
    is_10pct: bool,
) -> Form4Parsed | None:
    tx_date = _parse_iso_date(_text(tx, "transactionDate/value") or "")
    if tx_date is None:
        return None

    code = _text(tx, "transactionCoding/transactionCode")
    acquired_disposed = _text(tx, "transactionAmounts/transactionAcquiredDisposedCode/value")
    raw_shares = _float(tx, "transactionAmounts/transactionShares/value") or 0.0
    price = _float(tx, "transactionAmounts/transactionPricePerShare/value")
    shares_after = _float(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")

    # Carry direction in the sign of `shares` so the aggregate can sum them
    # naturally — features then compute counts/sums by sign without re-checking
    # the code on every row.
    shares = raw_shares if (acquired_disposed or "").upper() == "A" else -raw_shares

    total_value = abs(shares) * price if (price is not None and shares != 0) else None

    return Form4Parsed(
        transaction_date=tx_date,
        insider_name=insider_name,
        insider_title=insider_title,
        transaction_code=code,
        shares=shares,
        price_per_share=price,
        total_value=total_value,
        shares_owned_after=shares_after,
        is_director=is_director,
        is_officer=is_officer,
        is_10pct_owner=is_10pct,
    )
