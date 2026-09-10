import time
import logging
from fastapi import APIRouter, Depends, Query, HTTPException, status, BackgroundTasks
from backend.app.models.market_data import BatchCandlePayload, LiveCandlePayload, SyncStatusResponse
from backend.app.core.security import verify_api_key
from backend.app.services.sync_orchestrator import sync_orchestrator
from backend.app.repositories.source_repo import source_repo
from backend.app.core.constants import Timeframe, SyncType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/candles", tags=["Market Candles"])

@router.get("/sync-status", response_model=SyncStatusResponse)
def get_sync_status(
    source_id: str = Query(..., min_length=64, max_length=64),
    symbol: str = Query(...),
    timeframe: Timeframe = Query(...),
    _: str = Depends(verify_api_key)
) -> SyncStatusResponse:
    """
    Returns latest stored candle timestamp to help EA identify missing range for RECOVERY_SYNC.
    Also returns status='REBUILDING' if background feature engine is currently running.
    """
    src = source_repo.get_source(source_id)
    if not src:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source ID '{source_id}' not found. Please handshake first."
        )

    return sync_orchestrator.get_sync_status(source_id, symbol, timeframe)

@router.post("/batch")
def ingest_candle_batch(
    payload: BatchCandlePayload,
    background_tasks: BackgroundTasks,
    _: str = Depends(verify_api_key)
) -> dict:
    """
    Ingests a batch of candles for INITIAL_SYNC, RECOVERY_SYNC, GAP_BACKFILL, or live flushing.
    Validates timestamps, sanity, classifies gaps, and upserts idempotently.
    Dispatches feature engine rebuild via BackgroundTasks to eliminate application-induced HTTP blocking.
    """
    t_start = time.perf_counter()
    src = source_repo.get_source(payload.source_id)
    if not src:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source ID '{payload.source_id}' not registered. Please handshake first."
        )

    source_repo.update_heartbeat(payload.source_id)

    # 1. Ingest candles into database synchronously (without blocking on feature engine rebuild)
    res = sync_orchestrator.process_candle_batch(payload, run_features=False)

    # 2. Schedule Feature Engine execution in background
    background_tasks.add_task(
        sync_orchestrator.run_feature_engine,
        source_id=payload.source_id,
        symbol=payload.symbol,
        timeframe_str=payload.timeframe.value,
        sync_type=payload.sync_type
    )

    from backend.app.services.event_logger import event_logger
    event_logger.record_event(
        "INFO",
        "MT5",
        f"Received {len(payload.candles)} {payload.timeframe.value} candles for {payload.symbol} ({payload.sync_type.value})"
    )

    # Broadcast CANDLE_CLOSED for live chart transition
    try:
        from backend.app.services.websocket_manager import ws_manager
        for c in payload.candles:
            ws_manager.emit_candle_closed({
                "symbol": payload.symbol,
                "timeframe": payload.timeframe.value,
                "candle": c.model_dump()
            })
    except Exception:
        pass

    t_resp = time.perf_counter()
    logger.info(
        f"[Diagnostic] HTTP 200 response sent for {payload.symbol} ({payload.timeframe.value}) "
        f"in {(t_resp - t_start)*1000:.1f}ms"
    )

    return res

@router.post("/live")
def ingest_live_candle(
    payload: LiveCandlePayload,
    background_tasks: BackgroundTasks,
    _: str = Depends(verify_api_key)
) -> dict:
    """
    Ingests a single closed candle from the live event stream.
    Dispatches feature engine processing via BackgroundTasks.
    """
    src = source_repo.get_source(payload.source_id)
    if not src:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source ID '{payload.source_id}' not registered. Please handshake first."
        )

    source_repo.update_heartbeat(payload.source_id)
    res = sync_orchestrator.process_live_candle(payload, run_features=False)
    background_tasks.add_task(
        sync_orchestrator.run_feature_engine,
        source_id=payload.source_id,
        symbol=payload.symbol,
        timeframe_str=payload.timeframe.value,
        sync_type=SyncType.LIVE_SYNC
    )
    return res
