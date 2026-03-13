import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import health, recommendations, analysis
from src.pipeline.scheduler import init_scheduler, shutdown_scheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(recommendations.router, prefix="/api", tags=["recommendations"])
app.include_router(analysis.router, prefix="/api", tags=["analysis"])
