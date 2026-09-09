from fastapi import APIRouter, Depends, HTTPException, status
from backend.app.models.market_data import TickBatchPayload
from backend.app.models.common import BaseResponse
from backend.app.core.security import verify_api_key
from backend.app.repositories.tick_repo import tick_repo
from backend.app.repositories.source_repo import source_repo
from backend.app.services.timezone_service import generate_canonical_utc

router = APIRouter(prefix="/ticks", tags=["Market Ticks"])

@router.post("", response_model=BaseResponse)
def ingest_ticks(
    payload: TickBatchPayload,
    _: str = Depends(verify_api_key)
) -> BaseResponse:
    """
    Ingests configurable tick stream (Default: TICK_OFF).
    """
    src = source_repo.get_source(payload.source_id)
    if not src:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source ID '{payload.source_id}' not found."
        )

    rows = []
    for t in payload.ticks:
        canonical_utc = generate_canonical_utc(t.tick_time_epoch)
        rows.append({
            "source_id": payload.source_id,
            "symbol": payload.symbol.upper(),
            "tick_time_utc": canonical_utc.isoformat(),
            "tick_time_epoch": t.tick_time_epoch,
            "bid": float(t.bid),
            "ask": float(t.ask),
            "last": float(t.last or 0),
            "volume": t.volume or 0,
            "broker_time": t.broker_time
        })

    count = tick_repo.insert_ticks(rows)
    return BaseResponse(
        status="SUCCESS",
        message=f"Ingested {count} ticks for {payload.symbol}"
    )
