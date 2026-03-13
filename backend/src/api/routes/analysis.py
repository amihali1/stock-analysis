from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.db.session import get_db

router = APIRouter()


@router.get("/analysis/{ticker}")
def get_analysis(ticker: str, db: Session = Depends(get_db)):
    """Get full analysis for a single ticker."""
    # TODO: Implement in P3-001
    return {
        "ticker": ticker.upper(),
        "message": "Not yet implemented — see ticket P3-001",
    }
