from fastapi import APIRouter, Depends
from datetime import datetime, timezone
from backend.app.models.common import HandshakeRequest, HandshakeResponse
from backend.app.core.security import generate_source_id, verify_api_key
from backend.app.repositories.source_repo import source_repo
from backend.app.core.config import settings

router = APIRouter(prefix="/auth", tags=["Authentication & Handshake"])

@router.post("/handshake", response_model=HandshakeResponse)
def handshake(
    payload: HandshakeRequest,
    _: str = Depends(verify_api_key)
) -> HandshakeResponse:
    """
    Authoritative source identity registration.
    Backend normalizes fields and generates a 64-character SHA-256 source_id.
    """
    source_id = generate_source_id(
        broker=payload.broker,
        environment=payload.environment,
        account_hash=payload.account_hash,
        ea_identifier=payload.ea_identifier
    )

    now_utc = datetime.now(timezone.utc)

    # Register in database
    source_record = {
        "source_id": source_id,
        "broker": payload.broker.strip().upper(),
        "environment": payload.environment.strip().upper(),
        "account_hash": payload.account_hash.strip().lower(),
        "ea_identifier": payload.ea_identifier.strip().upper(),
        "ea_version": payload.ea_version,
        "payload_version": payload.payload_version,
        "schema_version": payload.schema_version,
        "is_active": True,
        "last_heartbeat_at": now_utc.isoformat()
    }
    source_repo.upsert_source(source_record)

    return HandshakeResponse(
        status="SUCCESS",
        source_id=source_id,
        server_time_utc=now_utc,
        schema_version=settings.SCHEMA_VERSION
    )
