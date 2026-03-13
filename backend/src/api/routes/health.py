from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
import httpx

from src.db.session import get_db
from src.config import get_settings

router = APIRouter()


@router.get("/health")
async def health_check(db: Session = Depends(get_db)):
    settings = get_settings()
    status = {"status": "ok", "database": "unknown", "ollama": "unknown"}

    # Check database
    try:
        db.execute(text("SELECT 1"))
        status["database"] = "connected"
    except Exception as e:
        status["database"] = f"error: {str(e)}"
        status["status"] = "degraded"

    # Check Ollama
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            if resp.status_code == 200:
                status["ollama"] = "connected"
                status["ollama_models"] = [m["name"] for m in resp.json().get("models", [])]
            else:
                status["ollama"] = f"error: HTTP {resp.status_code}"
                status["status"] = "degraded"
    except Exception as e:
        status["ollama"] = f"unreachable: {str(e)}"
        status["status"] = "degraded"

    return status
