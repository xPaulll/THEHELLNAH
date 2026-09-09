from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Optional
from backend.app.core.constants import PositionEventType

class AccountState(BaseModel):
    balance: Decimal
    equity: Decimal
    margin: Decimal
    free_margin: Decimal
    margin_level: Optional[Decimal] = Decimal("0")
    open_positions_count: int = 0

class PositionItem(BaseModel):
    ticket: int
    symbol: str
    type: str  # 'BUY' | 'SELL'
    lots: Decimal
    open_price: Decimal
    open_time_utc: str
    sl: Optional[Decimal] = Decimal("0")
    tp: Optional[Decimal] = Decimal("0")
    current_price: Decimal
    profit: Decimal
    magic_number: Optional[int] = 0
    comment: Optional[str] = None

class AccountSnapshotPayload(BaseModel):
    source_id: str = Field(..., min_length=64, max_length=64)
    payload_version: str = "1.0.0"
    schema_version: str = "1.0.0"
    account: AccountState
    positions: list[PositionItem] = []

class PositionEventItem(BaseModel):
    ticket: int
    event_type: PositionEventType
    symbol: str
    lots: Decimal
    price: Decimal
    sl: Optional[Decimal] = None
    tp: Optional[Decimal] = None
    profit: Optional[Decimal] = Decimal("0")
    event_time_epoch: int
    event_time_utc: str

class PositionEventsPayload(BaseModel):
    source_id: str = Field(..., min_length=64, max_length=64)
    payload_version: str = "1.0.0"
    schema_version: str = "1.0.0"
    events: list[PositionEventItem]
