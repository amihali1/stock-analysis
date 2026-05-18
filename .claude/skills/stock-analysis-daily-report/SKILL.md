---
name: stock-analysis-daily-report
description: >
  Generate a morning status report on the stock-analysis project: scheduler runs, errors in
  logs, ML and sentiment pipeline health, today's recommendations, paper trades, Alpaca order
  state, and infrastructure/resource health. Trigger when the user says "morning report",
  "stock-analysis daily report", "did stock-analysis run today", "is stock-analysis healthy",
  "how did stock-analysis run", "any errors in stock-analysis", or invokes
  /stock-analysis-daily-report. Auto-trigger on any morning question about the project's
  overnight/early-morning state.
---

Run every step. Report each section in order. Terse, structured. One-line summary per section, drill down only on `WARN`/`ERROR`.

## Layout cheatsheet (verified via `vm_runtime_layout.md`)

- VM: `proxmox@10.0.0.47`
- Containers: `backend-backend-1` (FastAPI :8000), `backend-frontend-1` (Next.js :3100), `backend-postgres-1` (PG :5432)
- DB: `stock_analysis`, user `stockuser` / pass `stockpass`
- Repo on VM: `/opt/stock-analysis`
- PG query: `docker exec backend-postgres-1 psql -U stockuser -d stock_analysis -c "..."`
- Times in logs are UTC; ET = UTC - 4 (EDT) or - 5 (EST)
- Today's recommendations job runs Mon-Fri 07:30 ET (= 11:30 UTC EDT / 12:30 UTC EST)

## Section 1 — Scheduler run summary

```bash
curl -s --max-time 8 http://10.0.0.47:8000/api/health
```

Parse `scheduler_jobs` map. For each job, extract status + timestamp. Required jobs on a weekday:
- `fetch_wikipedia_pageviews` — 05:30 ET daily
- `fetch_prices` — 06:00 ET Mon-Fri
- `compute_indicators` — 06:30 ET
- `fetch_options` — 06:45 ET
- `fetch_insider_transactions` — 06:50 ET
- `sentiment` — 07:00 ET (note: takes 2-3h, may still be running, see `sentiment_job_slow_2026-05-18.md`)
- `recommendations` — 07:30 ET
- `execute_recommendations` — 08:00 ET
- `fetch_earnings` — Sundays only
- `portfolio_sync` — every 5 min during market hours
- `retrain_models` — first Sunday of month 02:00 ET

Report format:
```
Scheduler: <OK | WARN: N jobs missing | ERROR: N jobs errored>
  - <job>: <status> at <timestamp>  (only if not "ok" or if timestamp old)
```

Flag job as missing if it should have run by now but is absent from `scheduler_jobs`. Flag as stale if its timestamp is from a previous day on a day it should have run.

Recommendations job parse: the status string contains rec counts in form `"ok (N recs [Bb/BB], C candidates...)"`. Extract `N`, `B` (bear), `B` (bull). Always surface these.

### Section 1b — DB cross-check (REQUIRED when health map looks empty/incomplete)

`scheduler_jobs` is in-memory and wiped by any backend rebuild/restart — see `last_job_runs_in_memory_wipe.md`. Whenever a job is absent from `scheduler_jobs` on a day it should have run, verify against persistent evidence BEFORE flagging it missing. Container uptime indicates whether a wipe likely occurred: `docker ps --format '{{.Names}}\t{{.Status}}' | grep backend-backend-1` — if "Up <N> minutes" or "Up <N> hours" with N less than the time since the earliest scheduled job, the in-memory state is incomplete and DB checks below are authoritative.

```bash
ssh proxmox@10.0.0.47 "docker exec backend-postgres-1 psql -U stockuser -d stock_analysis -c \"
  SELECT 'recommendations' AS job, (SELECT COUNT(*) FROM recommendations WHERE date = CURRENT_DATE) AS today_rows, (SELECT MAX(created_at) FROM recommendations WHERE date = CURRENT_DATE) AS latest
  UNION ALL
  SELECT 'sentiment', (SELECT COUNT(*) FROM sentiment_scores WHERE created_at::date = CURRENT_DATE), (SELECT MAX(created_at) FROM sentiment_scores WHERE created_at::date = CURRENT_DATE)
  UNION ALL
  SELECT 'fetch_prices', NULL, (SELECT MAX(date)::timestamp FROM price_history)
  UNION ALL
  SELECT 'compute_indicators', NULL, (SELECT MAX(date)::timestamp FROM technical_indicators)
  UNION ALL
  SELECT 'fetch_wikipedia_pageviews', NULL, (SELECT MAX(date)::timestamp FROM wikipedia_pageviews);
\""
```

(Table names: verify against `\dt` if any query errors — schemas drift.)

If DB confirms the job ran (today's rows present or MAX(date) = today), do NOT flag as missing. Report it as "ran (verified via DB)" with the DB timestamp instead of the lost in-memory one.

Also pull docker logs for completion lines if container was NOT recreated since midnight:
```bash
ssh proxmox@10.0.0.47 "docker logs backend-backend-1 --since=\$(date -u -d 'today 00:00 UTC' +%Y-%m-%dT%H:%M:%S) 2>&1 | grep -E 'Scheduler: .* complete' | head -20"
```

## Section 2 — Errors in backend logs since midnight ET

```bash
ssh proxmox@10.0.0.47 "docker logs backend-backend-1 --since=\$(date -u +%Y-%m-%dT04:00:00) 2>&1 | grep -iE 'ERROR|Traceback|Exception' | grep -vE 'Could not extract JSON|delisted' | head -40"
```

Note: `Could not extract JSON` from Ollama is a known soft warning, not a failure. `delisted` lines from yfinance are routine. Suppress them.

Report format:
```
Log errors: <NONE | N findings>
  <error line> (×N occurrences)
```

If errors found, fetch full traceback for the most recent and include in the report.

## Section 3 — Recommendations produced today

```bash
ssh proxmox@10.0.0.47 "docker exec backend-postgres-1 psql -U stockuser -d stock_analysis -c \"
  SELECT direction, strategy, COUNT(*) AS n,
         ROUND(AVG(score)::numeric, 3) AS avg_score,
         ROUND(AVG(max_loss)::numeric, 2) AS avg_max_loss
  FROM recommendations
  WHERE date = CURRENT_DATE
  GROUP BY direction, strategy
  ORDER BY direction, n DESC;
\""
```

Top picks:
```bash
ssh proxmox@10.0.0.47 "docker exec backend-postgres-1 psql -U stockuser -d stock_analysis -c \"
  SELECT ticker, direction, strategy, ROUND(score::numeric, 3) AS score,
         ROUND(directional_signal::numeric, 3) AS dir,
         ROUND(sentiment_signal::numeric, 3) AS sent,
         entry_price, ROUND(max_loss::numeric, 2) AS max_loss
  FROM recommendations
  WHERE date = CURRENT_DATE
  ORDER BY score DESC
  LIMIT 10;
\""
```

Report format:
```
Recommendations: <N total | M bull, M bear>
  Strategies: <breakdown>
  Top picks: <ticker (dir, strategy, score)> ...
```

Flag if:
- 0 recommendations on a weekday (likely pipeline failure)
- One-sided (only bull or only bear) — note as `regime shutout`
- Average score unusually low (<0.55) — model in dead zone

## Section 4 — Paper trades / Alpaca activity

```bash
ssh proxmox@10.0.0.47 "docker exec backend-postgres-1 psql -U stockuser -d stock_analysis -c \"
  SELECT 'paper_trades_today' AS source, COUNT(*) AS n
  FROM paper_trades WHERE opened_at::date = CURRENT_DATE
  UNION ALL
  SELECT 'paper_trades_open_now', COUNT(*) FROM paper_trades WHERE status = 'open'
  UNION ALL
  SELECT 'alpaca_orders_today', COUNT(*) FROM alpaca_orders WHERE submitted_at::date = CURRENT_DATE
  UNION ALL
  SELECT 'alpaca_positions_open', COUNT(*) FROM alpaca_positions;
\""
```

Detail today's orders if any:
```bash
ssh proxmox@10.0.0.47 "docker exec backend-postgres-1 psql -U stockuser -d stock_analysis -c \"
  SELECT ticker, side, qty, order_type, status, filled_price, submitted_at, filled_at
  FROM alpaca_orders
  WHERE submitted_at::date = CURRENT_DATE
  ORDER BY submitted_at DESC;
\""
```

Check Alpaca trading flag:
```bash
ssh proxmox@10.0.0.47 "grep ALPACA_TRADING_ENABLED /opt/stock-analysis/backend/.env"
```

Report format:
```
Paper trading: <ENABLED | DISABLED>
  Today: <N paper trades opened, N Alpaca orders>
  Open: <N paper trades open, N Alpaca positions>
  P&L (closed today): $X (if any closed)
```

If `ALPACA_TRADING_ENABLED=false` but `execute_recommendations` ran, that's expected (skipped path).

## Section 5 — ML pipeline health

Verify trained model files present + current versions:
```bash
ssh proxmox@10.0.0.47 "docker exec backend-backend-1 ls -la /app/trained_models/ 2>&1 | grep -E 'directional|volatility'"
```

Pull a quick prediction to confirm inference works (uses watchlist's first ticker):
```bash
ssh proxmox@10.0.0.47 "docker exec backend-backend-1 python -c \"
from src.models.directional import DirectionalModel
from src.models.volatility import VolatilityModel
d = DirectionalModel(direction='drop'); d.load(); print('drop OK')
r = DirectionalModel(direction='rise'); r.load(); print('rise OK')
v = VolatilityModel(); v.load(); print('vol OK')
\""
```

Report format:
```
ML models: <OK | WARN | ERROR>
  drop=<version> rise=<version> vol=<version>
  Last retrain: <date from session log or model metadata>
```

## Section 6 — Sentiment pipeline health

Was sentiment scored for at least 50% of watchlist today?
```bash
ssh proxmox@10.0.0.47 "docker exec backend-postgres-1 psql -U stockuser -d stock_analysis -c \"
  SELECT COUNT(DISTINCT ticker) AS scored_today,
         ROUND(AVG(sentiment)::numeric, 3) AS avg_sentiment,
         ROUND(AVG(confidence)::numeric, 3) AS avg_conf,
         COUNT(*) FILTER (WHERE confidence < 0.5) AS low_conf_rows
  FROM sentiment_scores
  WHERE created_at::date = CURRENT_DATE;
\""
```

Confirm Ollama responding:
```bash
curl -s --max-time 5 http://10.0.0.47:11434/api/tags | head -100
```

Report format:
```
Sentiment: <OK | RUNNING | STALE | ERROR>
  Today: N tickers scored, avg sent=X, avg conf=Y, M low-conf
  Ollama: <model loaded>
```

Flag as `STALE` if scored_today < (watchlist_size / 2) AND the sentiment job has supposedly completed for today. If `recommendations` already ran but sentiment is incomplete, surface that recs used stale sentiment (per `sentiment_job_slow_2026-05-18.md`).

## Section 7 — Infrastructure / resource health

```bash
ssh proxmox@10.0.0.47 "docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'backend|ollama'; df -h / /opt 2>&1 | tail -3; nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>&1 | head -3"
```

Report format:
```
Containers: <all up | N down>
Disk: <% used on / and /opt — flag if >85%>
GPU: <util, mem used / mem total>
```

## Section 8 — Recent commits / branch state

```bash
cd C:\\Dev\\stock-analysis && git log --oneline -5 && git status --short
```

Report format:
```
Repo: <N commits ahead of origin/master>
  Recent: <last 5 commit subjects>
  Working tree: <clean | N uncommitted | N untracked>
```

## Final report shape

Compose all sections as a single markdown report. Lead with one-line "overall" summary at top:

```
# Stock-Analysis Daily Report — <today's date in ET>

Overall: <ALL OK | N WARN | N ERROR>

## Scheduler
<section 1>

## Log errors
<section 2>

## Recommendations
<section 3>

## Paper trading / Alpaca
<section 4>

## ML pipeline
<section 5>

## Sentiment
<section 6>

## Infrastructure
<section 7>

## Repo
<section 8>
```

Caveman mode: report bodies stay caveman-terse, but section headers and table data stay normal markdown.

## Memory integration

Before running, read these memory files (if relevant findings exist) to interpret results:
- `C:\AgentMemory\projects\stock-analysis\sentiment_job_slow_2026-05-18.md` — sentiment runtime expectations
- `C:\AgentMemory\projects\stock-analysis\wikipedia_titles_package_data_2026-05-18.md` — recent wiki bug
- `C:\AgentMemory\projects\stock-analysis\vm_runtime_layout.md` — container names + DB
- `C:\AgentMemory\projects\stock-analysis\scheduler_async_silent_failure.md` — async-job silent no-op signs
- `C:\AgentMemory\projects\stock-analysis\paper_trading_readiness.md` — paper-trade gates
- `C:\AgentMemory\projects\stock-analysis\last_job_runs_in_memory_wipe.md` — health endpoint loses state on restart, MUST cross-check via DB
- `C:\AgentMemory\projects\homelab-monitoring\ollama_gpu_loss.md` — Ollama GPU drop pattern

After running, save any new failure pattern to a new memory file under `C:\AgentMemory\projects\stock-analysis\` (only if non-obvious and worth persisting per the autonomous-save rules in global CLAUDE.md).

## What this skill does NOT do

- Does not fix issues — only reports. User decides next steps.
- Does not write commits or restart services.
- Does not modify recommendations or trades.
- Does not page on errors — just surface them in the report.
