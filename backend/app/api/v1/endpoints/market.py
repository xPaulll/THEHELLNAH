import json
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, HTTPException, status
from datetime import datetime, timezone
from backend.app.models.live_market import LiveMarketPayload
from backend.app.core.security import verify_api_key
from backend.app.repositories.source_repo import source_repo
from backend.app.services.live_market_manager import live_market_manager
from backend.app.services.websocket_manager import ws_manager

router = APIRouter(tags=["Live Market Layer"])

@router.post("/api/v1/market/live-bar")
async def ingest_live_bar(
    payload: LiveMarketPayload,
    _: str = Depends(verify_api_key)
) -> dict:
    """
    Ingests live tick & Bar 0 forming candle from MT5.
    Pure In-Memory operation — ZERO writes to Supabase.
    """
    src = source_repo.get_source(payload.source_id)
    if not src:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source ID '{payload.source_id}' not registered. Please handshake first."
        )

    # 1. Update source heartbeat
    source_repo.update_heartbeat(payload.source_id)

    # 2. Update In-Memory Live Market State (with deduplication)
    is_changed = live_market_manager.update_live_market(payload)

    m1_bar = payload.timeframes.get("M1")
    m1_close = m1_bar.close if m1_bar else None
    
    import logging
    logger = logging.getLogger("alped.market")
    logger.info(
        f"[LIVE_BAR] Received packet from MT5: {payload.symbol} | "
        f"Bid={payload.tick.bid} Ask={payload.tick.ask} Spread={payload.tick.spread} | "
        f"M1_Close={m1_close} | Changed={is_changed}"
    )

    # 3. If market state changed, queue throttled WebSocket broadcast
    if is_changed:
        snapshot = live_market_manager.get_snapshot()
        await ws_manager.queue_market_update(snapshot)

    return {
        "status": "SUCCESS",
        "symbol": payload.symbol,
        "is_changed": is_changed
    }

@router.get("/api/v1/market/live-state")
def get_live_market_state() -> dict:
    """
    REST snapshot fallback for live market state.
    """
    return live_market_manager.get_snapshot()

@router.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    """
    Realtime WebSocket stream for Web Dashboard.
    Streams LIVE_MARKET (throttled), CANDLE_CLOSED (instant), and SYSTEM_EVENT (instant).
    Supports PING/PONG for client-side latency measurement.
    """
    await ws_manager.connect(websocket)
    try:
        # Send initial snapshot immediately upon connect
        init_snapshot = live_market_manager.get_snapshot()
        await websocket.send_text(json.dumps({
            "type": "INIT",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": init_snapshot
        }))

        # Message loop for client pings and commands
        while True:
            text = await websocket.receive_text()
            try:
                msg = json.loads(text)
                msg_type = msg.get("type", "").upper()
                if msg_type == "PING":
                    await websocket.send_text(json.dumps({
                        "type": "PONG",
                        "client_time": msg.get("client_time"),
                        "server_time": datetime.now(timezone.utc).isoformat()
                    }))
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)
