"""Prometheus metrics for pipeline health.

Default HTTP metrics (request count, latency, in-progress) are provided by
`prometheus-fastapi-instrumentator` and installed in main.py.

Custom pipeline metrics live here and are incremented from scheduler job bodies.
"""

from prometheus_client import Counter, Gauge

pipeline_prices_fetched_total = Counter(
    "pipeline_prices_fetched_total",
    "Total price rows fetched across all fetch_prices runs",
)

pipeline_indicators_computed_total = Counter(
    "pipeline_indicators_computed_total",
    "Total indicator rows computed across all compute_indicators runs",
)

pipeline_recommendations_generated_total = Counter(
    "pipeline_recommendations_generated_total",
    "Total recommendations persisted across all generate_recommendations runs",
    ["direction"],
)

pipeline_sentiment_runs_total = Counter(
    "pipeline_sentiment_runs_total",
    "Total sentiment jobs executed (labels track outcome)",
    ["status"],
)

pipeline_last_run_timestamp = Gauge(
    "pipeline_last_run_timestamp",
    "Unix timestamp of last completion per pipeline job",
    ["job", "status"],
)

pipeline_job_errors_total = Counter(
    "pipeline_job_errors_total",
    "Total scheduler job invocations that ended in error",
    ["job"],
)

# Paper-trading validation scoreboard (weekly job_paper_validation).
# These are THE live-readiness numbers — Grafana panels read them directly.
paper_validation_win_rate = Gauge(
    "paper_validation_win_rate",
    "Win rate of closed paper trades in the last validation window",
)

paper_validation_total_pnl = Gauge(
    "paper_validation_total_pnl",
    "Total realized P&L (dollars) of closed paper trades in the last validation window",
)

paper_validation_num_trades = Gauge(
    "paper_validation_num_trades",
    "Number of closed paper trades in the last validation window",
)
