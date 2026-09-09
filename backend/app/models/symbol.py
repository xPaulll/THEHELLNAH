from pydantic import BaseModel, Field
from decimal import Decimal

class SymbolMetadata(BaseModel):
    source_id: str = Field(..., min_length=64, max_length=64, description="64-char SHA-256 source identity")
    symbol: str
    digits: int
    point: Decimal
    contract_size: Decimal
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal
    stop_level: int
    trade_mode: int
    currency_base: str
    currency_profit: str
    payload_version: str = "1.0.0"
    schema_version: str = "1.0.0"
