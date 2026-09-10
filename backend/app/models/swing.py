from typing import Optional
from pydantic import BaseModel, Field
from backend.app.core.constants import SwingType, SwingClassification

class SwingPointRecord(BaseModel):
    id: Optional[int] = None
    source_id: str
    symbol: str
    timeframe: str
    swing_type: SwingType
    swing_candle_time_epoch: int
    swing_price: float
    confirmed_at_candle_time_epoch: int
    classification: Optional[SwingClassification] = None
    fractal_n: int = 2
    created_at: Optional[str] = None

class SwingResponse(BaseModel):
    status: str = "SUCCESS"
    symbol: str
    timeframe: str
    total_count: int
    swings: list[SwingPointRecord]

class SwingQueryFilter(BaseModel):
    source_id: str
    symbol: str
    timeframe: str
    limit: int = Field(default=50, ge=1, le=500)
