from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Optional
from backend.app.core.constants import Timeframe, SyncType

class CandleItem(BaseModel):
    candle_time_epoch: int = Field(..., description="Authoritative Unix timestamp in seconds from MT5")
    candle_time_utc: str = Field(..., description="ISO-8601 UTC timestamp string from EA")
    broker_time: str = Field(..., description="Raw broker server time string e.g. 2026-09-09 04:35:00")
    broker_gmt_offset: int = Field(..., description="Broker offset from GMT in seconds at bar time")
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    tick_volume: int
    spread: int

class BatchCandlePayload(BaseModel):
    source_id: str = Field(..., min_length=64, max_length=64)
    symbol: str
    timeframe: Timeframe
    sync_type: SyncType = SyncType.LIVE_SYNC
    payload_version: str = "1.0.0"
    schema_version: str = "1.0.0"
    candles: list[CandleItem]

class LiveCandlePayload(BaseModel):
    source_id: str = Field(..., min_length=64, max_length=64)
    symbol: str
    timeframe: Timeframe
    payload_version: str = "1.0.0"
    schema_version: str = "1.0.0"
    candle: CandleItem

class TickItem(BaseModel):
    tick_time_epoch: int
    tick_time_utc: str
    broker_time: str
    bid: Decimal
    ask: Decimal
    last: Optional[Decimal] = Decimal("0")
    volume: Optional[int] = 0

class TickBatchPayload(BaseModel):
    source_id: str = Field(..., min_length=64, max_length=64)
    symbol: str
    payload_version: str = "1.0.0"
    schema_version: str = "1.0.0"
    ticks: list[TickItem]

class SyncStatusResponse(BaseModel):
    source_id: str
    symbol: str
    timeframe: Timeframe
    latest_candle_time_utc: Optional[str] = None
    latest_candle_epoch: Optional[int] = None
    status: str = "SYNCED"
