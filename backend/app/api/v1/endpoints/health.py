from fastapi import APIRouter
from datetime import datetime, timezone
from backend.app.core.config import settings

router = APIRouter(prefix="/health", tags=["Health & Status"])

@router.get("")
def health_check() -> dict:
    """
    Returns pipeline health, server time, and environment configurations.
    """
    return {
        "status": "HEALTHY",
        "system": "Alped Punya V3 — Data Infrastructure",
        "server_time_utc": datetime.now(timezone.utc).isoformat(),
        "environment": settings.ENVIRONMENT,
        "schema_version": settings.SCHEMA_VERSION,
        "strategy_mode": settings.STRATEGY_MODE,
        "allow_execution": settings.ALLOW_EXECUTION
    }
