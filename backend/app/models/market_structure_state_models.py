from typing import Optional, Any
from pydantic import BaseModel, Field
from backend.app.core.constants import (
    MarketState,
    StructureStrength,
    StructureConfiguration,
    CanonicalStructureEventType
)


class CanonicalStructureEvent(BaseModel):
    """
    Canonical normalized structure event consumed by Step 3 State Engine.
    Carries discrete structural events from Step 1 swings and Step 2 break/shift events.
    """
    source_id: str = Field(..., min_length=64, max_length=64)
    symbol: str
    timeframe: str
    event_type: CanonicalStructureEventType
    event_time: int
    event_price: float
    bar_time: int
    bar_index: int = Field(..., description="Monotonic bar index; 0 indicates forming/unclosed Bar 0")
    source_event_id: Optional[int] = None
    is_closed_bar: bool = Field(True, description="Strictly True for persistent state evaluation; False rejects mutation")
    event_key: str = Field(..., description="Durable unique deterministic identity for idempotency")
    metadata: dict[str, Any] = Field(default_factory=dict)


class MarketStructureCurrentStateResponse(BaseModel):
    """
    Dashboard / API contract for current market structure state snapshot.
    """
    symbol: str
    timeframe: str
    state: MarketState
    previous_state: MarketState
    structure: StructureConfiguration
    last_event: str
    last_event_time: int
    last_event_price: float
    structure_strength: StructureStrength
    state_changed: bool
    state_reason: str
    bar_time: int
    bar_index: int
    last_update: Optional[str] = None


class MarketStructureHistoryItemResponse(BaseModel):
    """
    Audit trail history item representing an actual state transition.
    """
    id: Optional[int] = None
    symbol: str
    timeframe: str
    previous_state: MarketState
    new_state: MarketState
    structure: StructureConfiguration
    last_event: str
    event_time: int
    event_price: float
    state_reason: str
    structure_strength: StructureStrength
    source_event_id: Optional[int] = None
    bar_time: int
    bar_index: int
    created_at: Optional[str] = None


class MarketStructureHistoryListResponse(BaseModel):
    status: str = "SUCCESS"
    source_id: str
    symbol: str
    timeframe: str
    total: int
    history: list[MarketStructureHistoryItemResponse]
