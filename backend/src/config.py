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
    max_position_size: float = 5000.0

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
