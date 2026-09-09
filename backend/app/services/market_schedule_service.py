from datetime import datetime, date, time
from zoneinfo import ZoneInfo
from typing import Optional
from backend.app.core.constants import GapStatus
from backend.app.models.schedule import MarketScheduleConfig, TimeWindow

# Major Forex / Global Holidays
STANDARD_HOLIDAYS: set[date] = {
    date(2026, 1, 1),   # New Year's Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 12, 25), # Christmas Day
}

class MarketScheduleResolver:
    """
    Evaluates missing candle time intervals against market schedules.
    Prevents false-alarm data loss flags during weekends, holidays, and maintenance.
    """

    def __init__(self, schedules: Optional[dict[str, MarketScheduleConfig]] = None):
        self._schedules = schedules or {}

    def get_schedule(self, source_id: str, symbol: str) -> MarketScheduleConfig:
        key = f"{source_id}:{symbol}"
        if key in self._schedules:
            return self._schedules[key]

        # Default Forex 24/5 schedule (America/New_York)
        return MarketScheduleConfig(
            source_id=source_id,
            symbol=symbol,
            tz_name="America/New_York",
            trading_days=[0, 1, 2, 3, 4],  # Monday through Friday
            daily_open=time(17, 0),        # 17:00 Sunday NY open
            daily_close=time(17, 0),       # 17:00 Friday NY close
            maintenance_windows=[TimeWindow(start_time=time(17, 0), end_time=time(17, 10))],
            holiday_calendar=list(STANDARD_HOLIDAYS)
        )

    def classify_gap(
        self,
        gap_start_utc: datetime,
        gap_end_utc: datetime,
        source_id: str,
        symbol: str
    ) -> tuple[GapStatus, str]:
        """
        Classifies whether an interval gap is a legitimate market closure or actual MISSING_DATA.
        Returns (GapStatus, reason_str).
        """
        schedule = self.get_schedule(source_id, symbol)
        tz = ZoneInfo(schedule.tz_name)

        start_local = gap_start_utc.astimezone(tz)
        end_local = gap_end_utc.astimezone(tz)

        # 1. Holiday Check
        if start_local.date() in schedule.holiday_calendar:
            return (GapStatus.HOLIDAY, f"Market holiday on {start_local.date()}")

        # 2. Weekend Check
        # Friday after close (17:00 NY) until Sunday open (17:00 NY)
        if start_local.weekday() == 4 and start_local.time() >= schedule.daily_close:
            return (GapStatus.WEEKEND, "Friday market close until weekend")
        if start_local.weekday() == 5:
            return (GapStatus.WEEKEND, "Saturday weekend closure")
        if start_local.weekday() == 6 and start_local.time() < schedule.daily_open:
            return (GapStatus.WEEKEND, "Sunday pre-market open closure")

        # 3. Scheduled Daily Maintenance / Rollover Window (e.g. 17:00 - 17:10 NY)
        for window in schedule.maintenance_windows:
            if window.start_time <= start_local.time() <= window.end_time:
                return (GapStatus.BROKER_MAINTENANCE, f"Scheduled rollover maintenance {window.start_time}-{window.end_time}")

        # 4. Off-Day Check
        if start_local.weekday() not in schedule.trading_days:
            return (GapStatus.MARKET_CLOSED, f"Day of week {start_local.weekday()} is not a configured trading day")

        # 5. None of the above -> Actual Data Gap!
        return (GapStatus.MISSING_DATA, "Unscheduled timeline delta detected during active market hours")

schedule_resolver = MarketScheduleResolver()
