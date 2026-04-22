import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.api.routes import health, recommendations, analysis, paper_trades, backtest, alerts, watchlist, options, portfolio, execution
from src.auth.routes import router as auth_router, ensure_default_admin
from src.auth.jwt import verify_token
from src.auth.middleware import PUBLIC_PATHS
from src.pipeline.scheduler import init_scheduler, shutdown_scheduler

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """Protect all API routes except public ones."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public paths, OPTIONS (CORS preflight), and non-API paths
        if path in PUBLIC_PATHS or request.method == "OPTIONS" or not path.startswith("/api"):
            return await call_next(request)

        # Check Authorization header
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated"},
            )

        token = auth[7:]
        username = verify_token(token)
        if username is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token"},
            )

        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ensure_default_admin()
    init_scheduler()
    yield
    # Shutdown
    shutdown_scheduler()


app = FastAPI(
    title="Stock Analysis Platform",
    description="ML-powered stock analysis with sentiment analysis",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3100", "http://10.0.0.47:3100"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(AuthMiddleware)

Instrumentator(
    excluded_handlers=["/metrics"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

app.include_router(auth_router, prefix="/api", tags=["auth"])
app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(recommendations.router, prefix="/api", tags=["recommendations"])
app.include_router(analysis.router, prefix="/api", tags=["analysis"])
app.include_router(paper_trades.router, prefix="/api", tags=["paper-trades"])
app.include_router(backtest.router, tags=["backtest"])
app.include_router(alerts.router, prefix="/api", tags=["alerts"])
app.include_router(watchlist.router, prefix="/api", tags=["watchlist"])
app.include_router(options.router, prefix="/api", tags=["options"])
app.include_router(portfolio.router, prefix="/api", tags=["portfolio"])
app.include_router(execution.router, prefix="/api", tags=["execution"])
