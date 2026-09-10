from fastapi import APIRouter, Depends, Query
from backend.app.core.security import verify_api_key
from backend.app.models.swing import SwingResponse, SwingPointRecord
from backend.app.repositories.swing_repo import swing_repo

router = APIRouter(prefix="/swings", tags=["Feature Engine Swings"])

@router.get("", response_model=SwingResponse)
def get_market_swings(
    source_id: str = Query(..., min_length=64, max_length=64),
    symbol: str = Query(...),
    timeframe: str = Query(...),
    limit: int = Query(50, ge=1, le=500),
    _: str = Depends(verify_api_key)
) -> SwingResponse:
    """
    Returns stored market swing points for a given symbol and timeframe.
    Read-only query: Feature Engine detects and stores swings asynchronously during candle ingestion.
    """
    raw_swings = swing_repo.get_swings(
        source_id=source_id,
        symbol=symbol,
        timeframe=timeframe,
        limit=limit
    )

    records = [
        SwingPointRecord(
            id=s.get("id"),
            source_id=s["source_id"],
            symbol=s["symbol"],
            timeframe=s["timeframe"],
            swing_type=s["swing_type"],
            swing_candle_time_epoch=s["swing_candle_time_epoch"],
            swing_price=float(s["swing_price"]),
            confirmed_at_candle_time_epoch=s["confirmed_at_candle_time_epoch"],
            classification=s.get("classification"),
            fractal_n=s.get("fractal_n", 2),
            created_at=str(s.get("created_at")) if s.get("created_at") else None
        )
        for s in raw_swings
    ]

    return SwingResponse(
        status="SUCCESS",
        symbol=symbol,
        timeframe=timeframe,
        total_count=len(records),
        swings=records
    )
