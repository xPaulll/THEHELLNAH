from typing import Optional, Any
from pydantic import BaseModel, Field
from backend.app.core.constants import StructureBias, StructureTransitionState, StructureEventType

class MarketStructureEventCreate(BaseModel):
    source_id: str
    symbol: str
    timeframe: str
    event_type: StructureEventType
    event_candle_time_epoch: int
    broken_swing_candle_time_epoch: Optional[int] = None
    broken_swing_type: Optional[str] = None
    broken_swing_price: Optional[float] = None
    broken_high_swing_candle_time_epoch: Optional[int] = None
    broken_high_swing_price: Optional[float] = None
    broken_low_swing_candle_time_epoch: Optional[int] = None
    broken_low_swing_price: Optional[float] = None
    candle_close: float
    break_threshold: float = 0.0
    previous_bias: StructureBias
    resulting_bias: StructureBias
    transition_state: StructureTransitionState
    fractal_n: int = 2
    decision_context: dict[str, Any] = Field(default_factory=dict)

class MarketStructureEventResponse(BaseModel):
    id: Optional[int] = None
    source_id: str
    symbol: str
    timeframe: str
    event_type: str
    event_candle_time_epoch: int
    broken_swing_candle_time_epoch: Optional[int] = None
    broken_swing_type: Optional[str] = None
    broken_swing_price: Optional[float] = None
    broken_high_swing_candle_time_epoch: Optional[int] = None
    broken_high_swing_price: Optional[float] = None
    broken_low_swing_candle_time_epoch: Optional[int] = None
    broken_low_swing_price: Optional[float] = None
    candle_close: float
    break_threshold: float
    previous_bias: str
    resulting_bias: str
    transition_state: str
    fractal_n: int
    decision_context: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None

class MarketStructureStateModel(BaseModel):
    source_id: str
    symbol: str
    timeframe: str
    bias: str
    transition_state: str
    last_processed_candle_time_epoch: int
    last_processed_confirmation_time: int
    protected_high_candle_time_epoch: Optional[int] = None
    protected_high_price: Optional[float] = None
    protected_low_candle_time_epoch: Optional[int] = None
    protected_low_price: Optional[float] = None
    bullish_break_level_candle_time_epoch: Optional[int] = None
    bullish_break_level_price: Optional[float] = None
    bearish_break_level_candle_time_epoch: Optional[int] = None
    bearish_break_level_price: Optional[float] = None
    last_broken_high_candle_time_epoch: Optional[int] = None
    last_broken_low_candle_time_epoch: Optional[int] = None
    pending_choch_event_id: Optional[int] = None
    pending_choch_epoch: Optional[int] = None
    updated_at: Optional[str] = None

class MarketStructureQueryResponse(BaseModel):
    status: str = "SUCCESS"
    source_id: str
    symbol: str
    timeframe: str
    bias: str
    transition_state: str
    state: Optional[MarketStructureStateModel] = None
    events: list[MarketStructureEventResponse] = Field(default_factory=list)
    total_events: int
