from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, timezone
from backend.app.models.account import AccountSnapshotPayload, PositionEventsPayload
from backend.app.models.common import BaseResponse
from backend.app.core.security import verify_api_key
from backend.app.repositories.account_repo import account_repo
from backend.app.repositories.source_repo import source_repo
from backend.app.services.timezone_service import generate_canonical_utc

router = APIRouter(prefix="", tags=["Account & Positions"])

@router.post("/account/snapshot", response_model=BaseResponse)
def record_account_snapshot(
    payload: AccountSnapshotPayload,
    _: str = Depends(verify_api_key)
) -> BaseResponse:
    """
    Ingests periodic account balance, equity, margin, and current positions snapshot.
    """
    src = source_repo.get_source(payload.source_id)
    if not src:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source ID '{payload.source_id}' not found."
        )

    source_repo.update_heartbeat(payload.source_id)
    now_utc = datetime.now(timezone.utc).isoformat()

    # 1. Snapshot
    snapshot_row = {
        "source_id": payload.source_id,
        "balance": float(payload.account.balance),
        "equity": float(payload.account.equity),
        "margin": float(payload.account.margin),
        "free_margin": float(payload.account.free_margin),
        "margin_level": float(payload.account.margin_level or 0),
        "open_positions_count": payload.account.open_positions_count,
        "snapshot_time_utc": now_utc,
        "schema_version": payload.schema_version
    }
    account_repo.record_snapshot(snapshot_row)

    # 2. Reconcile Open Positions
    pos_rows = []
    for p in payload.positions:
        pos_rows.append({
            "source_id": payload.source_id,
            "ticket": p.ticket,
            "symbol": p.symbol.upper(),
            "type": p.type.upper(),
            "lots": float(p.lots),
            "open_price": float(p.open_price),
            "open_time_utc": p.open_time_utc,
            "sl": float(p.sl or 0),
            "tp": float(p.tp or 0),
            "current_price": float(p.current_price),
            "profit": float(p.profit),
            "magic_number": p.magic_number or 0,
            "comment": p.comment,
            "updated_at": now_utc
        })
    account_repo.upsert_positions(pos_rows)

    from backend.app.services.event_logger import event_logger
    event_logger.record_event(
        "INFO",
        "MT5",
        f"Account telemetry synced: Balance=${payload.account.balance:.2f}, Equity=${payload.account.equity:.2f}, Positions={len(pos_rows)}"
    )

    return BaseResponse(
        status="SUCCESS",
        message=f"Snapshot recorded. Reconciled {len(pos_rows)} positions."
    )

@router.post("/positions/events", response_model=BaseResponse)
def record_position_events(
    payload: PositionEventsPayload,
    _: str = Depends(verify_api_key)
) -> BaseResponse:
    """
    Ingests immutable position lifecycle events from OnTradeTransaction().
    """
    src = source_repo.get_source(payload.source_id)
    if not src:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source ID '{payload.source_id}' not found."
        )

    event_rows = []
    for ev in payload.events:
        canonical_utc = generate_canonical_utc(ev.event_time_epoch)
        event_rows.append({
            "source_id": payload.source_id,
            "ticket": ev.ticket,
            "event_type": ev.event_type.value,
            "symbol": ev.symbol.upper(),
            "lots": float(ev.lots),
            "price": float(ev.price),
            "sl": float(ev.sl) if ev.sl is not None else None,
            "tp": float(ev.tp) if ev.tp is not None else None,
            "profit": float(ev.profit or 0),
            "event_time_utc": canonical_utc.isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat()
        })

    count = account_repo.record_position_events(event_rows)
    return BaseResponse(
        status="SUCCESS",
        message=f"Logged {count} position events."
    )
