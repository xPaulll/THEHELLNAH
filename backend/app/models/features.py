from pydantic import BaseModel
from decimal import Decimal
from typing import Optional

class DisplacementEvidence(BaseModel):
    detected: bool
    timeframe: str
    candle_time_utc: str
    range_multiple: float
    body_ratio: float
    structure_break: bool
    origin_price: Optional[Decimal] = None

class FVGEvidence(BaseModel):
    detected: bool
    timeframe: str
    low: Decimal
    high: Decimal
    size_pips: Decimal
    status: str = "ACTIVE"  # ACTIVE, PARTIALLY_FILLED, FILLED, INVALID

class LiquidityEvidence(BaseModel):
    type: str  # ASIA_LOW, ASIA_HIGH, LONDON_LOW, LONDON_HIGH, PDH, PDL, EQH, EQL
    price_level: Decimal
    swept: bool
    sweep_time_utc: str
    sweep_low: Optional[Decimal] = None
    sweep_high: Optional[Decimal] = None
    reclaimed: bool = False
