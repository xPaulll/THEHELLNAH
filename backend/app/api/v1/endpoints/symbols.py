from fastapi import APIRouter, Depends, HTTPException, status
from backend.app.models.symbol import SymbolMetadata
from backend.app.models.common import BaseResponse
from backend.app.core.security import verify_api_key
from backend.app.repositories.symbol_repo import symbol_repo
from backend.app.repositories.source_repo import source_repo

router = APIRouter(prefix="/symbols", tags=["Symbols"])

@router.post("", response_model=BaseResponse)
def register_symbol(
    payload: SymbolMetadata,
    _: str = Depends(verify_api_key)
) -> BaseResponse:
    """
    Registers or updates symbol specifications for a specific source_id.
    """
    # Verify source_id exists
    src = source_repo.get_source(payload.source_id)
    if not src:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source ID '{payload.source_id}' not found. Perform handshake first."
        )

    row = {
        "source_id": payload.source_id,
        "symbol": payload.symbol.upper(),
        "digits": payload.digits,
        "point": float(payload.point),
        "contract_size": float(payload.contract_size),
        "volume_min": float(payload.volume_min),
        "volume_max": float(payload.volume_max),
        "volume_step": float(payload.volume_step),
        "stop_level": payload.stop_level,
        "trade_mode": payload.trade_mode,
        "currency_base": payload.currency_base.upper(),
        "currency_profit": payload.currency_profit.upper(),
        "schema_version": payload.schema_version
    }

    symbol_repo.upsert_symbol(row)
    return BaseResponse(
        status="SUCCESS",
        message=f"Symbol {payload.symbol} registered for source {payload.source_id[:8]}..."
    )
