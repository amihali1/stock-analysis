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
        # ETFs for macro context
        "SPY", "QQQ", "IWM", "XLF", "XLE", "XLK", "XLV",
    ]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
