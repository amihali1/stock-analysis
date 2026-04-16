"""API routes for options chain data."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()


class OptionsChainItem(BaseModel):
    ticker: str
    expiration: str
    strike: float
    option_type: str
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    implied_vol: float


class OptionsChainResponse(BaseModel):
    ticker: str
    expiration: str
    options: list[OptionsChainItem]
    count: int


class ExpirationsResponse(BaseModel):
    ticker: str
    expirations: list[str]


@router.get("/options-chain/{ticker}/expirations", response_model=ExpirationsResponse)
def get_expirations(ticker: str):
    """Get available expiration dates for a ticker."""
    from src.services.options_chain import OptionsChainFetcher

    fetcher = OptionsChainFetcher()
    try:
        expirations = fetcher.get_expirations(ticker.upper())
        return ExpirationsResponse(ticker=ticker.upper(), expirations=expirations)
    finally:
        fetcher.close()


@router.get("/options-chain/{ticker}", response_model=OptionsChainResponse)
def get_options_chain(
    ticker: str,
    expiration: str = Query(..., description="Expiration date in YYYY-MM-DD format"),
):
    """Get options chain for a ticker at a specific expiration."""
    from src.services.options_chain import OptionsChainFetcher

    fetcher = OptionsChainFetcher()
    try:
        chain = fetcher.fetch_chain(ticker.upper(), expiration)
        if not chain:
            raise HTTPException(
                status_code=404,
                detail=f"No options data found for {ticker.upper()} at expiration {expiration}",
            )
        return OptionsChainResponse(
            ticker=ticker.upper(),
            expiration=expiration,
            options=[OptionsChainItem(**row) for row in chain],
            count=len(chain),
        )
    finally:
        fetcher.close()
