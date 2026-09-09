from pydantic import BaseModel, Field
from datetime import time, date
from typing import Optional

class TimeWindow(BaseModel):
    start_time: time
    end_time: time
    description: Optional[str] = "Daily Maintenance / Rollover"

class MarketScheduleConfig(BaseModel):
    source_id: str
    symbol: str
    tz_name: str = "America/New_York"
    trading_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])  # Mon=0, Fri=4
    daily_open: time = time(17, 0)   # 17:00 Sunday NY
    daily_close: time = time(17, 0)  # 17:00 Friday NY
    maintenance_windows: list[TimeWindow] = Field(
        default_factory=lambda: [TimeWindow(start_time=time(17, 0), end_time=time(17, 10))]
    )
    holiday_calendar: list[date] = Field(default_factory=list)
