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
    newsapi_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "stock-analysis/0.1"

    # Trading constraints
    max_position_size: float = 1000.0
    min_confidence: float = 0.75  # Minimum per-model confidence to generate a recommendation

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
    recommendations_top_k: int = 10  # max recs emitted per scheduler run, ranked by score

    # Watchlist
    default_watchlist: list[str] = [
        # Tech
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "INTC", "CRM",
        # Finance
        "JPM", "BAC", "GS", "MS", "WFC", "C", "BLK", "SCHW",
        # Healthcare
        "JNJ", "UNH", "PFE", "ABBV", "MRK", "LLY", "BMY",
        # Consumer
        "WMT", "COST", "HD", "NKE", "SBUX", "MCD", "DIS",
        # Energy
        "XOM", "CVX", "COP", "SLB", "EOG",
        # Industrial
        "CAT", "BA", "HON", "GE", "MMM",
        # Macro context
        "SPY", "QQQ", "IWM", "^VIX",
        # Sector ETFs (P9-003)
        "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLC", "XLRE",
    ]


# Static ticker → sector-ETF map (P9-003). Tickers not listed default to SPY.
SECTOR_ETF_MAP: dict[str, str] = {
    # Tech → XLK
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AMD": "XLK", "INTC": "XLK", "CRM": "XLK",
    # Communication services → XLC
    "GOOGL": "XLC", "META": "XLC", "DIS": "XLC",
    # Consumer discretionary → XLY
    "AMZN": "XLY", "TSLA": "XLY", "HD": "XLY", "NKE": "XLY", "SBUX": "XLY", "MCD": "XLY",
    # Consumer staples → XLP
    "WMT": "XLP", "COST": "XLP",
    # Financials → XLF
    "JPM": "XLF", "BAC": "XLF", "GS": "XLF", "MS": "XLF", "WFC": "XLF", "C": "XLF",
    "BLK": "XLF", "SCHW": "XLF",
    # Healthcare → XLV
    "JNJ": "XLV", "UNH": "XLV", "PFE": "XLV", "ABBV": "XLV", "MRK": "XLV",
    "LLY": "XLV", "BMY": "XLV",
    # Energy → XLE
    "XOM": "XLE", "CVX": "XLE", "COP": "XLE", "SLB": "XLE", "EOG": "XLE",
    # Industrials → XLI
    "CAT": "XLI", "BA": "XLI", "HON": "XLI", "GE": "XLI", "MMM": "XLI",
}


def sector_etf_for(ticker: str) -> str:
    """Return the sector ETF for a ticker, defaulting to SPY when unknown."""
    return SECTOR_ETF_MAP.get(ticker, "SPY")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
