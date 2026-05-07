# P10-005: Add insider-transaction features (SEC Form 4) to directional model

**Status**: todo
**Phase**: 10
**Dependencies**: P10-001 (analyst-rating features — same shape, reuse the migration/fetcher/feature/wire-through pattern)
**Estimated scope**: medium (6-8 files)

## Description

Per `directional_model_information_ceiling.md`, the v3 model is at AUC 0.5505 because it's information-saturated on price/options/macro/sector/sentiment/earnings/analyst signal. P10-003 (short interest) was the previous attempt at a per-ticker event signal but landed dead on arrival — yfinance only exposes 2 historical points per ticker, so the backfill produced ~2 rows per ticker against a 48k-row training set, all 6 features came in at 0.000 importance, and the v4 retrain regressed AUC by 0.027 (see `short_interest_features_dead_2026-05-07.md`).

Insider transactions (SEC Form 4) are the next per-ticker event signal on the priority list and don't have the density problem: SEC EDGAR exposes the full filing history back to 2003 via a free REST API, with multiple Form 4s per ticker per year for any company with active insiders. Cluster buying (multiple insiders buying within a 30-day window) and the buy/sell ratio over rolling windows are well-documented short-term return predictors in equity literature (Cohen, Malloy & Pomorski 2012; Lakonishok & Lee 2001).

This ticket follows the same shape as P10-001 (analyst-rating features): model + migration + feature module + backfill script + scheduler hook + tests, plus a v5 trainer that bundles it on top of v3.

## Acceptance Criteria

- [ ] New `InsiderTransaction` table + Alembic migration. Columns: `ticker`, `filing_date`, `transaction_date`, `insider_name`, `insider_title`, `transaction_code` (P/S/A/D/G/F/M etc.), `shares`, `price_per_share`, `total_value` ($), `shares_owned_after`, `is_director`, `is_officer`, `is_10pct_owner`, `accession_number` (unique). Unique index on `accession_number` + composite index on `(ticker, transaction_date)`.
- [ ] `InsiderTransactionFetcher` in `backend/src/pipeline/insider_fetcher.py` pulling from SEC EDGAR REST API (`https://data.sec.gov/submissions/CIK{cik}.json` for filing index, `https://www.sec.gov/cgi-bin/browse-edgar` for Form 4 filings, then parse the per-filing XML at `https://www.sec.gov/Archives/edgar/data/{cik}/...`). Must include the SEC-required `User-Agent: <Project name> <email>` header; rate-limit to 10 req/sec per SEC fair-use guidance.
- [ ] CIK lookup: `backend/src/services/sec_cik.py` resolves ticker → CIK using `https://www.sec.gov/files/company_tickers.json` (cache the full mapping daily in DB or local JSON).
- [ ] `backfill_insider_transactions.py` script: idempotent, accepts `--lookback-days` (default 730 = 2 years), client-side dedup on `accession_number` to avoid the P10-001 batch-rollback failure mode.
- [ ] New `backend/src/features/insider.py` with `INSIDER_FEATURE_COLS`. Minimum feature set:
  - `insider_buys_30d` (count of P-coded buys in last 30d)
  - `insider_sells_30d` (count of S-coded sales in last 30d)
  - `insider_net_buy_value_30d` ($ buys − $ sales, last 30d)
  - `insider_net_buy_value_90d` (same, 90d window)
  - `insider_cluster_buy_30d` (binary: ≥3 distinct insiders bought in last 30d)
  - `insider_buy_sell_ratio_90d` (count of P / max(1, count of P+S))
  - `days_since_insider_buy` (capped at 365, -1 if never)
  - `days_since_insider_sell` (same)
  - `pct_insider_ownership_change_90d` (%change in `shares_owned_after` aggregated across insiders)
- [ ] Sane defaults for tickers with no insider activity (counts=0, ratios=0.5, days_since=-1, ownership_change=0).
- [ ] Scheduler: new `job_fetch_insider_transactions` runs daily Mon-Fri at 6:50 ET (after price + options, before sentiment).
- [ ] Wire features into `models/directional.py` `FEATURE_COLS` and `_merged_defaults`; into `pipeline/scheduler.py` recommendation feature dict; into `scripts/diagnose_recs_v2.py`.
- [ ] New `scripts/train_directional_v5.py` mirroring v4 — appends `INSIDER_FEATURE_COLS` to FEATURE_COLS, writes `directional_xgb_v5.pkl`, prints v5 vs v3 comparison + per-feature importance for the new group. Promote to `v1.pkl` only if AUC ≥ v3 + 0.005.
- [ ] Tests: `test_insider_fetcher.py` (mocked HTTP, CIK lookup, Form 4 XML parsing for buy/sell/grant codes, dedup), `test_insider_features.py` (count windows, cluster-buy threshold, ratio edge cases, days-since cap, default path).

## Files to Create/Modify

- `backend/alembic/versions/<new>_add_insider_transactions.py` (new)
- `backend/src/db/models.py` (add `InsiderTransaction`)
- `backend/src/services/sec_cik.py` (new — ticker→CIK resolver)
- `backend/src/pipeline/insider_fetcher.py` (new)
- `backend/src/features/insider.py` (new)
- `backend/src/models/directional.py` (extend feature cols + defaults)
- `backend/src/pipeline/scheduler.py` (new fetch job + inference feature dict)
- `backend/scripts/backfill_insider_transactions.py` (new)
- `backend/scripts/train_directional_v5.py` (new)
- `backend/scripts/diagnose_recs_v2.py` (extend feature dict)
- `backend/tests/test_insider_fetcher.py` (new)
- `backend/tests/test_insider_features.py` (new)

## Notes

- **SEC fair-use compliance**: User-Agent header MUST identify the project and contact email — `User-Agent: stock-analysis andymihalik@gmail.com`. Without it SEC returns 403. Hard rate limit is 10 req/sec; stay well under (5 req/sec) since we do bulk backfill.
- **Form 4 transaction codes**: `P` = open-market purchase (the strongest bullish signal), `S` = open-market sale, `A` = grant/award, `D` = disposition (often tax-related), `G` = gift, `F` = payment of exercise price (tax), `M` = exercise, `J` = other. Filter to P+S only for buy/sell ratio features; counting grants and tax events as "selling" is a known false-signal source.
- **CIK is zero-padded to 10 digits** in EDGAR URLs (e.g. AAPL = 320193 → 0000320193). Cache the ticker→CIK map daily; the SEC company_tickers.json is ~1.5 MB.
- **Density estimate**: typical S&P 500 ticker has 50-200 Form 4 filings/year. Across 159 watchlist tickers + 2-year lookback, expect 30k-50k rows on backfill — orders of magnitude denser than short interest's 286 rows.
- **Cluster-buy signal**: 3+ distinct insiders buying within 30 days has been shown in literature to predict 6-12% positive abnormal returns over the following 6 months. We're predicting 5-day direction (and our target is bearish), so this feature reverses sign for our use: cluster buys should *reduce* `dir_prob`. Don't preprocess the sign — let XGBoost learn it.
- **Backfill order**: run `sec_cik` cache populate first, then backfill. Script should resume from the latest existing `accession_number` per ticker so re-runs are cheap.
- After v5 trains, expect to also tune `min_dir_prob_lift` again — adding a real signal will shift the dir_prob distribution and the current 1.3 floor may drop too few or too many candidates.
