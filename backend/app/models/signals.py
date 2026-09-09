from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Any, Optional

class SignalEntryZone(BaseModel):
    zone_low: Decimal
    zone_high: Decimal

class DeepEvidenceSignal(BaseModel):
    signal_id: str = Field(..., description="Unique Signal ID e.g. SIG-20260909-000001")
    setup_id: Optional[str] = None
    symbol: str
    strategy: str  # INTRADAY | SILVER_BULLET
    direction: str  # BUY | SELL
    session: Optional[str] = None
    market_state: Optional[str] = None
    context: dict[str, Any]
    evidence: dict[str, Any]
    entry: SignalEntryZone
    stop_loss: Decimal
    take_profit: Decimal
    risk_reward: Decimal
    invalidation: str
    status: str = "WAITING"  # WAITING, APPROVED, REJECTED, EXPIRED
    allow_execution: bool = False
