from fastapi import APIRouter, Depends, Query
from backend.app.core.security import verify_api_key
from backend.app.core.constants import canonicalize_symbol, StructureBias, StructureTransitionState
from backend.app.models.market_structure import (
    MarketStructureQueryResponse,
    MarketStructureEventResponse,
    MarketStructureStateModel
)
from backend.app.repositories.market_structure_repo import market_structure_repo

router = APIRouter(prefix="/market-structure", tags=["Feature Engine Market Structure"])

@router.get("", response_model=MarketStructureQueryResponse)
def get_market_structure(
    source_id: str = Query(..., min_length=64, max_length=64),
    symbol: str = Query(...),
    timeframe: str = Query(...),
    limit: int = Query(50, ge=1, le=500),
    _: str = Depends(verify_api_key)
) -> MarketStructureQueryResponse:
    """
    Returns stored market structure events and current state for a given symbol and timeframe.
    Read-only endpoint: Feature Engine evaluates and stores market structure events
    chronologically based on confirmed Step 1 market_swings.
    """
    sym_canon = canonicalize_symbol(symbol)
    state_dict = market_structure_repo.get_structure_state(source_id, sym_canon, timeframe)
    raw_events = market_structure_repo.get_structure_events(source_id, sym_canon, timeframe, limit=limit)

    state_model = None
    bias = StructureBias.NEUTRAL.value
    transition_state = StructureTransitionState.NORMAL.value

    if state_dict:
        bias = state_dict.get("bias", StructureBias.NEUTRAL.value)
        transition_state = state_dict.get("transition_state", StructureTransitionState.NORMAL.value)
        state_model = MarketStructureStateModel(
            source_id=state_dict["source_id"],
            symbol=state_dict["symbol"],
            timeframe=state_dict["timeframe"],
            bias=bias,
            transition_state=transition_state,
            last_processed_candle_time_epoch=state_dict.get("last_processed_candle_time_epoch", 0),
            last_processed_confirmation_time=state_dict.get("last_processed_confirmation_time", 0),
            protected_high_candle_time_epoch=state_dict.get("protected_high_candle_time_epoch"),
            protected_high_price=float(state_dict["protected_high_price"]) if state_dict.get("protected_high_price") is not None else None,
            protected_low_candle_time_epoch=state_dict.get("protected_low_candle_time_epoch"),
            protected_low_price=float(state_dict["protected_low_price"]) if state_dict.get("protected_low_price") is not None else None,
            bullish_break_level_candle_time_epoch=state_dict.get("bullish_break_level_candle_time_epoch"),
            bullish_break_level_price=float(state_dict["bullish_break_level_price"]) if state_dict.get("bullish_break_level_price") is not None else None,
            bearish_break_level_candle_time_epoch=state_dict.get("bearish_break_level_candle_time_epoch"),
            bearish_break_level_price=float(state_dict["bearish_break_level_price"]) if state_dict.get("bearish_break_level_price") is not None else None,
            last_broken_high_candle_time_epoch=state_dict.get("last_broken_high_candle_time_epoch"),
            last_broken_low_candle_time_epoch=state_dict.get("last_broken_low_candle_time_epoch"),
            pending_choch_event_id=state_dict.get("pending_choch_event_id"),
            pending_choch_epoch=state_dict.get("pending_choch_epoch"),
            updated_at=str(state_dict.get("updated_at")) if state_dict.get("updated_at") else None
        )

    event_models = [
        MarketStructureEventResponse(
            id=ev.get("id"),
            source_id=ev["source_id"],
            symbol=ev["symbol"],
            timeframe=ev["timeframe"],
            event_type=ev["event_type"],
            event_candle_time_epoch=int(ev["event_candle_time_epoch"]),
            broken_swing_candle_time_epoch=ev.get("broken_swing_candle_time_epoch"),
            broken_swing_type=ev.get("broken_swing_type"),
            broken_swing_price=float(ev["broken_swing_price"]) if ev.get("broken_swing_price") is not None else None,
            broken_high_swing_candle_time_epoch=ev.get("broken_high_swing_candle_time_epoch"),
            broken_high_swing_price=float(ev["broken_high_swing_price"]) if ev.get("broken_high_swing_price") is not None else None,
            broken_low_swing_candle_time_epoch=ev.get("broken_low_swing_candle_time_epoch"),
            broken_low_swing_price=float(ev["broken_low_swing_price"]) if ev.get("broken_low_swing_price") is not None else None,
            candle_close=float(ev["candle_close"]),
            break_threshold=float(ev.get("break_threshold", 0.0)),
            previous_bias=ev["previous_bias"],
            resulting_bias=ev["resulting_bias"],
            transition_state=ev["transition_state"],
            fractal_n=int(ev.get("fractal_n", 2)),
            decision_context=ev.get("decision_context", {}),
            created_at=str(ev.get("created_at")) if ev.get("created_at") else None
        )
        for ev in raw_events
    ]

    return MarketStructureQueryResponse(
        status="SUCCESS",
        source_id=source_id,
        symbol=sym_canon,
        timeframe=timeframe,
        bias=bias,
        transition_state=transition_state,
        state=state_model,
        events=event_models,
        total_events=len(event_models)
    )
