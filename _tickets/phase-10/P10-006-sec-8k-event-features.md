# P10-006: Add SEC 8-K material-event features to directional model

**Status**: todo
**Phase**: 10
**Dependencies**: P10-005 (shares the `sec_cik` ticker→CIK resolver and the SEC fair-use User-Agent / rate-limit infrastructure)
**Estimated scope**: medium (6-8 files)

## Description

8-K filings disclose material events between quarterly reports — acquisitions, executive departures, guidance updates, restatements, bankruptcy, accounting changes, regulatory inquiries. They are the single best-timestamped, highest-frequency, per-ticker event stream that's free in the US equity market. SEC EDGAR exposes the full filing history via the same REST API used by P10-005, so the CIK resolver and User-Agent / rate-limit plumbing are already in place after that ticket lands.

The signal value is in two flavors: **filing rate** (a sudden jump in 8-K filings often precedes price moves regardless of content), and **item-code composition** (Item 2.01 acquisitions vs Item 5.02 executive departures vs Item 4.02 restatements have very different return distributions). We don't need to NLP the filing body — the structured Item codes carry most of the signal and are trivial to extract from the filing index.

Because we're predicting 5-day directional moves, the relevant features are short-window event counts and whether specific high-impact item codes appeared in that window. Filing-rate z-score over a 90-day baseline catches abnormal disclosure activity.

## Acceptance Criteria

- [ ] New `EightKFiling` table + Alembic migration. Columns: `ticker`, `filing_date`, `accepted_datetime` (when SEC received), `accession_number` (unique), `item_codes` (JSON array of strings like `["2.01", "9.01"]`), `is_amendment` (boolean — 8-K/A). Unique index on `accession_number`, composite index on `(ticker, filing_date)`.
- [ ] `EightKFetcher` in `backend/src/pipeline/eight_k_fetcher.py` pulling from `https://data.sec.gov/submissions/CIK{cik}.json` (the same endpoint P10-005 uses), filtering `recent.form == "8-K"` (and `8-K/A`). Item codes are extracted from `recent.primaryDocDescription` or by fetching the filing's `index.json` and parsing the cover page — pick whichever is more reliable in practice and document the choice in the module docstring.
- [ ] Reuse the `sec_cik` resolver, User-Agent header, and rate limiter from P10-005. Do **not** re-implement these — if P10-005's plumbing is in `services/sec_http.py` or similar, import it.
- [ ] `backfill_eight_k_filings.py` script: idempotent, accepts `--lookback-days` (default 730), client-side dedup on `accession_number`. Should resume from latest accession per ticker on re-runs.
- [ ] New `backend/src/features/eight_k.py` with `EIGHT_K_FEATURE_COLS`. Minimum feature set:
  - `eight_k_count_7d` (count of 8-Ks in last 7 days)
  - `eight_k_count_30d` (count in last 30 days)
  - `eight_k_filing_rate_zscore_90d` (count_30d standardized against trailing 90d distribution)
  - `days_since_last_8k` (capped at 180, -1 if never)
  - `has_acquisition_30d` (binary: any 2.01 in last 30d)
  - `has_exec_departure_30d` (binary: any 5.02 in last 30d)
  - `has_restatement_30d` (binary: any 4.02 in last 30d)
  - `has_material_agreement_30d` (binary: any 1.01 in last 30d)
  - `has_bankruptcy_30d` (binary: any 1.03 in last 30d — extremely rare but high-signal)
  - `has_guidance_30d` (binary: any 7.01/2.02 with FD in last 30d)
- [ ] Sane defaults for tickers with no recent activity (counts=0, zscore=0, days_since=-1, all binaries=0).
- [ ] Scheduler: extend `job_fetch_insider_transactions` from P10-005 OR add a sibling `job_fetch_8k_filings` running daily at 6:55 ET (right after insider, before sentiment). Sharing one job is preferable since they hit the same EDGAR endpoint per CIK — fetch the submissions JSON once, parse out both Form 4 and 8-K rows.
- [ ] Wire into `models/directional.py`, `pipeline/scheduler.py` recommendation feature dict, `scripts/diagnose_recs_v2.py`.
- [ ] New `scripts/train_directional_v6.py` mirroring v5 — appends `EIGHT_K_FEATURE_COLS` to FEATURE_COLS, writes `directional_xgb_v6.pkl`. Promotion gate: AUC ≥ current production + 0.005.
- [ ] Tests: `test_eight_k_fetcher.py` (mocked HTTP, item-code extraction across the 4-5 most common item types, amendment handling, dedup), `test_eight_k_features.py` (count windows, zscore for both quiet and active filers, item-flag boundaries, days-since cap, default path).

## Files to Create/Modify

- `backend/alembic/versions/<new>_add_eight_k_filings.py` (new)
- `backend/src/db/models.py` (add `EightKFiling`)
- `backend/src/pipeline/eight_k_fetcher.py` (new)
- `backend/src/features/eight_k.py` (new)
- `backend/src/models/directional.py` (extend)
- `backend/src/pipeline/scheduler.py` (extend EDGAR job to fetch 8-K alongside Form 4)
- `backend/scripts/backfill_eight_k_filings.py` (new)
- `backend/scripts/train_directional_v6.py` (new)
- `backend/scripts/diagnose_recs_v2.py` (extend)
- `backend/tests/test_eight_k_fetcher.py` (new)
- `backend/tests/test_eight_k_features.py` (new)

## Notes

- **EDGAR Item Code reference**: full list at https://www.sec.gov/fast-answers/answersform8khtm.html. The 10 codes covered here capture ~85% of high-signal 8-K traffic; the rest (5.07 voting results, 8.01 other events, 9.01 financial exhibits) are mostly noise.
- **Item-code extraction reliability**: `primaryDocDescription` is sometimes a free-text string ("Material Agreement; Departure of Officer") rather than a clean code list. The robust path is to fetch each filing's `index.json` (cheap, ~2KB) and parse the structured `items` array. Add this to the per-filing fetch with a 0.2s delay between calls.
- **8-K density**: typical S&P 500 ticker files 5-15 8-Ks/year. Across 159 watchlist tickers × 2 years = ~3-5k rows on backfill. Sparser than insider but with much higher per-row signal because each row is a discrete event, not a routine transaction.
- **Filing-rate zscore caveat**: trailing 90d window includes the current 30d window, so the zscore is mildly autocorrelated. If feature importance comes back high but sign is unstable, switch to a rolling 90d baseline that excludes the most recent 30d (`mean_60d_to_90d_ago`).
- **Amendments (8-K/A)**: don't double-count — exclude amendments from filing counts but keep them in the table for forensic queries. Add `is_amendment` filter in `_compute()`.
- **Earnings overlap**: Item 2.02 (earnings results) often clusters around scheduled earnings dates — this is partially redundant with `earnings_within_3d` from P9-005. Expect XGBoost to assign low importance to `has_guidance_30d` if `days_to_earnings` is already in the model. Don't drop it preemptively; let the model decide.
- **No 8-K NLP in this ticket**: filing-body sentiment / topic modeling could be a follow-up if item-code features prove insufficient, but the literature is mixed on whether body-text analysis adds anything beyond the structured codes for short-horizon predictions.
