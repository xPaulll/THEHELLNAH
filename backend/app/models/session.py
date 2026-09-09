from pydantic import BaseModel, Field
from datetime import time
from typing import Optional

class SessionWindow(BaseModel):
    name: str
    tz_name: str  # e.g. "Europe/London", "America/New_York", "Asia/Tokyo"
    start_time: time
    end_time: time
    enabled: bool = True
