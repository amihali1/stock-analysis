from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "sqlite:///./stock_analysis.db"

    # Ollama
    ollama_base_url: str = "http://10.0.0.47:11434"
    ollama_model: str = "mistral"
    ollama_timeout: int = 30

    # API Keys (optional, loaded from .env)
    fred_api_key: str = ""

    # Sentiment pipeline
    # Skip headlines older than this many days before sending to Ollama.
    # Stale news has no bearing on a 5-day directional prediction and just
    # burns LLM time. Default 7d (~one news cycle); empirically about 60% of
    # Finviz headlines fall within this window.
    sentiment_max_headline_age_days: int = 7
    # Max concurrent Ollama calls *per ticker* inside analyze_ticker. Combined
    # with analyze_all's ticker-level concurrency (default 3), Ollama sees up
    # to ticker_concurrency * headline_concurrency in-flight requests. With
    # OLLAMA_NUM_PARALLEL=1 on the host, Ollama still serializes internally
    # but the asyncio overlap removes Python-side dead time between calls.
    # If OLLAMA_NUM_PARALLEL is bumped on the homelab, this is the dial that
    # actually exploits the extra slots.
    sentiment_headline_concurrency: int = 4

    # Trading constraints
    # Absolute per-trade cap. Used when `max_position_ratio` is 0 (legacy mode).
    # When ratio > 0, `effective_per_trade_cap` overrides this to keep per-trade
    # auto-scaled to the daily pool — important for live trading at smaller
    # capital ($1k pool ÷ 5 = $200/trade, not stuck at $1k from the default).
    max_position_size: float = 1000.0
    # Per-trade cap as a fraction of `daily_capital_cap`. 0 disables (use
    # max_position_size directly). 0.20 = up to 5 concurrent positions per day.
    # Live $1k starter config: cap=1000, ratio=0.25 → $250/trade. Paper $5k
    # current config: cap=5000, ratio=0.0 → $1000/trade (preserves today's
    # behavior — set ratio=0.2 to make it derived instead).
    max_position_ratio: float = 0.0
    # Total daily capital cap across ALL recommendations, direction-blind.
    # Bull and bear share one pool — the long-term aim is max profit-potential,
    # not balanced hedging (bullish_side_build memo, 2026-05-12). At ~$1k/trade
    # and $25k aggregate, we get up to 25 concurrent positions before the cap
    # kicks in. Paper mode uses 25k to give the pipeline headroom over the
    # accumulated open-position deduction; live $1k mode overrides via env.
    # Capital consumed per rec is `max(position_size, max_loss)` so credit
    # spreads count collateral (max_loss), not credit received.
    daily_capital_cap: float = 25000.0
    # Drop watchlist tickers whose latest close > this threshold BEFORE running
    # ML. Default 0.0 = disabled. At $250/trade, a $500 stock can't fit a long
    # share or short (with 1.5x margin), so its ranked slot is wasted on a
    # no-sizer-match. Live $1k mode should set this near `effective_per_trade_cap`
    # (e.g. 250) — paper mode leaves it off. Caveat: blanket skip also drops
    # spread/option setups on expensive tickers, where the sizer might have
    # succeeded; acceptable trade-off when top-K slots are scarce.
    max_ticker_price: float = 0.0
    # Enable fractional-share orders for long stock when whole-share qty
    # rounds down to 0 (price > per-trade cap). Alpaca supports fractional
    # for long positions only, and ONLY as market orders without brackets —
    # so stop-loss/take-profit are dropped and execution becomes market-on-
    # submit. Default off. Turn on for $1k live so a $400 stock at $250/trade
    # can still be sized at ~0.6 shares. Min notional $1 (Alpaca constraint).
    enable_fractional_shares: bool = False
    min_confidence: float = 0.75  # Deprecated — kept for back-compat; see min_directional_lift / min_sentiment_confidence below

    # Alpaca
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"  # Paper trading default
    alpaca_trading_enabled: bool = False

    # Trading safety rails
    trading_mode: str = "disabled"  # disabled, paper, live
    max_daily_loss: float = 200.0
    max_open_positions: int = 5
    max_daily_orders: int = 20
    allowed_hours_only: bool = True
    blocked_tickers: list[str] = []
    auto_execute_enabled: bool = False
    min_score_threshold: float = 0.7

    # Auth
    jwt_secret: str = "change-me-in-production"
    jwt_access_expire_minutes: int = 1440  # 24 hours
    jwt_refresh_expire_days: int = 30
    default_admin_username: str = "admin"
    default_admin_password: str = "admin"

    # Phase 9 — feature flags
    skip_near_earnings: bool = False  # If true, scheduler filters out earnings_within_3d=True

    # Recommendation gating (replaces hardcoded score < 0.5 gate that produced zero recs
    # on a calibrated rare-event classifier — composite score now ranks, dir_prob gates)
    directional_base_rate: float = 0.175  # P(label=1) on training set; "drop > 3% in 5d"
    # lift=1.3 → 0.2275 floor. Empirically (v3, May 2026) catches the watchlist's
    # top 2-4 most-bearish picks per day. Bump to 1.5 (0.2625) for stricter quality
    # at the cost of zero recs on flat markets; drop to 1.0 for any-above-baseline.
    min_dir_prob_lift: float = 1.3
    # Max recs emitted PER DIRECTION (drop/rise); 5 → up to 10 total.
    # Lowered 10 → 5 on 2026-07-07: money-layer sweep showed K=3-5 carries the
    # best per-trade expectancy (rise +1.59%/1.50% at K=3/5 vs +1.44% at K=10).
    recommendations_top_k: int = 5

    # Bear-side pair trading (money_layer + bear_monetization sweeps, 2026-07-07):
    # bear picks carry relative alpha (-0.54%/10d vs universe) but naked shorts
    # and credit spreads lose absolutely in a rising tape. Short pick + equal-$
    # long hedge = +0.47%/10d market-neutral. When enabled, bear recs route to
    # strategy "pair_short" instead of the spread/options/short cascade.
    enable_pair_short: bool = True
    pair_hedge_symbol: str = "SPY"

    # Time-based exit for stock strategies (long/short/pair_short): close after
    # N trading sessions. Hold=10 dominated every shorter hold in the sweep
    # (rise +1.59% at H=10 vs +0.43% at H=5).
    time_exit_sessions: int = 10
    # Spread-vs-options routing in SpreadBuilder. The legacy gates
    # (`directional_signal > 0.6` / `score >= 0.5`) were written when the
    # directional model emitted uncalibrated probs and dir_prob could swing
    # 0.0-0.9. Post sigmoid calibration drop_prob clusters at base rate 0.05
    # (range ~0.04-0.10) and rise_prob at base rate 0.175 (range ~0.18-0.27),
    # so the absolute gates were structurally unreachable — 0 bull_spreads
    # ever emitted, and bear spreads only emitted via the unrelated
    # `vol_signal > 0.6 + dir_signal < 0.4` iron-condor branch.
    # Replacement: per-direction *relative* lift thresholds + a calibrated
    # composite-score floor that matches the actual production score range.
    drop_base_rate: float = 0.05  # vol-normalized drop label (v7) pos_rate
    # rise_base_rate reuses `directional_base_rate` above (0.175).
    spread_directional_lift: float = 1.3  # multiplier on direction base-rate
    spread_min_score: float = 0.30  # composite-score fallback, calibrated to today's range
    # Absolute composite-score floor applied by rec_ranker.select_candidates before
    # the top-K cap. The 2026-05-14 joint backtest at top_k=10 with no floor had
    # mean hit rate 25-30% (vs 60% break-even at -1.5/+1.0 payoffs) because slots
    # got filled with low-conviction picks on flat days. Default 0.0 = no filter
    # (preserves pre-fix behavior). Sweep backtest at 0.55 / 0.60 / 0.65 to pick.
    recommendations_min_score: float = 0.0

    # P10-004 — replaces the structurally-unreachable absolute `min_confidence=0.75`
    # gate (which used abs(prob-0.5)*2 — bounded ~0.48 for our calibrated rare-event
    # dir_prob range of [0.10, 0.27]). Now: directional confidence is gated upstream
    # by min_dir_prob_lift (rec_ranker.py); the per-rec gate uses a *relative*
    # bearish-lift floor as a safety net when callers bypass the ranker.
    # Formula: directional_lift = max(0, dir_prob - base_rate) / (1 - base_rate)
    # 0.05 ≈ dir_prob >= 0.216 (slightly looser than the ranker's 0.2275 default).
    min_directional_lift: float = 0.05
    # Sentiment confidence is LLM-self-reported per article and clusters around
    # 0.80 in production (qwen3.5:9b). Floor 0.40 filters genuinely-noisy results
    # without unreachability problems.
    min_sentiment_confidence: float = 0.40

    # Watchlist
    # Expanded May 2026 from ~50 mega-caps to ~150 names. Mega-caps alone are
    # too stable to often cross the >3%-drop-in-5d label, leaving the rare-event
    # classifier with too few above-floor candidates per day. Bias of additions
    # is toward higher-beta names (semis, biotech, retail, REITs, EV, fintech).
    default_watchlist: list[str] = [
        # --- Mega-cap tech (existing) ---
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "INTC", "CRM",
        # --- Mega-cap finance (existing) ---
        "JPM", "BAC", "GS", "MS", "WFC", "C", "BLK", "SCHW",
        # --- Mega-cap healthcare (existing) ---
        "JNJ", "UNH", "PFE", "ABBV", "MRK", "LLY", "BMY",
        # --- Mega-cap consumer (existing) ---
        "WMT", "COST", "HD", "NKE", "SBUX", "MCD", "DIS",
        # --- Energy (existing) ---
        "XOM", "CVX", "COP", "SLB", "EOG",
        # --- Industrial (existing) ---
        "CAT", "BA", "HON", "GE", "MMM",

        # --- Tech / semis (expansion) ---
        "TSM", "AVGO", "QCOM", "TXN", "AMAT", "MU", "ASML", "LRCX", "KLAC",
        "ADBE", "ORCL", "IBM", "CSCO", "NOW", "PANW",
        # --- Software / internet (expansion) ---
        "NFLX", "PYPL", "SHOP", "SNAP", "PINS", "ROKU", "SPOT", "UBER", "LYFT",
        # SQ removed 2026-07-02: Block renamed its ticker to XYZ in Jan 2025,
        # Yahoo stopped serving SQ ("possibly delisted" on every fetch).
        # XYZ (Block) added 2026-07-07 to restore fintech coverage.
        "ZM", "DOCU", "XYZ",
        # --- Communication services (expansion) ---
        "T", "VZ", "CMCSA", "TMUS", "CHTR",
        # --- Consumer discretionary (expansion) ---
        "TGT", "LOW", "TJX", "ROST", "BBY", "GM", "F", "EBAY", "ETSY", "ABNB",
        "BKNG", "MAR",
        # --- High-volatility fintech / EV (expansion) ---
        "COIN", "HOOD", "SOFI", "RIVN",
        # --- Consumer staples (expansion) ---
        "PG", "KO", "PEP", "MO", "PM", "KHC",
        # --- Healthcare / biotech (expansion) ---
        "ABT", "TMO", "DHR", "MRNA", "BIIB", "REGN", "VRTX", "GILD", "AMGN", "CVS",
        # --- Financials / payments (expansion) ---
        "BRK-B", "USB", "PNC", "TFC", "HBAN", "AIG", "PRU", "MET", "ALL", "V", "MA",
        # --- Industrials (expansion) ---
        "UPS", "FDX", "RTX", "LMT", "NOC", "GD", "DE", "EMR",
        # --- Energy (expansion) ---
        "HAL", "OXY", "MPC", "PSX", "VLO",
        # --- Materials (expansion) ---
        "FCX", "NEM", "LIN", "APD",
        # --- REITs (expansion) ---
        "PLD", "AMT", "EQIX", "CCI", "O", "SPG",
        # --- Homebuilders (expansion) ---
        "DHI", "LEN", "NVR", "PHM",

        # --- Macro context (existing) ---
        "SPY", "QQQ", "IWM", "^VIX",
        # --- Sector ETFs (P9-003, existing) ---
        "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLC", "XLRE",
    ]

    @property
    def effective_per_trade_cap(self) -> float:
        """Per-trade dollar cap, derived from cap × ratio when ratio > 0.

        Set ratio=0 (default) to use max_position_size verbatim — preserves
        legacy behavior. Set ratio>0 to auto-scale per-trade with the daily
        pool: cap=$1000, ratio=0.25 → $250/trade. Avoids the trap of dropping
        cap to $1k for live trading and leaving per-trade at $1k (one
        position fills the entire pool).
        """
        if self.max_position_ratio > 0:
            return self.daily_capital_cap * self.max_position_ratio
        return self.max_position_size


# Non-equity tickers on the watchlist (macro context + sector ETFs + indexes).
# They have no earnings, no fundamentals, no insider filings — data fetchers
# that only make sense for operating companies should skip them instead of
# burning a 404 per ticker per run (earnings fetch logged 11 ETF 404s every
# Sunday before this existed).
ETF_TICKERS: frozenset[str] = frozenset({
    "SPY", "QQQ", "IWM", "^VIX",
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLC", "XLRE",
})

# Static ticker → sector-ETF map (P9-003). Tickers not listed default to SPY.
SECTOR_ETF_MAP: dict[str, str] = {
    # Tech → XLK
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AMD": "XLK", "INTC": "XLK", "CRM": "XLK",
    "TSM": "XLK", "AVGO": "XLK", "QCOM": "XLK", "TXN": "XLK", "AMAT": "XLK", "MU": "XLK",
    "ASML": "XLK", "LRCX": "XLK", "KLAC": "XLK", "ADBE": "XLK", "ORCL": "XLK", "IBM": "XLK",
    "CSCO": "XLK", "NOW": "XLK", "PANW": "XLK", "ZM": "XLK", "DOCU": "XLK",
    # Communication services → XLC
    "GOOGL": "XLC", "META": "XLC", "DIS": "XLC", "NFLX": "XLC", "T": "XLC", "VZ": "XLC",
    "CMCSA": "XLC", "TMUS": "XLC", "CHTR": "XLC", "SNAP": "XLC", "PINS": "XLC",
    "ROKU": "XLC", "SPOT": "XLC",
    # Consumer discretionary → XLY
    "AMZN": "XLY", "TSLA": "XLY", "HD": "XLY", "NKE": "XLY", "SBUX": "XLY", "MCD": "XLY",
    "TGT": "XLY", "LOW": "XLY", "TJX": "XLY", "ROST": "XLY", "BBY": "XLY", "GM": "XLY",
    "F": "XLY", "EBAY": "XLY", "ETSY": "XLY", "ABNB": "XLY", "BKNG": "XLY", "MAR": "XLY",
    "RIVN": "XLY", "UBER": "XLY", "LYFT": "XLY", "DHI": "XLY", "LEN": "XLY", "NVR": "XLY",
    "PHM": "XLY",
    # Consumer staples → XLP
    "WMT": "XLP", "COST": "XLP", "PG": "XLP", "KO": "XLP", "PEP": "XLP", "MO": "XLP",
    "PM": "XLP", "KHC": "XLP",
    # Financials → XLF (incl. payments / fintech)
    "JPM": "XLF", "BAC": "XLF", "GS": "XLF", "MS": "XLF", "WFC": "XLF", "C": "XLF",
    "BLK": "XLF", "SCHW": "XLF", "BRK-B": "XLF", "USB": "XLF", "PNC": "XLF", "TFC": "XLF",
    "HBAN": "XLF", "AIG": "XLF", "PRU": "XLF", "MET": "XLF", "ALL": "XLF", "V": "XLF",
    "MA": "XLF", "PYPL": "XLF", "SQ": "XLF", "XYZ": "XLF", "SHOP": "XLF", "COIN": "XLF",
    "HOOD": "XLF", "SOFI": "XLF",
    # Healthcare → XLV
    "JNJ": "XLV", "UNH": "XLV", "PFE": "XLV", "ABBV": "XLV", "MRK": "XLV",
    "LLY": "XLV", "BMY": "XLV", "ABT": "XLV", "TMO": "XLV", "DHR": "XLV", "MRNA": "XLV",
    "BIIB": "XLV", "REGN": "XLV", "VRTX": "XLV", "GILD": "XLV", "AMGN": "XLV", "CVS": "XLV",
    # Energy → XLE
    "XOM": "XLE", "CVX": "XLE", "COP": "XLE", "SLB": "XLE", "EOG": "XLE",
    "HAL": "XLE", "OXY": "XLE", "MPC": "XLE", "PSX": "XLE", "VLO": "XLE",
    # Industrials → XLI
    "CAT": "XLI", "BA": "XLI", "HON": "XLI", "GE": "XLI", "MMM": "XLI",
    "UPS": "XLI", "FDX": "XLI", "RTX": "XLI", "LMT": "XLI", "NOC": "XLI", "GD": "XLI",
    "DE": "XLI", "EMR": "XLI",
    # Materials → XLB
    "FCX": "XLB", "NEM": "XLB", "LIN": "XLB", "APD": "XLB",
    # Real Estate → XLRE
    "PLD": "XLRE", "AMT": "XLRE", "EQIX": "XLRE", "CCI": "XLRE", "O": "XLRE", "SPG": "XLRE",
}


def sector_etf_for(ticker: str) -> str:
    """Return the sector ETF for a ticker, defaulting to SPY when unknown."""
    return SECTOR_ETF_MAP.get(ticker, "SPY")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
