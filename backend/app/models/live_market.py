from typing import Optional, Dict
from pydantic import BaseModel, Field
from datetime import datetime

class TickData(BaseModel):
    bid: float = Field(..., description="Current broker bid price")
    ask: float = Field(..., description="Current broker ask price")
    spread: int = Field(..., description="Current broker spread in points")
    tick_time_utc: str = Field(..., description="ISO UTC timestamp of tick")
    tick_time_epoch: Optional[int] = Field(None, description="Epoch timestamp of tick")

class Bar0Data(BaseModel):
    timeframe: str = Field(..., description="Timeframe e.g. M1, M5, H1")
    time_epoch: int = Field(..., description="Bar opening epoch (UTC)")
    time_utc: str = Field(..., description="Bar opening ISO timestamp (UTC)")
    open: float = Field(..., description="Opening price of bar")
    high: float = Field(..., description="Current highest price of forming bar")
    low: float = Field(..., description="Current lowest price of forming bar")
    close: float = Field(..., description="Latest market price (close of forming bar)")
    volume: int = Field(0, description="Current tick volume of forming bar")

class LiveMarketPayload(BaseModel):
    source_id: str = Field(..., min_length=64, max_length=64, description="64-char SHA256 EA Source ID")
    symbol: str = Field(..., description="Market symbol e.g. XAUUSD.vx")
    tick: TickData = Field(..., description="Current live tick metrics")
    timeframes: Dict[str, Bar0Data] = Field(..., description="Forming Bar 0 for each subscribed timeframe")

class WebSocketEnvelope(BaseModel):
    type: str = Field(..., description="Message type: LIVE_MARKET, CANDLE_CLOSED, SYSTEM_EVENT, PONG, INIT")
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    data: dict = Field(..., description="Payload data")
