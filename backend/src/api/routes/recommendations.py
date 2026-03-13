from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from src.db.session import get_db

router = APIRouter()


@router.get("/recommendations")
def get_recommendations(
    strategy: Optional[str] = Query(None, regex="^(short|options)$"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Get top recommendations, optionally filtered by strategy."""
    # TODO: Implement in P3-001
    return {
        "recommendations": [],
        "message": "Not yet implemented — see ticket P3-001",
    }
