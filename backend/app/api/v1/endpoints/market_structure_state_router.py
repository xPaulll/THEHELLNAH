from fastapi import APIRouter, Depends, Query, Path
from backend.app.core.security import verify_api_key
from backend.app.core.constants import canonicalize_symbol, MarketState, StructureStrength, StructureConfiguration
from backend.app.models.market_structure_state_models import (
    MarketStructureCurrentStateResponse,
    MarketStructureHistoryItemResponse,
    MarketStructureHistoryListResponse
)
from backend.app.repositories.market_structure_state_repo import market_structure_state_repo
from backend.app.features.market_structure_state_engine import create_initial_market_state

router = APIRouter(prefix="/market-structure-state", tags=["Feature Engine Market Structure State"])


@router.get("/{symbol}/{timeframe}", response_model=MarketStructureCurrentStateResponse)
def get_current_market_structure_state(
    symbol: str = Path(..., description="Symbol instrument (e.g. XAUUSD)"),
    timeframe: str = Path(..., description="Timeframe (e.g. M15)"),
    source_id: str = Query(..., min_length=64, max_length=64, description="Authoritative source ID"),
    _: str = Depends(verify_api_key)
) -> MarketStructureCurrentStateResponse:
    """
    Returns the persistent current market structure regime state (Step 3)
    for a given symbol and timeframe. Read-only dashboard contract.
    """
    sym_canon = canonicalize_symbol(symbol)
    state = market_structure_state_repo.get_current_state(source_id, sym_canon, timeframe)
    if not state:
        state = create_initial_market_state(source_id, sym_canon, timeframe)

    return MarketStructureCurrentStateResponse(
        symbol=sym_canon,
        timeframe=timeframe,
        state=state["state"],
        previous_state=state["previous_state"],
        structure=state["structure"],
        last_event=state["last_event"],
        last_event_time=int(state.get("last_event_time", 0)),
        last_event_price=float(state.get("last_event_price", 0.0)),
        structure_strength=state["structure_strength"],
        state_changed=bool(state.get("state_changed", False)),
        state_reason=state.get("state_reason", "No structural state evaluated"),
        bar_time=int(state.get("bar_time", 0)),
        bar_index=int(state.get("bar_index", 0)),
        last_update=str(state.get("updated_at")) if state.get("updated_at") else None
    )


@router.get("/{symbol}/{timeframe}/history", response_model=MarketStructureHistoryListResponse)
def get_market_structure_state_history(
    symbol: str = Path(..., description="Symbol instrument (e.g. XAUUSD)"),
    timeframe: str = Path(..., description="Timeframe (e.g. M15)"),
    source_id: str = Query(..., min_length=64, max_length=64, description="Authoritative source ID"),
    limit: int = Query(50, ge=1, le=500, description="Max transition records to return"),
    _: str = Depends(verify_api_key)
) -> MarketStructureHistoryListResponse:
    """
    Returns the audit trail of state transitions for a symbol and timeframe.
    Only records where state actually changed are returned.
    """
    sym_canon = canonicalize_symbol(symbol)
    history_records = market_structure_state_repo.get_state_history(
        source_id=source_id,
        symbol=sym_canon,
        timeframe=timeframe,
        limit=limit
    )

    items = [
        MarketStructureHistoryItemResponse(
            id=r.get("id"),
            symbol=sym_canon,
            timeframe=timeframe,
            previous_state=r["previous_state"],
            new_state=r["new_state"],
            structure=r["structure"],
            last_event=r["last_event"],
            event_time=int(r["event_time"]),
            event_price=float(r["event_price"]),
            state_reason=r["state_reason"],
            structure_strength=r["structure_strength"],
            source_event_id=r.get("source_event_id"),
            bar_time=int(r["bar_time"]),
            bar_index=int(r["bar_index"]),
            created_at=str(r.get("created_at")) if r.get("created_at") else None
        )
        for r in history_records
    ]

    return MarketStructureHistoryListResponse(
        status="SUCCESS",
        source_id=source_id,
        symbol=sym_canon,
        timeframe=timeframe,
        total=len(items),
        history=items
    )
