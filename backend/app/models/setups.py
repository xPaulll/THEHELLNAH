from pydantic import BaseModel, Field
from typing import Any, Optional

class SetupRecord(BaseModel):
    setup_id: str = Field(..., description="Unique setup ID e.g. SETUP-20260909-EURUSD-0001")
    source_id: str
    symbol: str
    strategy: str  # INTRADAY | SILVER_BULLET
    direction: str  # BUY | SELL
    setup_time_utc: str
    market_state: Optional[str] = "EXPANDING"  # TRENDING, EXPANDING, RANGING, etc.
    evidence: dict[str, Any]
    status: str = "DETECTED"  # DETECTED, QUALIFIED, INVALIDATED
