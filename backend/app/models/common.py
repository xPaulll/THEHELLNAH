from pydantic import BaseModel, Field
from typing import Any, Optional
from datetime import datetime

class SourceIdentity(BaseModel):
    broker: str = Field(..., description="Broker company name from MT5")
    environment: str = Field(..., description="DEMO or REAL")
    account_hash: str = Field(..., description="SHA-256 hash of raw account number")
    ea_identifier: str = Field(..., description="EA Instance Identifier e.g. ALPED_BRIDGE_01")
    ea_version: str = Field(default="3.0.0", description="EA Version")

class HandshakeRequest(BaseModel):
    broker: str
    environment: str
    account_hash: str
    ea_identifier: str
    ea_version: str = "3.0.0"
    payload_version: str = "1.0.0"
    schema_version: str = "1.0.0"

class HandshakeResponse(BaseModel):
    status: str = "SUCCESS"
    source_id: str
    server_time_utc: datetime
    schema_version: str = "1.0.0"

class BaseResponse(BaseModel):
    status: str = "SUCCESS"
    message: Optional[str] = None
    data: Optional[Any] = None
